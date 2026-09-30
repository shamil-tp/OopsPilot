import json
import logging

import httpx
import pytest
from pydantic import BaseModel

from app.ai.base import GenerationOptions
from app.ai.errors import (
    AIAuthenticationError,
    AIConfigurationError,
    AIProviderError,
    AIRateLimitError,
    AIStructuredOutputError,
    AITimeoutError,
)
from app.ai.gemini_provider import GeminiProvider
from app.ai.key_pool import GeminiKeyPool, KeyState
from app.core.logging import JsonFormatter
from tests.ai_fakes import (
    FAKE_KEYS,
    FakeGemini,
    Hang,
    api_error,
    gemini_response,
    invalid_key,
    key_slots,
    rate_limited,
)

DEFAULTS = GenerationOptions(temperature=0.2, max_output_tokens=512, timeout_seconds=5)


class Echo(BaseModel):
    value: str


def make_provider(
    fake: FakeGemini, *, keys: int = 4, max_retries: int = 2, cooldown: float = 60
) -> GeminiProvider:
    return GeminiProvider(
        model="gemini-3.1-flash-lite",
        key_pool=GeminiKeyPool(key_slots(keys), cooldown_seconds=cooldown),
        defaults=DEFAULTS,
        max_retries=max_retries,
        retry_backoff_seconds=0,
        client_factory=fake.client_factory,
    )


def key_state(provider: GeminiProvider, number: int) -> KeyState:
    return provider.key_pool.stats()[number - 1].state


# --- generation ---------------------------------------------------------------------------------


async def test_generate_returns_text_usage_and_uses_configured_model() -> None:
    fake = FakeGemini(default=gemini_response("  OpsPilot Gemini test OK \n"))
    provider = make_provider(fake)

    result = await provider.generate("Reply with exactly: OpsPilot Gemini test OK")

    assert result.text == "OpsPilot Gemini test OK"
    assert (result.provider, result.model, result.finish_reason) == (
        "gemini",
        "gemini-3.1-flash-lite",
        "STOP",
    )
    assert result.usage is not None and result.usage.total_tokens == 17
    call = fake.calls[0]
    assert call.model == "gemini-3.1-flash-lite"
    assert call.config.temperature == 0.2
    assert call.config.max_output_tokens == 512
    assert call.config.response_mime_type is None
    afc = call.config.automatic_function_calling
    assert afc is not None and afc.disable is True


async def test_generation_options_override_defaults() -> None:
    fake = FakeGemini()
    provider = make_provider(fake)

    await provider.generate(
        "hi",
        options=GenerationOptions(
            system_instruction="Be concise.", temperature=0.0, thinking_budget=0
        ),
    )

    config = fake.calls[0].config
    assert config.system_instruction == "Be concise."
    assert config.temperature == 0.0
    assert config.max_output_tokens == 512  # default kept
    assert config.thinking_config is not None and config.thinking_config.thinking_budget == 0


async def test_requests_rotate_keys_and_reuse_clients() -> None:
    fake = FakeGemini()
    provider = make_provider(fake, keys=2)

    for _ in range(4):
        await provider.generate("hi")

    assert fake.keys_used == [1, 2, 1, 2]
    assert fake.clients_created == 2  # one client per key, reused
    await provider.aclose()
    assert fake.clients_closed == 2


async def test_empty_response_is_an_error() -> None:
    fake = FakeGemini(default=gemini_response(None, finish="SAFETY"))

    with pytest.raises(AIProviderError, match="no text .*finish_reason=SAFETY"):
        await make_provider(fake).generate("hi")


# --- fallback and retries -----------------------------------------------------------------------


async def test_rate_limited_key_falls_back_and_cools_down() -> None:
    fake = FakeGemini().on(1, rate_limited(retry_delay="30s"))
    provider = make_provider(fake)

    result = await provider.generate("hi")

    assert result.text == "ok"
    assert fake.keys_used == [1, 2]
    assert key_state(provider, 1) is KeyState.COOLDOWN
    stats = provider.key_pool.stats()[0]
    assert 29 <= stats.cooldown_remaining_seconds <= 30  # Gemini's RetryInfo delay is honoured
    assert stats.rate_limited == 1
    # The cooling key is skipped by the next requests.
    await provider.generate("again")
    assert fake.keys_used == [1, 2, 3]


async def test_timeout_then_server_error_then_success() -> None:
    fake = (
        FakeGemini().on(1, httpx.ReadTimeout("read timed out")).on(2, api_error(503, "UNAVAILABLE"))
    )
    provider = make_provider(fake)

    result = await provider.generate("hi")

    assert result.text == "ok"
    assert fake.keys_used == [1, 2, 3]
    # Transient failures are not the key's fault: no cooldown.
    assert {s.state for s in provider.key_pool.stats()} == {KeyState.AVAILABLE}


async def test_provider_enforces_its_own_timeout() -> None:
    fake = FakeGemini().on(1, Hang(seconds=5))
    provider = make_provider(fake)

    result = await provider.generate("hi", options=GenerationOptions(timeout_seconds=0.05))

    assert result.text == "ok"
    assert fake.keys_used == [1, 2]


async def test_network_error_is_retried() -> None:
    fake = FakeGemini().on(1, httpx.ConnectError("connection refused"))

    result = await make_provider(fake).generate("hi")

    assert result.text == "ok"
    assert fake.keys_used == [1, 2]


async def test_all_keys_failing_raises_clean_error_without_looping() -> None:
    fake = FakeGemini(default=api_error(503, "UNAVAILABLE"))
    provider = make_provider(fake, max_retries=3)

    with pytest.raises(AIProviderError, match=r"failed after 4 attempt\(s\).*503") as info:
        await provider.generate("hi")

    assert fake.keys_used == [1, 2, 3, 4]
    assert info.value.attempts == 4
    assert info.value.status_code == 503


async def test_retries_are_bounded_by_llm_max_retries() -> None:
    fake = FakeGemini(default=api_error(500, "INTERNAL"))

    with pytest.raises(AIProviderError):
        await make_provider(fake, max_retries=2).generate("hi")

    assert len(fake.calls) == 3  # 1 attempt + 2 retries


async def test_single_key_retries_transient_errors_on_same_key() -> None:
    fake = FakeGemini().on(1, api_error(503, "UNAVAILABLE"))

    result = await make_provider(fake, keys=1).generate("hi")

    assert result.text == "ok"
    assert fake.keys_used == [1, 1]


async def test_all_timeouts_raise_timeout_error() -> None:
    fake = FakeGemini(default=httpx.ReadTimeout("slow"))

    with pytest.raises(AITimeoutError, match="failed after 3 attempt"):
        await make_provider(fake).generate("hi")


async def test_every_key_rate_limited_raises_rate_limit_error() -> None:
    fake = FakeGemini(default=rate_limited())
    provider = make_provider(fake, keys=2, max_retries=5)

    with pytest.raises(AIRateLimitError, match="failed after 2 attempt"):
        await provider.generate("hi")

    assert fake.keys_used == [1, 2]  # stops when no key is left, despite retries remaining
    # Next request fails fast without calling Gemini at all.
    with pytest.raises(AIRateLimitError, match="cooling down"):
        await provider.generate("hi")
    assert len(fake.calls) == 2


async def test_invalid_key_is_skipped_and_other_key_used() -> None:
    fake = FakeGemini().on(1, invalid_key())
    provider = make_provider(fake)

    result = await provider.generate("hi")

    assert result.text == "ok"
    assert fake.keys_used == [1, 2]
    assert key_state(provider, 1) is KeyState.COOLDOWN


async def test_all_keys_rejected_raises_authentication_error() -> None:
    fake = FakeGemini(default=api_error(403, "PERMISSION_DENIED"))

    with pytest.raises(AIAuthenticationError):
        await make_provider(fake, keys=2).generate("hi")


# --- non-retryable ------------------------------------------------------------------------------


async def test_invalid_request_is_not_retried() -> None:
    fake = FakeGemini(default=api_error(400, "INVALID_ARGUMENT"))

    with pytest.raises(AIProviderError, match="rejected the request") as info:
        await make_provider(fake).generate("hi")

    assert len(fake.calls) == 1
    assert info.value.status_code == 400
    assert {s.state for s in make_provider(fake).key_pool.stats()} == {KeyState.AVAILABLE}


async def test_unknown_model_is_a_configuration_error() -> None:
    fake = FakeGemini(default=api_error(404, "NOT_FOUND"))

    with pytest.raises(AIConfigurationError, match="gemini-3.1-flash-lite"):
        await make_provider(fake).generate("hi")

    assert len(fake.calls) == 1


async def test_programming_errors_propagate_unchanged() -> None:
    fake = FakeGemini(default=TypeError("bad argument"))

    with pytest.raises(TypeError):
        await make_provider(fake).generate("hi")

    assert len(fake.calls) == 1


def test_provider_requires_at_least_one_key() -> None:
    with pytest.raises(AIConfigurationError, match="no Gemini API key is configured"):
        GeminiProvider(model="m", key_pool=GeminiKeyPool({}), defaults=DEFAULTS)


# --- structured output --------------------------------------------------------------------------


async def test_structured_output_is_validated() -> None:
    fake = FakeGemini(default=gemini_response('{"value": "test"}'))

    result = await make_provider(fake).generate_structured("hi", Echo)

    assert result == Echo(value="test")
    config = fake.calls[0].config
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == Echo.model_json_schema()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"value": "te', "did not match Echo"),  # malformed JSON
        ("not json at all", "did not match Echo"),
        ('{"other": 1}', "did not match Echo: 1 error\\(s\\) at value"),  # schema mismatch
        ('{"value": 42}', "did not match Echo"),  # wrong type
    ],
)
async def test_invalid_structured_output_raises(text: str, message: str) -> None:
    fake = FakeGemini(default=gemini_response(text))

    with pytest.raises(AIStructuredOutputError, match=message):
        await make_provider(fake).generate_structured("hi", Echo)


async def test_truncated_structured_output_raises() -> None:
    fake = FakeGemini(default=gemini_response('{"value": "te', finish="MAX_TOKENS"))

    with pytest.raises(AIStructuredOutputError, match="truncated"):
        await make_provider(fake).generate_structured("hi", Echo)


# --- secret safety ------------------------------------------------------------------------------


async def test_api_keys_never_leak(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = (
        FakeGemini()
        .on(1, rate_limited())
        .on(2, httpx.ReadTimeout("slow"), invalid_key())
        .on(3, api_error(503, "UNAVAILABLE"))
    )
    provider = make_provider(fake, max_retries=3)
    outputs: list[object] = [await provider.generate("hi")]
    fake.default = gemini_response('{"value": "x"}')
    outputs += [
        await provider.generate_structured("hi", Echo),
        provider.key_pool.stats(),
        repr(provider.key_pool.acquire()),
    ]
    for error in (
        api_error(400, "INVALID_ARGUMENT"),
        api_error(404, "NOT_FOUND"),
    ):
        fake.default = error
        with pytest.raises(AIProviderError) as info:
            await provider.generate("hi")
        outputs += [str(info.value), repr(info.value), info.value.__cause__]
    fake.default = gemini_response("nope")
    with pytest.raises(AIStructuredOutputError) as info:
        await provider.generate_structured("hi", Echo)
    outputs.append(str(info.value))

    formatter = JsonFormatter()
    logged = "\n".join(formatter.format(record) for record in caplog.records)
    everything = logged + json.dumps([repr(o) for o in outputs])
    assert "gemini-key-1" in logged  # safe identifiers are logged ...
    assert not any(secret in everything for secret in FAKE_KEYS)  # ... secrets never are
    # The pool holds the secrets, as SecretStr (repr "**********").
    assert not any(secret in repr(provider.key_pool.__dict__) for secret in FAKE_KEYS)
