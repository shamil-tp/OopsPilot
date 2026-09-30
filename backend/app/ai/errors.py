"""Provider-independent AI errors.

Messages are safe to log and show: they never contain API keys, prompts, or raw provider
responses. Provider SDK exceptions are never chained (`raise ... from None`), so their
details cannot leak through tracebacks either.
"""


class AIProviderError(Exception):
    """Any AI provider failure. Subclasses narrow down the cause."""

    def __init__(
        self, message: str, *, status_code: int | None = None, attempts: int | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


class AIConfigurationError(AIProviderError):
    """Missing or invalid configuration: no API keys, unknown provider, unavailable model."""


class AIAuthenticationError(AIProviderError):
    """The provider rejected every API key that was tried."""


class AIRateLimitError(AIProviderError):
    """Rate limit or quota exhausted on every available key."""


class AITimeoutError(AIProviderError):
    """The provider did not answer in time, or could not be reached."""


class AIStructuredOutputError(AIProviderError):
    """The response was not valid JSON for the requested schema."""
