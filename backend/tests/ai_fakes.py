"""Offline stand-ins for the google-genai client. No test ever calls the real Gemini API.

Responses and errors are real SDK objects (`types.GenerateContentResponse`, `errors.APIError`),
so the provider's parsing and error classification are exercised as in production.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from google.genai import errors, types
from pydantic import SecretStr

FAKE_KEYS = [f"test-key-DO-NOT-USE-{n}" for n in range(1, 5)]


def key_slots(count: int) -> dict[int, SecretStr]:
    return {n: SecretStr(FAKE_KEYS[n - 1]) for n in range(1, count + 1)}


def gemini_response(text: str | None, finish: str = "STOP") -> types.GenerateContentResponse:
    parts = [types.Part(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=types.FinishReason(finish),
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12, candidates_token_count=5, total_token_count=17
        ),
    )


def api_error(code: int, status: str, *, details: Iterable[dict[str, Any]] = ()) -> errors.APIError:
    body = {"error": {"code": code, "status": status, "message": status, "details": list(details)}}
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, body)


def rate_limited(retry_delay: str | None = None) -> errors.APIError:
    details = (
        [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
        if retry_delay
        else []
    )
    return api_error(429, "RESOURCE_EXHAUSTED", details=details)


def invalid_key() -> errors.APIError:
    reason = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}
    return api_error(400, "INVALID_ARGUMENT", details=[reason])


@dataclass
class Hang:
    """Outcome that never answers in time (exercises the provider's own timeout)."""

    seconds: float = 5.0


@dataclass
class Call:
    api_key: str
    model: str
    contents: Any
    config: types.GenerateContentConfig


@dataclass
class FakeGemini:
    """Scripted outcomes per API key; a key with no script left answers `default`."""

    script: dict[str, list[Any]] = field(default_factory=dict)
    default: Any = field(default_factory=lambda: gemini_response("ok"))
    calls: list[Call] = field(default_factory=list)
    clients_created: int = 0
    clients_closed: int = 0

    def on(self, key_number: int, *outcomes: Any) -> "FakeGemini":
        self.script.setdefault(FAKE_KEYS[key_number - 1], []).extend(outcomes)
        return self

    def client_factory(self, api_key: str) -> "_FakeClient":
        self.clients_created += 1
        return _FakeClient(self, api_key)

    @property
    def keys_used(self) -> list[int]:
        return [FAKE_KEYS.index(call.api_key) + 1 for call in self.calls]

    async def _respond(self, api_key: str, **kwargs: Any) -> types.GenerateContentResponse:
        self.calls.append(Call(api_key=api_key, **kwargs))
        queue = self.script.get(api_key) or []
        outcome = queue.pop(0) if queue else self.default
        if isinstance(outcome, Hang):
            await asyncio.sleep(outcome.seconds)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeModels:
    def __init__(self, fake: FakeGemini, api_key: str) -> None:
        self._fake, self._api_key = fake, api_key

    async def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        return await self._fake._respond(self._api_key, **kwargs)


class _FakeAio:
    def __init__(self, fake: FakeGemini, api_key: str) -> None:
        self.models = _FakeModels(fake, api_key)
        self._fake = fake

    async def aclose(self) -> None:
        self._fake.clients_closed += 1


class _FakeClient:
    def __init__(self, fake: FakeGemini, api_key: str) -> None:
        self.aio = _FakeAio(fake, api_key)

    def close(self) -> None:
        pass
