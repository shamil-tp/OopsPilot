from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AgentName
from app.tools import AGENT_TOOL_ALLOWLIST, ToolExecutor, ToolPermission, registry
from app.tools.registry import (
    ToolArgumentError,
    ToolError,
    ToolPermissionError,
    ToolRegistry,
    ToolSpec,
)

NOW = datetime(2026, 9, 30, 11, 44, tzinfo=UTC)


def executor(db: AsyncSession, **kwargs: Any) -> ToolExecutor:
    kwargs.setdefault("max_calls", 8)
    kwargs.setdefault("max_retries", 2)
    return ToolExecutor(AgentName.INVESTIGATION, db, **kwargs)


def test_permission_model() -> None:
    assert set(registry.names(ToolPermission.READ_ONLY)) == {
        "get_application_logs",
        "get_service_health",
        "get_recent_deployments",
        "get_previous_incidents",
    }
    assert set(registry.names(ToolPermission.REQUIRES_HUMAN_APPROVAL)) == {
        "rollback_deployment",
        "restart_service",
    }
    # The investigation allowlist contains read-only tools only.
    allowed = AGENT_TOOL_ALLOWLIST[AgentName.INVESTIGATION]
    assert allowed <= set(registry.names(ToolPermission.READ_ONLY))


@pytest.mark.parametrize("tool", ["rollback_deployment", "restart_service"])
async def test_action_tools_are_refused(db: AsyncSession, tool: str) -> None:
    with pytest.raises(ToolPermissionError, match="requires human approval|not allowed"):
        await executor(db).call(tool, version="v1.8.2")


async def test_unknown_tool_is_refused(db: AsyncSession) -> None:
    with pytest.raises(ToolPermissionError, match="Unknown tool"):
        await executor(db).call("execute_sql", query="DROP TABLE incidents")


async def test_agent_without_allowlist_gets_nothing(db: AsyncSession) -> None:
    remediation = ToolExecutor(AgentName.REMEDIATION, db, max_calls=8, max_retries=0)

    with pytest.raises(ToolPermissionError, match="not allowed for the remediation agent"):
        await remediation.call("get_service_health", service="payment-api")


async def test_allowlisted_action_tool_is_still_refused(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even if someone adds an action tool to the allowlist, the permission check blocks it.
    monkeypatch.setitem(
        AGENT_TOOL_ALLOWLIST,
        AgentName.INVESTIGATION,
        AGENT_TOOL_ALLOWLIST[AgentName.INVESTIGATION] | {"rollback_deployment"},
    )

    with pytest.raises(ToolPermissionError, match="requires human approval"):
        await executor(db).call("rollback_deployment", version="v1.8.2")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_service_health", {"service": "billing-api"}),
        ("get_service_health", {"service": "payment-api", "query": "SELECT 1"}),
        (
            "get_application_logs",
            {"service": "payment-api", "since": NOW - timedelta(hours=2), "until": NOW},
        ),
        (
            "get_application_logs",
            {"service": "payment-api", "since": NOW, "until": NOW, "limit": 500},
        ),
        (
            "get_application_logs",
            {"service": "payment-api", "since": NOW, "until": NOW - timedelta(minutes=1)},
        ),
        ("get_recent_deployments", {"service": "payment-api", "limit": 50}),
        (
            "get_previous_incidents",
            {"service": "payment-api", "before": NOW, "exclude_incident_id": 1, "limit": 100},
        ),
    ],
)
async def test_arguments_are_validated_and_bounded(
    db: AsyncSession, tool: str, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ToolArgumentError, match=f"Invalid arguments for '{tool}'"):
        await executor(db).call(tool, **arguments)


class _Args(BaseModel):
    n: int


def _custom(handler: Any) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(ToolSpec("probe", ToolPermission.READ_ONLY, "test", _Args, handler))
    return tools


@pytest.fixture
def allow_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(AGENT_TOOL_ALLOWLIST, AgentName.INVESTIGATION, frozenset({"probe"}))


@pytest.mark.usefixtures("allow_probe")
async def test_results_are_cached_and_calls_are_capped(db: AsyncSession) -> None:
    calls: list[int] = []

    async def handler(_db: AsyncSession, args: _Args) -> int:
        calls.append(args.n)
        return args.n * 10

    ex = executor(db, max_calls=2, tools=_custom(handler))

    assert await ex.call("probe", n=1) == 10
    assert await ex.call("probe", n=1) == 10  # cached: no second database call
    assert await ex.call("probe", n=2) == 20
    with pytest.raises(ToolError, match="limit reached"):
        await ex.call("probe", n=3)
    assert calls == [1, 2]


@pytest.mark.usefixtures("allow_probe")
async def test_transient_database_errors_are_retried_then_bounded(db: AsyncSession) -> None:
    attempts: list[int] = []

    async def flaky(_db: AsyncSession, args: _Args) -> str:
        attempts.append(args.n)
        if len(attempts) < 3:
            raise OperationalError("SELECT", {}, Exception("connection reset"))
        return "ok"

    assert await executor(db, tools=_custom(flaky)).call("probe", n=1) == "ok"
    assert len(attempts) == 3  # 1 + TOOL_MAX_RETRIES(2)

    async def down(_db: AsyncSession, _args: _Args) -> str:
        raise OperationalError("SELECT", {}, Exception("password=secret"))

    with pytest.raises(ToolError, match="failed: OperationalError") as info:
        await executor(db, tools=_custom(down)).call("probe", n=1)
    assert "secret" not in str(info.value)
