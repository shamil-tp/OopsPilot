"""Creates the simulated incident and resets the demo environment.

Repeat-safety:
- `simulate_incident` returns the existing simulated incident while one is still active (not
  RESOLVED / FAILED / ESCALATED), so repeated clicks never create duplicates. Otherwise it
  rebuilds the simulated services' telemetry around "now" and creates a new incident; earlier
  incidents are kept as history.
- Telemetry rows have no incident_id: they describe the environment's current state. Rebuilding
  replaces only rows of the simulated services (`SERVICE_NAMES`), so logs and deployments from
  different runs never interleave into a confusing timeline.
- `reset_demo` deletes incidents (their runs, events, approvals and reports go with them through
  ON DELETE CASCADE) and the simulated services' telemetry, then seeds a healthy environment.
- CI/CD: a new simulation replays the demo repository's GitHub deliveries (push, tag, successful
  deploy-production run) through the webhook ingestion service, so the v1.8.2 deployment arrives
  as CI/CD evidence. Demo-repository events are simulated telemetry and are cleared with it;
  events from a real, configured repository are never deleted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import AgentEvent, Deployment, Incident, LogEntry, ServiceHealth
from app.models.enums import DeploymentStatus, IncidentStatus
from app.services import cicd
from app.services.scenario import (
    INCIDENT_DESCRIPTION,
    INCIDENT_TITLE,
    PRIMARY_SERVICE,
    RECOVERED_HEALTH,
    SCENARIO_ID,
    build_environment,
    classify_severity,
    restart_logs,
    rollback_logs,
)
from app.services.service_catalog import SERVICE_NAMES

logger = get_logger(__name__)

TERMINAL_STATUSES = (IncidentStatus.RESOLVED, IncidentStatus.FAILED, IncidentStatus.ESCALATED)


@dataclass(frozen=True)
class ResetResult:
    incidents_deleted: int
    logs_deleted: int
    deployments_deleted: int
    health_records_deleted: int
    cicd_events_deleted: int


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


async def find_active_simulation(db: AsyncSession) -> Incident | None:
    return await db.scalar(
        select(Incident)
        .where(
            Incident.service_name == PRIMARY_SERVICE,
            Incident.title == INCIDENT_TITLE,
            Incident.status.not_in(TERMINAL_STATUSES),
        )
        .order_by(Incident.created_at.desc(), Incident.id.desc())
        .limit(1)
    )


async def _clear_simulated_telemetry(db: AsyncSession) -> tuple[int, int, int, int]:
    """Delete telemetry of the simulated services only.

    Returns (logs, deployments, health, demo CI/CD events).
    """
    cicd_events = await cicd.clear_demo(db)
    counts = []
    for model in (LogEntry, Deployment, ServiceHealth):
        result = await db.execute(delete(model).where(model.service_name.in_(SERVICE_NAMES)))
        counts.append(result.rowcount or 0)
    return counts[0], counts[1], counts[2], cicd_events


async def simulate_incident(db: AsyncSession) -> tuple[Incident, bool]:
    """Return `(incident, created)`; `created` is False when an active simulation was reused."""
    existing = await find_active_simulation(db)
    if existing is not None:
        logger.info("simulation_reused", extra={"incident_id": existing.id})
        return existing, False

    detected_at = _now()
    await _clear_simulated_telemetry(db)
    environment = build_environment(detected_at, incident=True)
    db.add_all(environment.records())

    # Severity comes from a deterministic rule over the primary service's current health.
    current = max(
        (h for h in environment.health if h.service_name == PRIMARY_SERVICE),
        key=lambda h: h.timestamp,
    )
    incident = Incident(
        title=INCIDENT_TITLE,
        description=INCIDENT_DESCRIPTION,
        severity=classify_severity(current.status, current.error_rate),
        status=IncidentStatus.DETECTED,
        service_name=PRIMARY_SERVICE,
        created_at=detected_at,
        updated_at=detected_at,
    )
    incident.events.append(
        AgentEvent(
            agent_name=None,
            event_type="incident_created",
            message=f"Incident detected on {PRIMARY_SERVICE}",
            meta={
                "source": "simulation",
                "scenario": SCENARIO_ID,
                "error_rate": current.error_rate,
                "latency_ms": current.latency_ms,
            },
            timestamp=detected_at,
        )
    )
    db.add(incident)
    await db.commit()
    logger.info(
        "incident_simulated",
        extra={"incident_id": incident.id, "scenario": SCENARIO_ID, "service": PRIMARY_SERVICE},
    )
    try:
        await cicd.replay_demo(db, detected_at)
    except Exception as exc:  # CI/CD evidence is optional: never fail the simulation over it
        await db.rollback()
        await db.refresh(incident)  # rollback expired it; the caller still returns it
        logger.warning("demo_cicd_replay_failed", extra={"error": type(exc).__name__})
    return incident, True


async def reset_demo(db: AsyncSession) -> ResetResult:
    incidents = await db.execute(delete(Incident))
    if db.bind.dialect.name == "postgresql":
        # The table is now empty, so the next incident is INC-001 again. (SQLite reuses ids of
        # deleted rows on its own.)
        await db.execute(text("SELECT setval(pg_get_serial_sequence('incidents', 'id'), 1, false)"))
    logs, deployments, health, cicd_events = await _clear_simulated_telemetry(db)
    db.add_all(build_environment(_now(), incident=False).records())
    await db.commit()

    result = ResetResult(
        incidents_deleted=incidents.rowcount or 0,
        logs_deleted=logs,
        deployments_deleted=deployments,
        health_records_deleted=health,
        cicd_events_deleted=cicd_events,
    )
    logger.info("demo_reset", extra=result.__dict__)
    return result


# --- Simulated remediation (only ever called by the approval flow, after human approval) ------


@dataclass(frozen=True)
class RollbackEffect:
    rolled_back_deployment_id: int
    new_deployment_id: int
    health_after: ServiceHealth


async def apply_rollback(
    db: AsyncSession, *, service: str, from_version: str, to_version: str, at: datetime
) -> RollbackEffect:
    """Roll `service` back from `from_version` to `to_version` in the simulated environment.

    Marks the active deployment ROLLED_BACK, redeploys the target version (same commit) as a new
    SUCCEEDED deployment, and records the resulting logs and health. Plain database writes on
    the simulated services only: no shell, no network, no real infrastructure. The caller has
    already validated the versions and commits the transaction.
    """
    current = await db.scalar(
        select(Deployment)
        .where(
            Deployment.service_name == service,
            Deployment.version == from_version,
            Deployment.status == DeploymentStatus.SUCCEEDED,
        )
        .order_by(Deployment.timestamp.desc(), Deployment.id.desc())
        .limit(1)
    )
    target = await db.scalar(
        select(Deployment)
        .where(
            Deployment.service_name == service,
            Deployment.version == to_version,
            Deployment.status == DeploymentStatus.SUCCEEDED,
        )
        .order_by(Deployment.timestamp.desc(), Deployment.id.desc())
        .limit(1)
    )
    if current is None or target is None:
        raise LookupError("rollback deployments not found")

    await db.execute(
        update(Deployment)
        .where(Deployment.id == current.id)
        .values(status=DeploymentStatus.ROLLED_BACK)
    )
    redeploy = Deployment(
        service_name=service,
        version=to_version,
        timestamp=at,
        status=DeploymentStatus.SUCCEEDED,
        commit_sha=target.commit_sha,
    )
    logs = rollback_logs(at, service, from_version, to_version)
    status, error_rate, latency, cpu, memory = RECOVERED_HEALTH
    health = ServiceHealth(
        service_name=service,
        timestamp=logs[-1].timestamp,
        status=status,
        error_rate=error_rate,
        latency_ms=latency,
        cpu_usage=cpu,
        memory_usage=memory,
    )
    db.add_all([redeploy, *logs, health])
    await db.flush()
    return RollbackEffect(current.id, redeploy.id, health)


async def apply_restart(db: AsyncSession, *, service: str, at: datetime) -> ServiceHealth | None:
    """Restart `service` in the simulated environment.

    A restart does not change configuration, so the simulation does not invent a recovery: the
    service reports the same health as before the restart (verification decides the outcome).
    """
    logs = restart_logs(at, service)
    latest = await db.scalar(
        select(ServiceHealth)
        .where(ServiceHealth.service_name == service)
        .order_by(ServiceHealth.timestamp.desc(), ServiceHealth.id.desc())
        .limit(1)
    )
    after = None
    if latest is not None:
        after = ServiceHealth(
            service_name=service,
            timestamp=logs[-1].timestamp,
            status=latest.status,
            error_rate=latest.error_rate,
            latency_ms=latest.latency_ms,
            cpu_usage=latest.cpu_usage,
            memory_usage=latest.memory_usage,
        )
    db.add_all([*logs, *([after] if after else [])])
    await db.flush()
    return after
