"""Run lifecycle shared by the agents: claim, fail safely, reload, validate citations.

Pattern (each agent step):
    1. `claim_incident` moves the incident from its expected state to the working state with a
       conditional UPDATE, so of two concurrent requests only one can start a run.
    2. The agent runs in its own session and stores output with `complete_run`.
    3. On any failure `record_failure` marks the run FAILED, stores an `error` event and puts the
       incident back in the state it was claimed from, so the step can be retried.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import AIConfigurationError, AIProviderError
from app.core.logging import get_logger
from app.models import AgentRun, Incident
from app.models.enums import AgentName, AgentRunStatus, IncidentStatus
from app.services.agent_events import EventRecorder
from app.services.remediation_policy import PolicyViolationError
from app.tools import ToolError

logger = get_logger(__name__)


class AgentError(Exception):
    """Base class; messages are safe to return to clients."""


class IncidentNotFoundError(AgentError):
    pass


class AgentConflictError(AgentError):
    """The request conflicts with the incident's state or a run in progress (HTTP 409)."""


@dataclass
class AgentFailedError(AgentError):
    message: str
    http_status: int

    def __str__(self) -> str:
        return self.message


def now() -> datetime:
    return datetime.now(UTC)


async def latest_run(db: AsyncSession, incident_id: int, agent: AgentName) -> AgentRun | None:
    return await db.scalar(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id, AgentRun.agent_name == agent)
        .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


async def reload(db: AsyncSession, model: type[Any], row_id: int) -> Any:
    """Fresh copy from the database (another session may have changed it)."""
    return await db.scalar(
        select(model).where(model.id == row_id).execution_options(populate_existing=True)
    )


async def lock_incident(db: AsyncSession, incident_id: int, *, status: IncidentStatus) -> bool:
    """Row-lock the incident if it is in `status`, without changing its state.

    A no-op UPDATE takes the row lock (PostgreSQL) / write lock (SQLite) until commit, so checks
    made after it see any run a concurrent request committed first.
    """
    locked = await db.execute(
        update(Incident)
        .where(Incident.id == incident_id, Incident.status == status)
        .values(status=status)
    )
    return locked.rowcount == 1


async def claim_incident(
    db: AsyncSession, incident_id: int, *, expected: IncidentStatus, new: IncidentStatus
) -> bool:
    claimed = await db.execute(
        update(Incident)
        .where(Incident.id == incident_id, Incident.status == expected)
        .values(status=new)
    )
    return claimed.rowcount == 1


async def complete_run(
    db: AsyncSession, run_id: int, *, summary: str, output: dict[str, Any]
) -> None:
    await db.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(status=AgentRunStatus.COMPLETED, completed_at=now(), summary=summary, output=output)
    )


def failure_for(exc: Exception, *, step: str, restored: IncidentStatus) -> AgentFailedError:
    """Map an exception to a safe message and HTTP status. Never includes raw exception text
    except from our own error types, whose messages are safe by construction."""
    retry = f" The incident is back in {restored} and the {step} can be retried."
    if isinstance(exc, AIConfigurationError):
        return AgentFailedError(f"AI provider is not configured: {exc}.{retry}", 503)
    if isinstance(exc, AgentConflictError):
        return AgentFailedError(f"{exc}.{retry}", 409)
    if isinstance(exc, PolicyViolationError):
        return AgentFailedError(f"Proposal rejected by backend policy: {exc}.{retry}", 502)
    if isinstance(exc, AIProviderError):
        return AgentFailedError(f"AI analysis failed: {exc}.{retry}", 502)
    if isinstance(exc, ToolError):
        return AgentFailedError(f"Evidence collection failed: {exc}.{retry}", 503)
    if isinstance(exc, SQLAlchemyError | OSError):
        return AgentFailedError(f"Database unavailable during the {step}.{retry}", 503)
    return AgentFailedError(f"The {step} failed unexpectedly.{retry}", 500)


async def record_failure(
    db: AsyncSession,
    *,
    agent: AgentName,
    run_id: int,
    incident_id: int,
    message: str,
    claimed: IncidentStatus,
    restore: IncidentStatus,
) -> None:
    """Best effort: if the database itself is down, the error is still returned to the client."""
    try:
        await db.rollback()
        await db.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(status=AgentRunStatus.FAILED, completed_at=now(), summary=message)
        )
        await db.execute(
            update(Incident)
            .where(Incident.id == incident_id, Incident.status == claimed)
            .values(status=restore)
        )
        EventRecorder(db, incident_id, agent).emit("error", message, run_id=run_id)
        await db.commit()
    except (SQLAlchemyError, OSError) as exc:
        logger.error(
            "agent_failure_not_recorded",
            extra={"agent": agent, "run_id": run_id, "error": type(exc).__name__},
        )


def keep_known(ids: Iterable[str], known: set[str]) -> list[str]:
    """Cited ids that the backend actually supplied, deduplicated, in order."""
    return [ref for ref in dict.fromkeys(ids) if ref in known]
