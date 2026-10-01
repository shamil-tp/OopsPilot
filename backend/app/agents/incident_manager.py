"""Incident Manager for real applications: deterministic detection and the automatic response.

Detection (backend rules, never AI), after every health check of a monitored real service:
- INCIDENT_DOWN_CHECKS consecutive DOWN checks, or
- INCIDENT_DEGRADED_CHECKS consecutive checks that are not HEALTHY
open one incident for the service. No second incident is opened while one is still active.

Response (AUTO_RESPOND, on by default):
- a new incident runs investigation -> root cause analysis -> remediation proposal in the
  background, through the same agents and guardrails as the REST endpoints, and stops at the
  human approval gate (nothing is executed);
- once an approved action has been performed by an operator and VERIFY_MIN_CHECKS health checks
  follow it, verification runs and the incident report is created.

Every step is idempotent and conflict-safe, so a step that already ran (e.g. from the dashboard)
is simply skipped. A failing step stops the automatic response; the dashboard can retry it.
"""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import investigation, remediation, root_cause, verification
from app.agents import report as reports
from app.agents.common import AgentConflictError, AgentFailedError, IncidentNotFoundError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import AgentEvent, AgentRun, Incident, ServiceHealth
from app.models.enums import IncidentStatus, ServiceStatus, Severity
from app.services.service_catalog import MonitoredService

logger = get_logger(__name__)

ACTIVE = (
    IncidentStatus.DETECTED,
    IncidentStatus.INVESTIGATING,
    IncidentStatus.ANALYZING,
    IncidentStatus.AWAITING_APPROVAL,
    IncidentStatus.REMEDIATING,
    IncidentStatus.VERIFYING,
)
# Background responses; kept referenced so they are not garbage-collected mid-run.
_tasks: set[asyncio.Task[None]] = set()


def spawn(coroutine: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coroutine)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def active_incident(db: AsyncSession, service: str) -> Incident | None:
    return await db.scalar(
        select(Incident)
        .where(Incident.service_name == service, Incident.status.in_(ACTIVE))
        .order_by(Incident.created_at.desc(), Incident.id.desc())
        .limit(1)
    )


def _leading(rows: list[ServiceHealth], match: Callable[[ServiceHealth], bool]) -> int:
    count = 0
    for row in rows:
        if not match(row):
            break
        count += 1
    return count


async def detect(db: AsyncSession, service: MonitoredService) -> Incident | None:
    """Open an incident if the latest health checks meet a detection rule. Returns it, or None."""
    settings = get_settings()
    if await active_incident(db, service.name) is not None:
        return None
    window = max(settings.incident_down_checks, settings.incident_degraded_checks)
    recent = list(
        await db.scalars(
            select(ServiceHealth)
            .where(ServiceHealth.service_name == service.name)
            .order_by(ServiceHealth.timestamp.desc(), ServiceHealth.id.desc())
            .limit(window)
        )
    )
    down = _leading(recent, lambda row: row.status is ServiceStatus.DOWN)
    failing = _leading(recent, lambda row: row.status is not ServiceStatus.HEALTHY)
    if down >= settings.incident_down_checks:
        state, severity, count = "down", Severity.HIGH, down
    elif failing >= settings.incident_degraded_checks:
        state, severity, count = "degraded", Severity.MEDIUM, failing
    else:
        return None

    latest = recent[0]
    detected_at = datetime.now(UTC).replace(microsecond=0)
    where = service.url or service.name
    incident = Incident(
        title=f"{service.display_name} is {state}",
        description=(
            f"Health checks of {where} failed {count} times in a row "
            f"(latest: {latest.status}, {latest.latency_ms:g} ms)."
        ),
        severity=severity,
        status=IncidentStatus.DETECTED,
        service_name=service.name,
        created_at=detected_at,
        updated_at=detected_at,
    )
    incident.events.append(
        AgentEvent(
            agent_name=None,
            event_type="incident_created",
            message=f"Incident detected on {service.name}: {count} failed health checks in a row",
            meta={
                "source": "health_check",
                "rule": f"{count} consecutive {'DOWN' if state == 'down' else 'failed'} checks",
                "status": latest.status.value,
                "latency_ms": latest.latency_ms,
                "failed_checks_pct": latest.error_rate,
            },
            timestamp=detected_at,
        )
    )
    db.add(incident)
    await db.commit()
    logger.info(
        "incident_detected",
        extra={"incident_id": incident.id, "service": service.name, "rule": state, "checks": count},
    )
    return incident


Step = tuple[
    Callable[[AsyncSession, int], Awaitable[tuple[AgentRun, bool]]],
    Callable[[int], Awaitable[None]],
]
RESPONSE: tuple[Step, ...] = (
    (investigation.start_investigation, investigation.run_investigation),
    (root_cause.start_analysis, root_cause.run_analysis),
    (remediation.start_remediation, remediation.run_remediation),
)


async def respond(
    incident_id: int, *, session_factory: async_sessionmaker[AsyncSession] = SessionLocal
) -> None:
    """Investigation -> root cause -> remediation proposal; stops at the human approval gate."""
    for start, run in RESPONSE:
        try:
            async with session_factory() as db:
                agent_run, created = await start(db, incident_id)
            if created:
                await run(agent_run.id)
        except (AgentConflictError, AgentFailedError, IncidentNotFoundError) as exc:
            logger.warning(
                "auto_response_stopped",
                extra={
                    "incident_id": incident_id,
                    "step": start.__module__,
                    "error": type(exc).__name__,
                },
            )
            return
    logger.info("auto_response_waiting_for_approval", extra={"incident_id": incident_id})


async def verify_when_ready(
    service: str, *, session_factory: async_sessionmaker[AsyncSession] = SessionLocal
) -> None:
    """Verify an operator-remediated incident once enough health checks follow the action."""
    async with session_factory() as db:
        incident = await active_incident(db, service)
        if incident is None or incident.status is not IncidentStatus.VERIFYING:
            return
        incident_id = incident.id
        try:
            run, created = await verification.start_verification(db, incident_id)
        except AgentConflictError:
            return  # not enough checks yet, or another request is verifying
    if not created:
        return
    try:
        await verification.run_verification(run.id)
    except AgentFailedError as exc:
        logger.warning(
            "auto_verification_failed", extra={"incident_id": incident_id, "error": exc.message}
        )
        return
    async with session_factory() as db:
        await reports.ensure_report(db, incident_id)


async def after_check(db: AsyncSession, service: MonitoredService) -> None:
    """Called by the health checker after each check of a real service."""
    incident = await detect(db, service)
    if not get_settings().auto_respond:
        return
    if incident is not None:
        spawn(respond(incident.id))
    spawn(verify_when_ready(service.name))
