import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models import MonitoredProject, ServiceHealth
from app.models.enums import LogLevel
from app.schemas.common import ErrorResponse
from app.schemas.services import (
    DeploymentRead,
    LogEntryRead,
    ProjectCreate,
    ProjectCreateResponse,
    ProjectRead,
    ProjectsRead,
    ServiceHealthRead,
    ServiceSummary,
    WebhookInstructions,
)
from app.services import telemetry
from app.services.service_catalog import (
    DEMO_SERVICE_NAMES,
    MonitoredService,
    add_db_service,
    get_service,
    real_services,
    remove_db_service,
    service_names,
    services,
)

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


def _clean_repository(repo: str) -> str:
    cleaned = repo.strip().rstrip("/")
    if "github.com/" in cleaned:
        cleaned = cleaned.split("github.com/", 1)[1]
    elif "github.com:" in cleaned:
        cleaned = cleaned.split("github.com:", 1)[1]
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned


@router.post(
    "/projects",
    response_model=ProjectCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new monitored project",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid project configuration"},
        409: {"model": ErrorResponse, "description": "Project or service identifier already exists"},
    },
)
async def create_project(
    data: ProjectCreate, db: DbSession, request: Request
) -> ProjectCreateResponse:
    repo = _clean_repository(data.repository)
    if "/" not in repo or len(repo.split("/")) != 2:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Repository must be in the format 'owner/repo' (e.g. 'MrNihalT/mallutyping')",
        )

    raw_service = data.service or data.name
    service_slug = re.sub(r"[^a-z0-9\-]+", "-", raw_service.lower()).strip("-")
    if not service_slug:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid service slug")

    if service_slug in DEMO_SERVICE_NAMES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Service identifier '{service_slug}' is reserved for the demo simulation",
        )

    existing = await db.scalar(
        select(MonitoredProject).where(MonitoredProject.service == service_slug)
    )
    if existing or service_slug in service_names():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A project with service identifier '{service_slug}' already exists",
        )

    clean_url = data.url.strip() if data.url and data.url.strip() else None

    project = MonitoredProject(
        name=data.name.strip(),
        service=service_slug,
        url=clean_url,
        repository=repo,
        environment=data.environment.strip() or "production",
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    add_db_service(
        MonitoredService(
            name=project.service,
            display_name=project.name,
            description=f"{project.name} ({project.environment})",
            kind="real",
            url=project.url,
            repository=project.repository,
            environment=project.environment,
        )
    )

    settings = get_settings()
    base = str(request.base_url).rstrip("/")
    payload_url = f"{base}/api/webhooks/github"

    webhook = WebhookInstructions(
        payload_url=payload_url,
        content_type="application/json",
        secret_configured=bool(settings.github_webhook_secret and settings.github_webhook_secret.get_secret_value()),
        events=["push", "deployment_status"],
        github_setup_url=f"https://github.com/{repo}/settings/hooks/new",
    )

    return ProjectCreateResponse(
        project=ProjectRead(
            name=project.name,
            environment=project.environment,
            service=project.service,
            url=project.url,
            repository=project.repository,
        ),
        webhook=webhook,
    )


@router.delete(
    "/projects/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dynamically registered project",
    responses={
        404: {"model": ErrorResponse, "description": "Project not found or configured in .env"},
    },
)
async def delete_project(name: str, db: DbSession) -> None:
    project = await db.scalar(select(MonitoredProject).where(MonitoredProject.service == name))
    if project is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Project '{name}' was not found in registered projects (built-in or .env projects cannot be deleted via API)",
        )
    await db.delete(project)
    await db.commit()
    remove_db_service(name)

