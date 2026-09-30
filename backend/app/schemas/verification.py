"""Verification: backend recovery checks, the model's explanation, and the stored result."""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import AgentName, AgentRunStatus, IncidentStatus
from app.schemas.common import UtcDatetime
from app.schemas.execution import ServiceSnapshot
from app.schemas.investigation import EvidenceItem

CheckName = Literal[
    "remediation_executed",
    "target_deployment_active",
    "service_healthy",
    "error_rate_recovered",
    "latency_recovered",
    "telemetry_fresh",
]


class RecoveryCheck(BaseModel):
    name: CheckName
    passed: bool
    expected: str
    actual: str
    evidence_ids: list[str]


class VerificationExplanation(BaseModel):
    """What the model returns: an explanation only. It has no `recovered` field and cannot
    change the outcome, which the backend's checks decide."""

    reasoning_summary: str
    supporting_evidence: list[str] = Field(description="Evidence ids the summary relies on")


class VerificationResult(BaseModel):
    incident_id: int
    incident_reference: str
    service: str
    status: Literal["verification_complete"] = "verification_complete"
    recovered: bool = Field(description="Decided by the backend checks, never by the model")
    confidence: float = Field(ge=0, le=1, description="Share of recovery checks that passed")
    before: ServiceSnapshot
    after: ServiceSnapshot
    checks: list[RecoveryCheck]
    failed_checks: list[CheckName]
    supporting_evidence: list[EvidenceItem]
    reasoning_summary: str
    next_step: Literal["incident_report", "human_investigation"]
    execution_run_id: int
    approval_id: int
    model: str


class VerificationRunRead(BaseModel):
    run_id: int
    incident_id: int
    incident_reference: str
    incident_status: IncidentStatus
    agent: AgentName
    status: AgentRunStatus
    started_at: UtcDatetime
    completed_at: UtcDatetime | None
    summary: str | None
    result: VerificationResult | None
