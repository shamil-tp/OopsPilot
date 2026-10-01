from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DeploymentStatus, LogLevel, ServiceStatus
from app.schemas.common import UtcDatetime


class ServiceSummary(BaseModel):
    name: str
    display_name: str
    description: str
    dependencies: list[str]
    kind: Literal["real", "demo"] = Field(description="real application or demo simulation")
    url: str | None = Field(description="URL whose health checks measure this service (real only)")
    status: ServiceStatus | None = Field(
        description="Status from the latest health snapshot; null if none has been recorded"
    )
    last_health_at: UtcDatetime | None


class ServiceHealthRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_name: str
    timestamp: UtcDatetime
    status: ServiceStatus
    error_rate: float = Field(description="Percentage of failed requests (0-100)")
    latency_ms: float
    cpu_usage: float | None = Field(description="Percent; null when not measured")
    memory_usage: float | None = Field(description="Percent; null when not measured")


class LogEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: UtcDatetime
    service_name: str
    level: LogLevel
    message: str
    metadata: dict[str, Any] = Field(validation_alias="meta")


class DeploymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_name: str
    version: str
    status: DeploymentStatus
    timestamp: UtcDatetime
    commit_sha: str | None


class ProjectRead(BaseModel):
    """One monitored real application (non-sensitive configuration only)."""

    name: str
    environment: str
    service: str
    url: str | None = Field(description="Health-checked URL")
    repository: str | None = Field(description="GitHub repository mapped to this project")


class ProjectsRead(BaseModel):
    """What OpsPilot is watching."""

    projects: list[ProjectRead] = Field(description="Empty when only the demo is set up")
    demo_mode: bool = Field(description="Simulate incident / Reset demo are available")
    health_check_interval_seconds: float


class WebhookInstructions(BaseModel):
    payload_url: str
    content_type: str = "application/json"
    secret_configured: bool
    events: list[str] = ["push", "deployment_status"]
    github_setup_url: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100, description="Project display name")
    service: str | None = Field(
        default=None, max_length=64, description="Service slug (e.g. 'my-api'); auto-derived if omitted"
    )
    url: str | None = Field(default=None, max_length=300, description="Health check endpoint URL")
    repository: str = Field(min_length=3, max_length=140, description="GitHub repository 'owner/repo'")
    environment: str = Field(default="production", max_length=32)


class ProjectCreateResponse(BaseModel):
    project: ProjectRead
    webhook: WebhookInstructions

