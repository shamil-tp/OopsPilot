"""Human decision on a remediation proposal, and its execution: by OpsPilot in the simulation,
by a human operator for a real application."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import ActionType, AgentRunStatus, IncidentStatus, ServiceStatus
from app.schemas.common import UtcDatetime
from app.schemas.remediation import ApprovalRead


class ServiceSnapshot(BaseModel):
    active_version: str | None
    status: ServiceStatus | None
    error_rate: float | None
    latency_ms: float | None


class ExecutionResult(BaseModel):
    incident_id: int
    approval_id: int
    remediation_run_id: int | None
    action: ActionType
    target: str
    parameters: dict[str, Any] = Field(description="The approved parameters that were executed")
    status: Literal["executed"] = "executed"
    simulated: bool = True
    # Real applications: OpsPilot never acts on them; an operator performs the approved action
    # and confirms it, and verification measures the result.
    performed_by: Literal["opspilot", "operator"] = "opspilot"
    service: str
    before: ServiceSnapshot
    after: ServiceSnapshot
    executed_at: UtcDatetime
    next_step: Literal["verification"] = "verification"


class ExecutionRead(BaseModel):
    run_id: int
    status: AgentRunStatus
    started_at: UtcDatetime
    completed_at: UtcDatetime | None
    summary: str | None
    result: ExecutionResult | None
    mode: Literal["simulated", "operator"] = "simulated"
    instructions: str | None = Field(
        default=None, description="What the operator must do (operator mode, before it is done)"
    )


class DecisionRead(BaseModel):
    incident_id: int
    incident_reference: str
    incident_status: IncidentStatus
    approval: ApprovalRead
    execution: ExecutionRead | None
