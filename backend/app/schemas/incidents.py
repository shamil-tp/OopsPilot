from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IncidentStatus, Severity
from app.schemas.common import UtcDatetime


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reference: str = Field(description="Human-friendly identifier, e.g. INC-001")
    title: str
    description: str
    severity: Severity
    status: IncidentStatus
    service_name: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
