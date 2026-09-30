"""Builds the configured AI provider.

The provider is created lazily on first use, not at startup: the rest of the API (incidents,
services, demo) keeps working without Gemini keys, and a misconfiguration surfaces as an
`AIConfigurationError` exactly where AI is needed.

Usage (agents):
    provider = get_ai_provider()
    result = await provider.generate_structured(prompt, SomeModel)
"""

import threading

from app.ai.base import AIProvider, GenerationOptions
from app.ai.errors import AIConfigurationError
from app.ai.gemini_provider import GeminiProvider
from app.ai.key_pool import GeminiKeyPool
from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_PROVIDERS = ("gemini",)

_provider: AIProvider | None = None
_lock = threading.Lock()


def create_ai_provider(settings: Settings) -> AIProvider:
    if settings.ai_provider != "gemini":
        raise AIConfigurationError(
            f"Unsupported AI_PROVIDER '{settings.ai_provider}'. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    pool = GeminiKeyPool(
        settings.gemini_api_key_slots, cooldown_seconds=settings.gemini_key_cooldown_seconds
    )
    provider = GeminiProvider(
        model=settings.gemini_model,
        key_pool=pool,
        max_retries=settings.llm_max_retries,
        defaults=GenerationOptions(
            temperature=settings.llm_temperature,
            max_output_tokens=settings.llm_max_output_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
    )
    logger.info(
        "ai_provider_initialized",
        extra={
            "provider": provider.name,
            "model": settings.gemini_model,
            "keys": [s.key_id for s in pool.stats()],
            "max_retries": settings.llm_max_retries,
        },
    )
    return provider


def get_ai_provider() -> AIProvider:
    """The shared provider instance (one key pool for all agents)."""
    global _provider
    with _lock:
        if _provider is None:
            _provider = create_ai_provider(get_settings())
        return _provider


async def close_ai_provider() -> None:
    global _provider
    with _lock:
        provider, _provider = _provider, None
    if provider is not None:
        await provider.aclose()
