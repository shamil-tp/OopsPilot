"""Root Cause Analysis: what the model returns, and the stored/returned result."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentName, AgentRunStatus, IncidentStatus
from app.schemas.common import UtcDatetime
from app.schemas.investigation import EvidenceItem

RootCauseCategory = Literal[
    "deployment_regression",
    "configuration_error",
    "database_failure",
    "network_connectivity",
    "resource_exhaustion",
    "external_dependency",
    "transient",
    "unknown",
]
Assessment = Literal["less_likely", "ruled_out", "not_assessable"]
NextStep = Literal["propose_remediation", "collect_more_evidence", "escalate_to_human"]


class CitedStatement(BaseModel):
    statement: str
    evidence_ids: list[str]


class AlternativeExplanation(BaseModel):
    explanation: str
    assessment: Assessment = Field(
        description="ruled_out only if the evidence contradicts it; otherwise less_likely"
    )
    reason: str
    evidence_ids: list[str]


class RootCauseAnalysis(BaseModel):
    """What the model returns (JSON schema sent to the provider)."""

    root_cause: str
    category: RootCauseCategory
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[str] = Field(description="Evidence ids supporting the root cause")
    causal_chain: list[CitedStatement] = Field(description="Key events in time order")
    contributing_factors: list[CitedStatement]
    alternative_explanations: list[AlternativeExplanation]
    missing_evidence: list[str] = Field(description="Data that would raise confidence")
    reasoning_summary: str
    recommended_next_step: NextStep


class RootCauseResult(BaseModel):
    incident_id: int
    incident_reference: str
    service: str
    status: Literal["root_cause_identified"] = "root_cause_identified"
    root_cause: str
    category: RootCauseCategory
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[EvidenceItem]
    causal_chain: list[CitedStatement]
    contributing_factors: list[CitedStatement]
    alternative_explanations: list[AlternativeExplanation]
    missing_evidence: list[str]
    reasoning_summary: str
    recommended_next_step: NextStep
    investigation_run_id: int
    model: str


class RootCauseRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    incident_id: int
    incident_reference: str
    incident_status: IncidentStatus
    agent: AgentName
    status: AgentRunStatus
    started_at: UtcDatetime
    completed_at: UtcDatetime | None
    summary: str | None
    result: RootCauseResult | None
