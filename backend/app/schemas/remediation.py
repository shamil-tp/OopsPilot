"""Remediation: what the model proposes, the validated stored result, and approvals."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActionType,
    AgentName,
    AgentRunStatus,
    ApprovalStatus,
    IncidentStatus,
    RiskLevel,
)
from app.schemas.common import UtcDatetime
from app.schemas.investigation import EvidenceItem


class RemediationProposal(BaseModel):
    """What the model returns. It has no risk / approval / execution fields: the backend decides
    those. The action is restricted to the four supported types by the JSON schema."""

    action: ActionType
    target_service: str
    rollback_to_version: str | None = Field(
        description="Only for ROLLBACK_DEPLOYMENT: exactly one of the listed rollback candidates"
    )
    reason: str
    supporting_evidence: list[str] = Field(description="Evidence ids justifying the action")
    confidence: float = Field(ge=0, le=1)


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    action_type: ActionType
    target: str
    risk: RiskLevel
    reason: str
    parameters: dict[str, Any]
    status: ApprovalStatus
    requested_at: UtcDatetime
    decided_at: UtcDatetime | None


class RemediationResult(BaseModel):
    incident_id: int
    incident_reference: str
    service: str
    status: Literal["approval_required", "no_action", "escalated"]
    action: ActionType
    target: str
    parameters: dict[str, Any]
    reason: str
    risk: RiskLevel = Field(description="Set by backend policy, not by the model")
    requires_approval: bool = Field(description="Set by backend policy, not by the model")
    approval_id: int | None
    supporting_evidence: list[EvidenceItem]
    confidence: float = Field(ge=0, le=1)
    executed: Literal[False] = False
    root_cause_run_id: int
    model: str


class RemediationRunRead(BaseModel):
    run_id: int
    incident_id: int
    incident_reference: str
    incident_status: IncidentStatus
    agent: AgentName
    status: AgentRunStatus
    started_at: UtcDatetime
    completed_at: UtcDatetime | None
    summary: str | None
    result: RemediationResult | None
    approval: ApprovalRead | None
