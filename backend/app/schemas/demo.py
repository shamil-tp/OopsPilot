from typing import Literal

from pydantic import BaseModel, Field


class DemoResetResponse(BaseModel):
    status: Literal["reset"] = "reset"
    message: str
    incidents_deleted: int
    logs_deleted: int
    deployments_deleted: int
    health_records_deleted: int
    cicd_events_deleted: int = Field(description="Demo-repository CI/CD events only")
