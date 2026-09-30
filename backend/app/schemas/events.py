from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentName
from app.schemas.common import UtcDatetime


class AgentEventRead(BaseModel):
    """One entry of an incident's agent timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    agent: AgentName | None = Field(validation_alias="agent_name")
    event_type: str
    message: str
    metadata: dict[str, Any] = Field(validation_alias="meta")
    timestamp: UtcDatetime
