from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models import ServiceHealth
from app.models.enums import LogLevel
from app.schemas.common import ErrorResponse
from app.schemas.services import (
    DeploymentRead,
    LogEntryRead,
    ProjectRead,
    ProjectsRead,
    ServiceHealthRead,
    ServiceSummary,
)
from app.services import telemetry
from app.services.service_catalog import MonitoredService, get_service, real_services, services

router = APIRouter(prefix="/services", tags=["services"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
_UNKNOWN_SERVICE = {404: {"model": ErrorResponse, "description": "Unknown service"}}


def known_service(name: str) -> MonitoredService:
    service = get_service(name)
    if service is None:
        known = ", ".join(s.name for s in services())
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown service '{name}'. Known services: {known}"
        )
    return service


Service = Annotated[MonitoredService, Depends(known_service)]


@router.get("", response_model=list[ServiceSummary], summary="List monitored services (real first)")
async def list_services(db: DbSession) -> list[ServiceSummary]:
    latest = await telemetry.latest_health_by_service(db)
    summaries = []
    for service in services():
        health = latest.get(service.name)
        summaries.append(
            ServiceSummary(
                name=service.name,
                display_name=service.display_name,
                description=service.description,
                dependencies=list(service.dependencies),
                kind=service.kind,
                url=service.url,
                status=health.status if health else None,
                last_health_at=health.timestamp if health else None,
            )
        )
    return summaries


@router.get(
    "/{name}/health/history",
    response_model=list[ServiceHealthRead],
    summary="Recent health checks of a service, newest first",
    responses=_UNKNOWN_SERVICE,
)
async def service_health_history(
    service: Service, db: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[ServiceHealthRead]:
    rows = await db.scalars(
        select(ServiceHealth)
        .where(ServiceHealth.service_name == service.name)
        .order_by(ServiceHealth.timestamp.desc(), ServiceHealth.id.desc())
        .limit(limit)
    )
    return [ServiceHealthRead.model_validate(row) for row in rows]


@router.get(
    "/{name}/health",
    response_model=ServiceHealthRead,
    summary="Latest persisted health snapshot of a service",
    responses={
        404: {"model": ErrorResponse, "description": "Unknown service, or no health recorded"}
    },
)
async def service_health(service: Service, db: DbSession) -> ServiceHealthRead:
    health = await telemetry.latest_health(db, service.name)
    if health is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No health data recorded for '{service.name}'. "
            "Run POST /api/demo/reset or POST /api/incidents/simulate first.",
        )
    return ServiceHealthRead.model_validate(health)


@router.get(
    "/{name}/logs",
    response_model=list[LogEntryRead],
    summary="Service logs, oldest first",
    description=(
        "Returns the newest `limit` matching entries in chronological order (oldest first). "
        "Optionally filter by level (repeatable, e.g. `?level=ERROR&level=WARN`) and time window."
    ),
    responses=_UNKNOWN_SERVICE,
)
async def service_logs(
    service: Service,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    level: Annotated[list[LogLevel] | None, Query()] = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[LogEntryRead]:
    rows = await telemetry.list_logs(
        db, service.name, limit=limit, levels=level, since=since, until=until
    )
    return [LogEntryRead.model_validate(row) for row in rows]


@router.get(
    "/{name}/deployments",
    response_model=list[DeploymentRead],
    summary="Service deployment history, newest first",
    responses=_UNKNOWN_SERVICE,
)
async def service_deployments(
    service: Service,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[DeploymentRead]:
    rows = await telemetry.list_deployments(db, service.name, limit=limit)
    return [DeploymentRead.model_validate(row) for row in rows]


@router.get(
    "/projects", response_model=ProjectsRead, summary="The monitored projects (configuration)"
)
async def list_projects() -> ProjectsRead:
    settings = get_settings()
    return ProjectsRead(
        projects=[
            ProjectRead(
                name=service.display_name,
                environment=service.environment,
                service=service.name,
                url=service.url,
                repository=service.repository,
            )
            for service in real_services()
        ],
        demo_mode=settings.demo_mode,
        health_check_interval_seconds=settings.monitored_health_interval_seconds,
    )
