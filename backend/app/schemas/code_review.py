"""AI code review of a push: what the model returns, and what the API serves."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CodeReviewStatus, RiskLevel
from app.schemas.common import UtcDatetime

Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal[
    "bug", "security", "performance", "reliability", "configuration", "maintainability", "testing"
]
SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class ReviewFinding(BaseModel):
    severity: Severity
    category: Category
    file: str = Field(description="Exactly one of the file names in the diff")
    line: int | None = Field(default=None, ge=1, description="Line in the new file, if known")
    title: str = Field(max_length=160)
    explanation: str = Field(description="What is wrong and its impact, from the changed lines")
    recommendation: str = Field(description="How to fix it, concretely")


class StoredFinding(ReviewFinding):
    """A finding as served: from the AI review or from the static syntax check."""

    source: Literal["ai", "static"] = "ai"


class CodeReviewAnalysis(BaseModel):
    """What the model returns (JSON schema sent to the provider)."""

    summary: str
    risk: RiskLevel = Field(description="Overall production risk of deploying this change")
    findings: list[ReviewFinding]


class CodeReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository: str
    service_name: str
    cicd_event_id: int | None
    commit_sha: str
    base_sha: str | None
    branch: str | None
    commit_message: str | None
    author: str | None
    status: CodeReviewStatus
    risk: RiskLevel | None
    summary: str | None
    findings: list[StoredFinding]
    files: list[dict[str, Any]]
    skipped_files: list[dict[str, Any]]
    truncated: bool
    model: str | None
    error: str | None
    created_at: UtcDatetime
    completed_at: UtcDatetime | None
