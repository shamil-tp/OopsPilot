"""Read-only telemetry tools. Thin, bounded wrappers over app.services.telemetry/incidents.

Each tool validates its arguments (known service, bounded window and limits) and returns the same
Pydantic read models the REST API uses. Action tools (rollback_deployment, restart_service) are
only declared here, as REQUIRES_HUMAN_APPROVAL; they are implemented in a later phase and can
never be run by an agent.
"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LogLevel, ServiceStatus
from app.schemas.common import UtcDatetime
from app.schemas.investigation import PreviousIncidentRead
from app.schemas.services import DeploymentRead, LogEntryRead, ServiceHealthRead
from app.services import incidents, telemetry
from app.services.service_catalog import service_names
from app.tools.registry import ToolPermission, ToolSpec, registry

MAX_LOG_WINDOW = timedelta(minutes=60)


class _ServiceArgs(BaseModel):
    # Unknown arguments are rejected, never silently ignored.
    model_config = ConfigDict(extra="forbid")

    service: str

    @field_validator("service")
    @classmethod
    def _known_service(cls, value: str) -> str:
        if value not in service_names():
            raise ValueError("unknown service")
        return value


class LogsArgs(_ServiceArgs):
    since: UtcDatetime
    until: UtcDatetime
    levels: list[LogLevel] | None = None
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _bounded_window(self) -> "LogsArgs":
        if not timedelta(0) <= self.until - self.since <= MAX_LOG_WINDOW:
            raise ValueError("window must be between 0 and 60 minutes")
        return self


class HealthArgs(_ServiceArgs):
    until: UtcDatetime | None = None
    status: ServiceStatus | None = None


class DeploymentsArgs(_ServiceArgs):
    until: UtcDatetime | None = None
    limit: int = Field(default=3, ge=1, le=5)


class PreviousIncidentsArgs(_ServiceArgs):
    before: UtcDatetime
    exclude_incident_id: int
    limit: int = Field(default=3, ge=1, le=5)


async def get_application_logs(db: AsyncSession, args: LogsArgs) -> list[LogEntryRead]:
    rows = await telemetry.list_logs(
        db, args.service, limit=args.limit, levels=args.levels, since=args.since, until=args.until
    )
    return [LogEntryRead.model_validate(row) for row in rows]


async def get_service_health(db: AsyncSession, args: HealthArgs) -> ServiceHealthRead | None:
    row = await telemetry.latest_health(db, args.service, until=args.until, status=args.status)
    return ServiceHealthRead.model_validate(row) if row else None


async def get_recent_deployments(db: AsyncSession, args: DeploymentsArgs) -> list[DeploymentRead]:
    rows = await telemetry.list_deployments(db, args.service, limit=args.limit, until=args.until)
    return [DeploymentRead.model_validate(row) for row in rows]


async def get_previous_incidents(
    db: AsyncSession, args: PreviousIncidentsArgs
) -> list[PreviousIncidentRead]:
    rows = await incidents.list_previous_incidents(
        db, args.service, before=args.before, exclude_id=args.exclude_incident_id, limit=args.limit
    )
    return [
        PreviousIncidentRead(
            reference=incident.reference,
            title=incident.title,
            severity=incident.severity,
            status=incident.status,
            created_at=incident.created_at,
            root_cause=root_cause,
        )
        for incident, root_cause in rows
    ]


for _spec in (
    ToolSpec(
        "get_application_logs",
        ToolPermission.READ_ONLY,
        "Log entries of one service in a bounded time window (max 60 min, max 50 entries).",
        LogsArgs,
        get_application_logs,
    ),
    ToolSpec(
        "get_service_health",
        ToolPermission.READ_ONLY,
        "Latest health snapshot of one service, optionally as of a time or with a status.",
        HealthArgs,
        get_service_health,
    ),
    ToolSpec(
        "get_recent_deployments",
        ToolPermission.READ_ONLY,
        "Most recent deployments of one service (max 5).",
        DeploymentsArgs,
        get_recent_deployments,
    ),
    ToolSpec(
        "get_previous_incidents",
        ToolPermission.READ_ONLY,
        "Earlier incidents of one service (max 5).",
        PreviousIncidentsArgs,
        get_previous_incidents,
    ),
    ToolSpec(
        "rollback_deployment",
        ToolPermission.REQUIRES_HUMAN_APPROVAL,
        "Roll back a deployment. Executed by the backend after human approval (later phase).",
    ),
    ToolSpec(
        "restart_service",
        ToolPermission.REQUIRES_HUMAN_APPROVAL,
        "Restart a service. Executed by the backend after human approval (later phase).",
    ),
):
    registry.register(_spec)
