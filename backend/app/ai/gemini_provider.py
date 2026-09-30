"""Gemini implementation of `AIProvider` (google-genai SDK, async client).

Retry model: each attempt takes the next available key from the pool, so a failing key is
followed by a different one. Attempts are bounded by 1 + LLM_MAX_RETRIES. The SDK's own retries
are disabled so they can't multiply requests behind our back.

| Failure                                  | Retry on another key | Key cooldown          |
| ---------------------------------------- | -------------------- | --------------------- |
| 429 rate limit / quota                   | yes                  | yes (Gemini's delay)  |
| invalid / unauthorized key (400/401/403) | yes                  | yes (long)            |
| 408, 5xx, timeout, network error         | yes, after backoff   | no                    |
| 404 model not found                      | no                   | no                    |
| other 4xx (malformed request)            | no                   | no                    |
| anything else (programming errors)       | no: raised as is     | no                    |
"""

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from app.ai.base import AIProvider, AIResponse, GenerationOptions, ModelT, TokenUsage
from app.ai.errors import (
    AIAuthenticationError,
    AIConfigurationError,
    AIProviderError,
    AIRateLimitError,
    AIStructuredOutputError,
    AITimeoutError,
)
from app.ai.key_pool import GeminiKeyPool, PooledKey
from app.core.logging import get_logger

logger = get_logger(__name__)

# A rejected key won't start working in a minute; don't keep trying it.
_AUTH_COOLDOWN_SECONDS = 15 * 60
# Gemini reports an invalid API key as 400 INVALID_ARGUMENT with this ErrorInfo reason.
_INVALID_KEY_REASONS = {"API_KEY_INVALID", "API_KEY_EXPIRED"}
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")

ClientFactory = Callable[[str], Any]


def _default_client_factory(api_key: str) -> genai.Client:
    # retry_options=None means a single attempt per call: GeminiProvider does the retrying.
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(retry_options=None))


@dataclass(frozen=True)
class _Failure:
    error: AIProviderError
    retryable: bool
    cooldown: bool = False
    cooldown_seconds: float | None = None
    backoff: bool = False


def _error_details(exc: genai_errors.APIError) -> list[dict[str, Any]]:
    """The structured `error.details` entries (google.rpc ErrorInfo, RetryInfo, ...)."""
    body = exc.details if isinstance(exc.details, dict) else {}
    details = body.get("error", body).get("details", [])
    return [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []


def _retry_delay_seconds(details: list[dict[str, Any]]) -> float | None:
    for detail in details:
        match = _DURATION.match(str(detail.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        model: str,
        key_pool: GeminiKeyPool,
        defaults: GenerationOptions,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        client_factory: ClientFactory = _default_client_factory,
    ) -> None:
        if len(key_pool) == 0:
            raise AIConfigurationError(
                "AI_PROVIDER=gemini but no Gemini API key is configured. "
                "Set at least one of GEMINI_API_KEY_1 ... GEMINI_API_KEY_4."
            )
        self.model = model
        self.key_pool = key_pool
        self._defaults = defaults
        self._max_attempts = 1 + max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._client_factory = client_factory
        # One reusable client per key (each holds its own connection pool).
        self._clients: dict[str, Any] = {}

    # --- public API ---------------------------------------------------------------------------

    async def generate(
        self, prompt: str, *, options: GenerationOptions | None = None
    ) -> AIResponse:
        merged = self._merge(options)
        response = await self._request(prompt, merged, self._config(merged))
        result = self._to_ai_response(response)
        if not result.text:
            raise AIProviderError(self._empty_response_message(response, result.finish_reason))
        return result

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[ModelT],
        *,
        options: GenerationOptions | None = None,
    ) -> ModelT:
        merged = self._merge(options)
        config = self._config(merged, json_schema=response_model.model_json_schema())
        response = await self._request(prompt, merged, config)
        result = self._to_ai_response(response)
        name = response_model.__name__
        if result.finish_reason == "MAX_TOKENS":
            raise AIStructuredOutputError(
                f"Gemini output for {name} was truncated at max_output_tokens "
                f"({merged.max_output_tokens})"
            )
        if not result.text:
            raise AIStructuredOutputError(
                self._empty_response_message(response, result.finish_reason)
            )
        try:
            return response_model.model_validate_json(result.text)
        except ValidationError as exc:
            problems = exc.errors(include_input=False, include_url=False)
            locations = ", ".join(".".join(map(str, p["loc"])) or p["type"] for p in problems[:5])
            raise AIStructuredOutputError(
                f"Gemini output did not match {name}: {len(problems)} error(s) at {locations}"
            ) from None

    async def aclose(self) -> None:
        clients, self._clients = self._clients, {}
        for client in clients.values():
            await client.aio.aclose()
            client.close()

    # --- request loop ---------------------------------------------------------------------------

    async def _request(
        self, prompt: str, options: GenerationOptions, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        last: _Failure | None = None
        for attempt in range(1, self._max_attempts + 1):
            key = self.key_pool.acquire()
            if key is None:
                if last is not None:
                    raise self._exhausted(last, attempt - 1)
                wait = self.key_pool.next_available_in()
                raise AIRateLimitError(
                    f"All {len(self.key_pool)} Gemini API key(s) are cooling down after rate "
                    f"limits; next one is available in {wait:.0f}s",
                    status_code=429,
                    attempts=0,
                )

            started = time.perf_counter()
            try:
                async with asyncio.timeout(options.timeout_seconds):
                    response = await self._client(key).aio.models.generate_content(
                        model=self.model, contents=prompt, config=config
                    )
            except (genai_errors.APIError, httpx.TransportError, TimeoutError) as exc:
                failure = self._classify(exc, key, options)
                self.key_pool.report_failure(
                    key, cooldown=failure.cooldown, cooldown_seconds=failure.cooldown_seconds
                )
                log_fields = {
                    "provider": self.name,
                    "model": self.model,
                    "key_id": key.key_id,
                    "attempt": attempt,
                    "error": type(failure.error).__name__,
                    "status_code": failure.error.status_code,
                    "detail": str(failure.error),
                }
                if not failure.retryable:
                    logger.error("ai_request_failed", extra=log_fields)
                    raise failure.error from None
                last = failure
                if attempt < self._max_attempts:
                    logger.warning("ai_request_retry", extra=log_fields)
                    if failure.backoff and self._retry_backoff_seconds:
                        await asyncio.sleep(self._retry_backoff_seconds * attempt)
                continue

            self.key_pool.report_success(key)
            usage = response.usage_metadata
            logger.info(
                "ai_request_completed",
                extra={
                    "provider": self.name,
                    "model": self.model,
                    "key_id": key.key_id,
                    "attempt": attempt,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "prompt_tokens": usage.prompt_token_count if usage else None,
                    "output_tokens": usage.candidates_token_count if usage else None,
                    "thinking_tokens": usage.thoughts_token_count if usage else None,
                },
            )
            return response

        assert last is not None  # the loop ran at least once and every path above returned
        raise self._exhausted(last, self._max_attempts)

    def _exhausted(self, last: _Failure, attempts: int) -> AIProviderError:
        error = last.error
        failure = type(error)(
            f"Gemini request failed after {attempts} attempt(s): {error}",
            status_code=error.status_code,
            attempts=attempts,
        )
        logger.error(
            "ai_request_failed",
            extra={
                "provider": self.name,
                "model": self.model,
                "attempts": attempts,
                "error": type(error).__name__,
                "status_code": error.status_code,
            },
        )
        return failure

    def _classify(self, exc: Exception, key: PooledKey, options: GenerationOptions) -> _Failure:
        if isinstance(exc, TimeoutError | httpx.TimeoutException):
            return _Failure(
                AITimeoutError(f"Gemini did not respond within {options.timeout_seconds}s"),
                retryable=True,
                backoff=True,
            )
        if isinstance(exc, httpx.TransportError):
            return _Failure(
                AITimeoutError(f"Could not reach Gemini ({type(exc).__name__})"),
                retryable=True,
                backoff=True,
            )

        assert isinstance(exc, genai_errors.APIError)
        code, status = exc.code, exc.status or ""
        label = f"{code} {status}".strip()
        details = _error_details(exc)
        reasons = {str(d.get("reason")) for d in details if d.get("reason")}

        if code in (401, 403) or reasons & _INVALID_KEY_REASONS:
            return _Failure(
                AIAuthenticationError(f"Gemini rejected {key.key_id} ({label})", status_code=code),
                retryable=True,
                cooldown=True,
                cooldown_seconds=_AUTH_COOLDOWN_SECONDS,
            )
        if code == 429:
            return _Failure(
                AIRateLimitError(f"Gemini rate limit or quota reached ({label})", status_code=code),
                retryable=True,
                cooldown=True,
                cooldown_seconds=_retry_delay_seconds(details),
            )
        if code == 408 or code >= 500:
            return _Failure(
                AIProviderError(f"Gemini is temporarily unavailable ({label})", status_code=code),
                retryable=True,
                backoff=True,
            )
        if code == 404:
            return _Failure(
                AIConfigurationError(
                    f"Gemini model '{self.model}' was not found or is not available ({label})",
                    status_code=code,
                ),
                retryable=False,
            )
        return _Failure(
            AIProviderError(f"Gemini rejected the request ({label})", status_code=code),
            retryable=False,
        )

    # --- helpers --------------------------------------------------------------------------------

    def _client(self, key: PooledKey) -> Any:
        client = self._clients.get(key.key_id)
        if client is None:
            client = self._client_factory(key.secret.get_secret_value())
            self._clients[key.key_id] = client
        return client

    def _merge(self, options: GenerationOptions | None) -> GenerationOptions:
        if options is None:
            return self._defaults
        overrides = {k: v for k, v in options.__dict__.items() if v is not None}
        return replace(self._defaults, **overrides)

    @staticmethod
    def _config(
        options: GenerationOptions, *, json_schema: dict[str, Any] | None = None
    ) -> types.GenerateContentConfig:
        thinking = (
            types.ThinkingConfig(thinking_budget=options.thinking_budget)
            if options.thinking_budget is not None
            else None
        )
        return types.GenerateContentConfig(
            system_instruction=options.system_instruction,
            temperature=options.temperature,
            max_output_tokens=options.max_output_tokens,
            thinking_config=thinking,
            response_mime_type="application/json" if json_schema is not None else None,
            response_json_schema=json_schema,
            # Never let the SDK execute functions on the model's behalf: actions go through
            # OpsPilot's allowlisted tools and approval gate only.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _to_ai_response(self, response: types.GenerateContentResponse) -> AIResponse:
        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason if candidate else None
        usage = response.usage_metadata
        return AIResponse(
            text=(response.text or "").strip(),
            provider=self.name,
            model=self.model,
            finish_reason=str(getattr(finish, "value", finish)) if finish is not None else None,
            usage=TokenUsage(
                prompt_tokens=usage.prompt_token_count,
                output_tokens=usage.candidates_token_count,
                total_tokens=usage.total_token_count,
            )
            if usage
            else None,
        )

    @staticmethod
    def _empty_response_message(
        response: types.GenerateContentResponse, finish_reason: str | None
    ) -> str:
        feedback = response.prompt_feedback
        block = getattr(feedback.block_reason, "value", None) if feedback else None
        message = f"Gemini returned no text (finish_reason={finish_reason}, block_reason={block})"
        if finish_reason == "MAX_TOKENS":
            message += "; raise max_output_tokens or lower thinking_budget"
        return message
