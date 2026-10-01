"""CI/CD telemetry and GitHub webhook responses."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CicdCategory, CicdConclusion, CicdStatus
from app.schemas.common import UtcDatetime


class CicdEventRead(BaseModel):
    """One normalized CI/CD event (never the raw GitHub payload)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    delivery_id: str
    event_type: str
    category: CicdCategory
    repository: str
    branch: str | None
    commit_sha: str | None
    commit_message: str | None = Field(description="First line of the commit/run title, clipped")
    actor: str | None
    workflow_name: str | None
    workflow_run_id: int | None
    run_number: int | None
    status: CicdStatus
    conclusion: CicdConclusion | None
    service_name: str | None
    environment: str | None
    version: str | None = Field(description="Null when no tag/metadata names a version")
    version_source: str | None = Field(description="tag | deployment_payload | null")
    html_url: str | None
    occurred_at: UtcDatetime
    started_at: UtcDatetime | None
    completed_at: UtcDatetime | None
    received_at: UtcDatetime
    deployment_id: int | None = Field(description="Deployment created by or linked to this event")
    metadata: dict[str, Any] = Field(validation_alias="meta")


class WebhookResponse(BaseModel):
    status: Literal["recorded", "duplicate", "ignored", "pong"]
    delivery_id: str
    event_type: str
    reason: str | None = None
    event: CicdEventRead | None = None


class WebhookStatus(BaseModel):
    """Non-sensitive webhook configuration (never the secret or anything derived from it)."""

    configured: bool = Field(description="A webhook secret is set, so deliveries are accepted")
    repository: str | None = Field(description="Only this repository is accepted, if set")
    service: str | None = Field(description="The service the configured repository deploys")
    supported_events: list[str]
    max_payload_bytes: int
