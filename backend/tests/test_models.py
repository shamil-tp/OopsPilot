import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentEvent, AgentRun, Approval, Incident, IncidentReport
from app.models.enums import (
    ActionType,
    AgentName,
    AgentRunStatus,
    ApprovalStatus,
    IncidentStatus,
    RecoveryStatus,
    RiskLevel,
    Severity,
)


def _incident() -> Incident:
    return Incident(
        title="Payment API returning 500 errors",
        description="Payment API suddenly returns 500 Internal Server Error.",
        severity=Severity.HIGH,
        service_name="payment-api",
    )


async def test_incident_defaults_and_reference(db: AsyncSession) -> None:
    incident = _incident()
    db.add(incident)
    await db.commit()
    await db.refresh(incident)

    assert incident.status is IncidentStatus.DETECTED
    assert incident.created_at is not None
    assert incident.reference == f"INC-{incident.id:03d}"


async def test_incident_children_round_trip_and_cascade(db: AsyncSession) -> None:
    incident = _incident()
    incident.agent_runs.append(
        AgentRun(agent_name=AgentName.INVESTIGATION, output={"findings": []})
    )
    incident.events.append(
        AgentEvent(
            agent_name=AgentName.INVESTIGATION,
            event_type="tool_completed",
            message="Retrieved 14 relevant log entries",
            meta={"tool": "get_application_logs"},
        )
    )
    incident.approvals.append(
        Approval(action_type=ActionType.ROLLBACK_DEPLOYMENT, target="v1.8.2", risk=RiskLevel.MEDIUM)
    )
    incident.report = IncidentReport(
        root_cause="Database connection failure introduced after deployment v1.8.2",
        confidence=0.87,
        evidence=[{"source": "deployment", "fact": "v1.8.2 deployed at 11:41"}],
    )
    db.add(incident)
    await db.commit()
    incident_id = incident.id

    def for_incident(model: type) -> object:
        return select(model).where(model.incident_id == incident_id)

    run = (await db.scalars(for_incident(AgentRun))).one()
    event = (await db.scalars(for_incident(AgentEvent))).one()
    approval = (await db.scalars(for_incident(Approval))).one()
    report = (await db.scalars(for_incident(IncidentReport))).one()
    assert run.status is AgentRunStatus.RUNNING
    assert event.meta == {"tool": "get_application_logs"}
    assert approval.status is ApprovalStatus.PENDING
    assert report.recovery_status is RecoveryStatus.NOT_ATTEMPTED
    assert report.evidence[0]["fact"] == "v1.8.2 deployed at 11:41"

    # Children must be removed by the database's ON DELETE CASCADE, not only by the ORM.
    db.expunge_all()
    await db.delete(await db.get(Incident, incident_id))
    await db.commit()
    for model in (AgentRun, AgentEvent, Approval, IncidentReport):
        count = await db.scalar(
            select(func.count()).select_from(model).where(model.incident_id == incident_id)
        )
        assert count == 0


async def test_row_level_security_enabled_on_postgres(db: AsyncSession) -> None:
    if db.bind.dialect.name != "postgresql":
        pytest.skip("RLS only exists on PostgreSQL")

    rows = await db.execute(
        text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
        )
    )
    rls = dict(rows.all())
    for table in (
        "incidents",
        "logs",
        "deployments",
        "service_health",
        "agent_runs",
        "agent_events",
        "approvals",
        "incident_reports",
        "alembic_version",
    ):
        assert rls[table] is True, table
