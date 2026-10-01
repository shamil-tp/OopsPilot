"""Investigation Agent result (stored in agent_runs.output and returned by the API)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentName, AgentRunStatus, IncidentStatus, Severity
from app.schemas.common import UtcDatetime
from app.schemas.services import DeploymentRead

# "execution" is used by verification (the approved remediation that was executed).
EvidenceSource = Literal[
    "logs", "health", "deployments", "previous_incidents", "cicd", "code_review", "execution"
]


class PreviousIncidentRead(BaseModel):
    reference: str
    title: str
    severity: Severity
    status: IncidentStatus
    created_at: UtcDatetime
    root_cause: str | None = None


class EvidenceItem(BaseModel):
    """One deterministic fact collected by a read-only tool. `id` is what findings cite."""

    id: str = Field(
        description=(
            "Citation id: L<n> logs, H<n> health, D<n> deployments, P<n> incidents, C<n> CI/CD"
        )
    )
    source: EvidenceSource
    service: str
    timestamp: UtcDatetime | None
    fact: str
    data: dict[str, Any] = Field(default_factory=dict)


class InvestigationFinding(BaseModel):
    kind: Literal["observation", "hypothesis"] = Field(
        description="observation: shown by the evidence; hypothesis: possible explanation"
    )
    statement: str
    evidence_ids: list[str]


class InvestigationAnalysis(BaseModel):
    """What the model returns (JSON schema sent to the provider). Deliberately small."""

    summary: str
    findings: list[InvestigationFinding]
    confidence: float = Field(ge=0, le=1)
    next_step: Literal["root_cause_analysis", "collect_more_evidence"]


class InvestigationResult(BaseModel):
    incident_id: int
    incident_reference: str
    service: str
    status: Literal["investigation_complete"] = "investigation_complete"
    summary: str
    findings: list[InvestigationFinding]
    evidence: list[EvidenceItem]
    related_deployments: list[DeploymentRead]
    related_previous_incidents: list[PreviousIncidentRead]
    confidence: float = Field(
        ge=0, le=1, description="Confidence in the findings, not a root cause"
    )
    next_step: Literal["root_cause_analysis", "collect_more_evidence"]
    model: str


class InvestigationRunRead(BaseModel):
    run_id: int
    incident_id: int
    incident_reference: str
    incident_status: IncidentStatus
    agent: AgentName
    status: AgentRunStatus
    started_at: UtcDatetime
    completed_at: UtcDatetime | None
    summary: str | None
    result: InvestigationResult | None

    model_config = ConfigDict(from_attributes=True)
