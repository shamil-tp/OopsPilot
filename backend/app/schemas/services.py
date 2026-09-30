from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DeploymentStatus, LogLevel, ServiceStatus
from app.schemas.common import UtcDatetime


class ServiceSummary(BaseModel):
    name: str
    display_name: str
    description: str
    dependencies: list[str]
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
    cpu_usage: float = Field(description="Percent")
    memory_usage: float = Field(description="Percent")


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
