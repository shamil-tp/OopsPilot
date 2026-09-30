"""Final incident report (orchestrator role, CLAUDE.md §9/§27). Deterministic: no AI call.

The report is assembled from what the agents already stored, following the links each phase
recorded (verification -> execution -> approval / remediation proposal -> root cause analysis ->
investigation), plus the incident's own events. It is created once, after verification
completes (recovered or not), and stored in `incident_reports`; the table's unique constraint on
incident_id makes concurrent creation safe (the loser returns the stored report).
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import AgentConflictError, IncidentNotFoundError, latest_run, reload
from app.core.logging import get_logger
from app.models import AgentRun, Approval, Incident, IncidentReport
from app.models.enums import AgentName, AgentRunStatus, IncidentStatus, RecoveryStatus
from app.schemas.execution import ExecutionResult
from app.schemas.investigation import InvestigationResult
from app.schemas.remediation import ApprovalRead, RemediationResult
from app.schemas.report import (
    IncidentReportContent,
    IncidentReportRead,
    ReportOutcome,
    ReportSummary,
    TimelineEntry,
)
from app.schemas.root_cause import RootCauseResult
from app.schemas.verification import VerificationResult
from app.services.agent_events import EventRecorder, list_events

logger = get_logger(__name__)

# The milestones shown in the report timeline (tool-level and per-check events are left out).
TIMELINE_EVENTS = frozenset(
    {
        "incident_created",
        "agent_started",
        "evidence_found",
        "investigation_completed",
        "root_cause_analysis_started",
        "root_cause_identified",
        "remediation_recommended",
        "approval_required",
        "approval_received",
        "remediation_started",
        "remediation_completed",
        "verification_started",
        "verification_completed",
        "incident_resolved",
        "incident_escalated",
        "error",
    }
)


class ReportNotReadyError(AgentConflictError):
    """The incident has not been verified yet (HTTP 409)."""


async def _completed_output(db: AsyncSession, run_id: int | None) -> AgentRun:
    run = await reload(db, AgentRun, run_id) if run_id is not None else None
    if run is None or run.status is not AgentRunStatus.COMPLETED or not run.output:
        raise ReportNotReadyError("a phase result referenced by the verification is missing")
    return run


async def build_report(db: AsyncSession, incident_id: int) -> IncidentReportContent:
    incident = await reload(db, Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"Incident {incident_id} not found")
    verification_run = await latest_run(db, incident_id, AgentName.VERIFICATION)
    if (
        verification_run is None
        or verification_run.status is not AgentRunStatus.COMPLETED
        or not verification_run.output
    ):
        raise ReportNotReadyError(
            f"{incident.reference} has not been verified yet; the report is created after "
            "verification"
        )
    verification = VerificationResult.model_validate(verification_run.output)
    execution_run = await _completed_output(db, verification.execution_run_id)
    execution = ExecutionResult.model_validate(execution_run.output)
    remediation_run = await _completed_output(db, execution.remediation_run_id)
    remediation = RemediationResult.model_validate(remediation_run.output)
    rca = RootCauseResult.model_validate(
        (await _completed_output(db, remediation.root_cause_run_id)).output
    )
    investigation = InvestigationResult.model_validate(
        (await _completed_output(db, rca.investigation_run_id)).output
    )
    approval = await reload(db, Approval, verification.approval_id)
    if approval is None:
        raise ReportNotReadyError("the approval referenced by the verification is missing")

    events = await list_events(db, incident_id)
    resolved = incident.status is IncidentStatus.RESOLVED
    return IncidentReportContent(
        summary=ReportSummary(
            incident_id=incident.id,
            reference=incident.reference,
            title=incident.title,
            description=incident.description,
            service=incident.service_name,
            severity=incident.severity,
            final_status=incident.status,
            created_at=incident.created_at,
            resolved_at=verification_run.completed_at if resolved else None,
        ),
        timeline=[
            TimelineEntry(
                timestamp=e.timestamp,
                agent=e.agent_name,
                event_type=e.event_type,
                message=e.message,
            )
            for e in events
            if e.event_type in TIMELINE_EVENTS
        ],
        investigation=investigation,
        root_cause=rca,
        remediation=remediation,
        approval=ApprovalRead.model_validate(approval),
        execution=execution,
        verification=verification,
        outcome=ReportOutcome(
            recovered=verification.recovered,
            final_status=incident.status,
            human_decision=approval.status.value
            if approval.status.value in {"APPROVED", "REJECTED", "PENDING"}
            else "NONE",
            remediation_performed=execution_run.summary,
            verification=verification_run.summary or "",
            recovery_status=RecoveryStatus.RECOVERED
            if verification.recovered
            else RecoveryStatus.NOT_RECOVERED,
        ),
    )


async def stored_report(db: AsyncSession, incident_id: int) -> IncidentReport | None:
    return await db.scalar(
        select(IncidentReport)
        .where(IncidentReport.incident_id == incident_id)
        .execution_options(populate_existing=True)
    )


async def ensure_report(db: AsyncSession, incident_id: int) -> tuple[IncidentReport, bool]:
    """Return the stored report, creating it once if the incident has been verified.

    Returns `(report, created)`. Never changes the incident's state and never calls AI.
    """
    existing = await stored_report(db, incident_id)
    if existing is not None:
        return existing, False
    content = await build_report(db, incident_id)
    rca, execution = content.root_cause, content.execution
    row = IncidentReport(
        incident_id=incident_id,
        root_cause=rca.root_cause,
        confidence=rca.confidence,
        evidence=[e.model_dump(mode="json") for e in rca.supporting_evidence],
        action_taken=content.outcome.remediation_performed,
        recovery_status=content.outcome.recovery_status,
        report=content.model_dump(mode="json"),
    )
    db.add(row)
    EventRecorder(db, incident_id, AgentName.ORCHESTRATOR).emit(
        "report_generated",
        f"Incident report generated for {content.summary.reference} "
        f"({content.summary.final_status})",
        final_status=content.summary.final_status,
        recovered=content.outcome.recovered,
        execution_run_id=content.verification.execution_run_id,
        action=execution.action,
    )
    try:
        await db.commit()
    except IntegrityError:  # created concurrently by another request: return that one
        await db.rollback()
        existing = await stored_report(db, incident_id)
        assert existing is not None
        return existing, False
    await db.refresh(row)  # load the database-generated created_at
    logger.info("report_generated", extra={"incident_id": incident_id, "report_id": row.id})
    return row, True


def report_view(row: IncidentReport) -> IncidentReportRead:
    return IncidentReportRead(
        report_id=row.id,
        incident_id=row.incident_id,
        created_at=row.created_at,
        report=IncidentReportContent.model_validate(row.report),
    )
