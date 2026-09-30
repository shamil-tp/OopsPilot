"""Final incident report (CLAUDE.md §27): a deterministic snapshot of the stored phase results.

It reuses each phase's own result schema instead of re-describing them, so the report says
exactly what the agents stored — nothing is regenerated or re-decided.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import AgentName, IncidentStatus, RecoveryStatus, Severity
from app.schemas.common import UtcDatetime
from app.schemas.execution import ExecutionResult
from app.schemas.investigation import InvestigationResult
from app.schemas.remediation import ApprovalRead, RemediationResult
from app.schemas.root_cause import RootCauseResult
from app.schemas.verification import VerificationResult


class ReportSummary(BaseModel):
    incident_id: int
    reference: str
    title: str
    description: str
    service: str
    severity: Severity
    final_status: IncidentStatus
    created_at: UtcDatetime
    resolved_at: UtcDatetime | None = Field(description="When verification resolved it, if it did")


class TimelineEntry(BaseModel):
    timestamp: UtcDatetime
    agent: AgentName | None
    event_type: str
    message: str


class ReportOutcome(BaseModel):
    recovered: bool
    final_status: IncidentStatus
    human_decision: Literal["APPROVED", "REJECTED", "PENDING", "NONE"]
    remediation_performed: str | None
    verification: str
    recovery_status: RecoveryStatus


class IncidentReportContent(BaseModel):
    summary: ReportSummary
    timeline: list[TimelineEntry]
    investigation: InvestigationResult
    root_cause: RootCauseResult
    remediation: RemediationResult
    approval: ApprovalRead
    execution: ExecutionResult
    verification: VerificationResult
    outcome: ReportOutcome


class IncidentReportRead(BaseModel):
    report_id: int
    incident_id: int
    created_at: UtcDatetime
    report: IncidentReportContent
