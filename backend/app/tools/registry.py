"""Tool registry, permission model, and the executor that enforces them.

Every tool an agent may use is declared here with a permission level:

- READ_ONLY: reads telemetry; agents may run it directly (through `ToolExecutor`).
- REQUIRES_HUMAN_APPROVAL: changes the environment; agents may only *propose* it, the backend
  runs it after a human approves (Phase 7). `ToolExecutor` refuses these unconditionally.

Agents get an explicit allowlist. Arguments are validated against a Pydantic model with bounds
before anything runs, so neither an agent bug nor model output can issue arbitrary queries.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.enums import AgentName

logger = get_logger(__name__)


class ToolPermission(StrEnum):
    READ_ONLY = "READ_ONLY"
    REQUIRES_HUMAN_APPROVAL = "REQUIRES_HUMAN_APPROVAL"


ToolHandler = Callable[[AsyncSession, Any], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: ToolPermission
    description: str
    args_model: type[BaseModel] | None = None
    handler: ToolHandler | None = None  # None: declared, not executable by agents


class ToolError(Exception):
    """A tool could not run. Messages are safe to show and store."""


class ToolPermissionError(ToolError):
    pass


class ToolArgumentError(ToolError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool {spec.name} is already registered")
        if spec.permission is ToolPermission.READ_ONLY and spec.handler is None:
            raise ValueError(f"Read-only tool {spec.name} needs a handler")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self, permission: ToolPermission | None = None) -> list[str]:
        return [
            n for n, s in self._tools.items() if permission is None or s.permission is permission
        ]


registry = ToolRegistry()

# Which tools each agent may call. Anything not listed is refused.
AGENT_TOOL_ALLOWLIST: dict[AgentName, frozenset[str]] = {
    AgentName.INVESTIGATION: frozenset(
        {
            "get_application_logs",
            "get_service_health",
            "get_recent_deployments",
            "get_previous_incidents",
        }
    ),
}


class ToolExecutor:
    """Runs allowlisted read-only tools for one agent run.

    Enforces the allowlist and permission, validates arguments, caps the number of tool calls
    (MAX_AGENT_STEPS), retries transient database errors (TOOL_MAX_RETRIES), and caches results
    so identical calls within one investigation never hit the database twice.
    """

    def __init__(
        self,
        agent: AgentName,
        db: AsyncSession,
        *,
        max_calls: int,
        max_retries: int,
        tools: ToolRegistry = registry,
    ) -> None:
        self.agent = agent
        self._db = db
        self._max_calls = max_calls
        self._max_retries = max_retries
        self._tools = tools
        self._cache: dict[str, Any] = {}
        self.calls = 0

    def check_allowed(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolPermissionError(f"Unknown tool '{name}'")
        if name not in AGENT_TOOL_ALLOWLIST.get(self.agent, frozenset()):
            raise ToolPermissionError(f"Tool '{name}' is not allowed for the {self.agent} agent")
        if spec.permission is not ToolPermission.READ_ONLY or spec.handler is None:
            raise ToolPermissionError(
                f"Tool '{name}' requires human approval; agents can only propose it"
            )
        return spec

    async def call(self, name: str, **arguments: Any) -> Any:
        spec = self.check_allowed(name)
        assert spec.handler is not None and spec.args_model is not None
        try:
            args = spec.args_model.model_validate(arguments)
        except ValidationError as exc:
            fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            raise ToolArgumentError(f"Invalid arguments for '{name}': {fields}") from None

        key = f"{name}:{json.dumps(args.model_dump(mode='json'), sort_keys=True)}"
        if key in self._cache:
            return self._cache[key]
        if self.calls >= self._max_calls:
            raise ToolError(f"Tool call limit reached ({self._max_calls} calls)")
        self.calls += 1

        for attempt in range(1, self._max_retries + 2):
            try:
                result = await spec.handler(self._db, args)
                break
            except (SQLAlchemyError, OSError) as exc:
                await self._db.rollback()
                logger.warning(
                    "tool_retry" if attempt <= self._max_retries else "tool_failed",
                    extra={
                        "agent": self.agent,
                        "tool": name,
                        "attempt": attempt,
                        "error": type(exc).__name__,
                    },
                )
                if attempt > self._max_retries:
                    raise ToolError(f"Tool '{name}' failed: {type(exc).__name__}") from None
        self._cache[key] = result
        return result
