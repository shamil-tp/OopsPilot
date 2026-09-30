"""Non-destructive check of the configured database (DATABASE_URL), e.g. Supabase.

    cd backend && python -m scripts.check_database

Checks connectivity, the Alembic revision, that all OpsPilot tables exist with RLS enabled, and
CRUD across every table. All writes happen inside a single transaction that is always rolled
back, so nothing is ever committed and no existing data is touched.
"""

import asyncio
import sys
from datetime import UTC, datetime

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.core.config import BACKEND_DIR, get_settings, redact_database_url
from app.models import (
    AgentEvent,
    AgentRun,
    Approval,
    Deployment,
    Incident,
    IncidentReport,
    LogEntry,
    ServiceHealth,
)
from app.models.enums import (
    ActionType,
    AgentName,
    DeploymentStatus,
    LogLevel,
    RiskLevel,
    ServiceStatus,
    Severity,
)

TABLES = (
    "incidents",
    "logs",
    "deployments",
    "service_health",
    "agent_runs",
    "agent_events",
    "approvals",
    "incident_reports",
)
# Marks the throwaway rows; they are rolled back regardless.
PROBE = "opspilot-db-check"


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' - {detail}' if detail else ''}")
    return ok


async def check_schema(session: AsyncSession) -> bool:
    version = await session.scalar(text("SHOW server_version"))
    report("connection", True, f"PostgreSQL {version}")

    head = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_current_head()
    current = await session.scalar(text("SELECT version_num FROM alembic_version"))
    ok = report("alembic revision", current == head, f"current={current} head={head}")

    rows = await session.execute(
        text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
        )
    )
    rls = dict(rows.all())
    missing = [t for t in TABLES if t not in rls]
    ok &= report("tables", not missing, f"missing: {missing}" if missing else "all 8 present")
    no_rls = [t for t in TABLES if t in rls and not rls[t]]
    ok &= report("row level security", not no_rls, f"disabled on: {no_rls}" if no_rls else "")
    return ok


async def check_crud(session: AsyncSession) -> bool:
    now = datetime.now(UTC)
    service = f"{PROBE}-service"
    session.add_all(
        [
            LogEntry(service_name=service, timestamp=now, level=LogLevel.ERROR, message=PROBE),
            Deployment(
                service_name=service,
                version="v0.0.0",
                timestamp=now,
                status=DeploymentStatus.SUCCEEDED,
                commit_sha="0000000",
            ),
            ServiceHealth(
                service_name=service,
                timestamp=now,
                status=ServiceStatus.DEGRADED,
                error_rate=37,
                latency_ms=2800,
                cpu_usage=43,
                memory_usage=68,
            ),
        ]
    )
    incident = Incident(title=PROBE, severity=Severity.HIGH, service_name=service)
    incident.agent_runs.append(AgentRun(agent_name=AgentName.INVESTIGATION))
    incident.events.append(
        AgentEvent(agent_name=AgentName.INVESTIGATION, event_type="probe", message=PROBE)
    )
    incident.approvals.append(
        Approval(action_type=ActionType.ROLLBACK_DEPLOYMENT, target="v0.0.0", risk=RiskLevel.LOW)
    )
    incident.report = IncidentReport(root_cause=PROBE, confidence=0.5, evidence=[])
    session.add(incident)
    await session.flush()
    ok = report("create (all 8 tables)", True)

    session.expunge_all()
    loaded = await session.scalar(
        select(Incident)
        .where(Incident.id == incident.id)
        .options(
            selectinload(Incident.agent_runs),
            selectinload(Incident.events),
            selectinload(Incident.approvals),
            selectinload(Incident.report),
        )
    )
    telemetry = [
        await session.scalar(select(func.count()).select_from(m).where(m.service_name == service))
        for m in (LogEntry, Deployment, ServiceHealth)
    ]
    related = (
        loaded is not None
        and len(loaded.agent_runs) == len(loaded.events) == len(loaded.approvals) == 1
        and loaded.report is not None
        and telemetry == [1, 1, 1]
    )
    ok &= report("read related records", related)

    await session.delete(loaded)
    await session.flush()
    leftovers = [
        await session.scalar(
            select(func.count()).select_from(m).where(m.incident_id == incident.id)
        )
        for m in (AgentRun, AgentEvent, Approval, IncidentReport)
    ]
    ok &= report("delete cascades to children", leftovers == [0, 0, 0, 0])
    return ok


async def main() -> int:
    database_url = get_settings().database_url
    print(f"Checking {redact_database_url(database_url)}")
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                ok = await check_schema(session)
                ok &= await check_crud(session)
            finally:
                await session.close()
                await transaction.rollback()
        report("cleanup", True, "transaction rolled back, nothing committed")
    except Exception as exc:  # report any failure as one line, never a traceback
        report("database check", False, f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        await engine.dispose()
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
