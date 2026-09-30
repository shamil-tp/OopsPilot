"""Read access to normalized CI/CD telemetry (bounded filters only; no free-form queries)."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import CicdEvent
from app.models.enums import CicdCategory, CicdConclusion, CicdStatus
from app.schemas.cicd import CicdEventRead
from app.schemas.common import ErrorResponse
from app.services import cicd
from app.services.service_catalog import SERVICE_NAMES

router = APIRouter(prefix="/cicd", tags=["cicd"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/events",
    response_model=list[CicdEventRead],
    summary="Recent CI/CD events (newest first)",
    responses={422: {"model": ErrorResponse, "description": "Invalid filter"}},
)
async def list_cicd_events(
    db: DbSession,
    repository: Annotated[
        str | None, Query(max_length=140, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    ] = None,
    branch: Annotated[str | None, Query(max_length=255)] = None,
    commit_sha: Annotated[str | None, Query(pattern=r"^[0-9a-fA-F]{4,40}$")] = None,
    workflow: Annotated[str | None, Query(max_length=100)] = None,
    service: Annotated[str | None, Query(max_length=64)] = None,
    category: CicdCategory | None = None,
    event_status: Annotated[CicdStatus | None, Query(alias="status")] = None,
    conclusion: CicdConclusion | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[CicdEventRead]:
    if service is not None and service not in SERVICE_NAMES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown service '{service}'")
    rows = await cicd.list_events(
        db,
        cicd.CicdFilter(
            repository=repository,
            branch=branch,
            commit_sha=commit_sha,
            workflow=workflow,
            service=service,
            category=category,
            status=event_status,
            conclusion=conclusion,
            since=since,
            until=until,
        ),
        limit=limit,
    )
    return [CicdEventRead.model_validate(row) for row in rows]


@router.get(
    "/events/{event_id}",
    response_model=CicdEventRead,
    summary="One CI/CD event",
    responses={404: {"model": ErrorResponse, "description": "CI/CD event not found"}},
)
async def get_cicd_event(event_id: int, db: DbSession) -> CicdEventRead:
    row = await db.get(CicdEvent, event_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"CI/CD event {event_id} not found")
    return CicdEventRead.model_validate(row)
