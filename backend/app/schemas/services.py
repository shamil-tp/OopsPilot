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
    """What OpsPilot is watching (non-sensitive configuration only)."""

    name: str | None = Field(
        description="MONITORED_PROJECT_NAME; null when only the demo is set up"
    )
    environment: str
    service: str | None
    url: str | None
    repository: str | None = Field(description="GitHub repository whose webhooks are accepted")
    demo_mode: bool = Field(description="Simulate incident / Reset demo are available")
    health_check_interval_seconds: float
