from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from app.ai import factory
from app.ai.errors import AIConfigurationError
from app.ai.gemini_provider import GeminiProvider
from app.core.config import Settings
from tests.ai_fakes import FAKE_KEYS

KEY_VARS = [f"gemini_api_key_{n}" for n in range(1, 5)]


def settings_with(keys: dict[int, str] | None = None, **values: object) -> Settings:
    """Settings isolated from the developer's real .env (which may hold real keys)."""
    for n, var in enumerate(KEY_VARS, start=1):
        values.setdefault(var, (keys or {}).get(n))
    values.setdefault("database_url", "sqlite+aiosqlite:///unused.db")
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def no_cached_provider() -> Iterator[None]:
    factory._provider = None
    yield
    factory._provider = None


def _key_ids(provider: object) -> list[str]:
    assert isinstance(provider, GeminiProvider)
    return [s.key_id for s in provider.key_pool.stats()]


def test_no_keys_is_a_clear_configuration_error() -> None:
    with pytest.raises(AIConfigurationError, match="GEMINI_API_KEY_1"):
        factory.create_ai_provider(settings_with())


def test_blank_keys_are_ignored() -> None:
    with pytest.raises(AIConfigurationError):
        factory.create_ai_provider(settings_with({1: "  ", 2: ""}))


def test_one_key() -> None:
    provider = factory.create_ai_provider(settings_with({1: FAKE_KEYS[0]}))

    assert _key_ids(provider) == ["gemini-key-1"]


def test_two_keys_with_a_gap() -> None:
    provider = factory.create_ai_provider(settings_with({2: FAKE_KEYS[1], 4: FAKE_KEYS[3]}))

    assert _key_ids(provider) == ["gemini-key-2", "gemini-key-4"]


def test_four_keys_and_settings_are_applied() -> None:
    settings = settings_with(
        dict(enumerate(FAKE_KEYS, start=1)),
        gemini_model="gemini-3.1-flash-lite",
        llm_max_retries=2,
    )

    provider = factory.create_ai_provider(settings)

    assert isinstance(provider, GeminiProvider)
    assert _key_ids(provider) == [f"gemini-key-{n}" for n in range(1, 5)]
    assert provider.model == "gemini-3.1-flash-lite"
    assert provider._max_attempts == 3


def test_unsupported_provider_is_a_clear_configuration_error() -> None:
    settings = settings_with({1: FAKE_KEYS[0]}, ai_provider="Ollama")

    with pytest.raises(AIConfigurationError, match="Unsupported AI_PROVIDER 'ollama'"):
        factory.create_ai_provider(settings)


def test_get_ai_provider_is_lazy_and_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "get_settings", lambda: settings_with({1: FAKE_KEYS[0]}))

    assert factory._provider is None
    first = factory.get_ai_provider()

    assert factory.get_ai_provider() is first


async def test_close_ai_provider_resets_the_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "get_settings", lambda: settings_with({1: FAKE_KEYS[0]}))
    factory.get_ai_provider()

    await factory.close_ai_provider()

    assert factory._provider is None


async def test_non_ai_api_works_without_gemini_keys(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even with no keys configured, the provider is never built by the Phase 3 APIs.
    monkeypatch.setattr(factory, "get_settings", lambda: settings_with())

    responses = [
        await client.post("/api/demo/reset"),
        await client.post("/api/incidents/simulate"),
        await client.get("/api/services/payment-api/health"),
    ]

    assert [r.status_code for r in responses] == [200, 201, 200]
    assert factory._provider is None
    await client.post("/api/demo/reset")


def test_default_model_is_gemini_31_flash_lite() -> None:
    settings = settings_with({1: FAKE_KEYS[0]})

    assert settings.gemini_model == "gemini-3.1-flash-lite"
