"""Allowlisted agent tools (read-only now; approval-gated actions in a later phase).

Importing this package registers every tool on `app.tools.registry.registry`.

Owner: Backend/DevOps Simulation.
"""

from app.tools import telemetry_tools  # noqa: F401  (registers the tools)
from app.tools.registry import (
    AGENT_TOOL_ALLOWLIST,
    ToolError,
    ToolExecutor,
    ToolPermission,
    ToolPermissionError,
    registry,
)

__all__ = [
    "AGENT_TOOL_ALLOWLIST",
    "ToolError",
    "ToolExecutor",
    "ToolPermission",
    "ToolPermissionError",
    "registry",
]
