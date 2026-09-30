"""The provider-independent AI interface used by the agents.

Agents depend only on `AIProvider`, `GenerationOptions` and `AIResponse` (and the errors in
`app.ai.errors`), never on a provider SDK, so Gemini can be swapped for Ollama + Qwen later
without touching agent code. Tool calling (`execute_tool_loop`) is added in a later phase.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class GenerationOptions:
    """Per-request overrides. `None` means "use the provider's configured default"."""

    system_instruction: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None
    # Reasoning-token budget for models that "think" (0 disables it where the model allows).
    thinking_budget: int | None = None


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class AIResponse:
    text: str
    provider: str
    model: str
    finish_reason: str | None = None
    usage: TokenUsage | None = None


class AIProvider(ABC):
    name: ClassVar[str]

    @abstractmethod
    async def generate(
        self, prompt: str, *, options: GenerationOptions | None = None
    ) -> AIResponse:
        """Generate free-form text."""

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        response_model: type[ModelT],
        *,
        options: GenerationOptions | None = None,
    ) -> ModelT:
        """Generate JSON matching `response_model` and return it validated.

        Raises `AIStructuredOutputError` if the output is not valid JSON for the schema; invalid
        data is never returned.
        """

    async def aclose(self) -> None:  # noqa: B027  (optional hook, no-op by default)
        """Release network resources (called on application shutdown)."""
