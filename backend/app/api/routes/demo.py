from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.demo import DemoResetResponse
from app.services import simulator

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post(
    "/reset",
    response_model=DemoResetResponse,
    summary="Reset the demo environment",
    description=(
        "Deletes all incidents (with their agent runs, events, approvals and reports) and the "
        "simulated services' logs, deployments and health, then seeds a healthy environment "
        "running payment-api v1.8.1. Never drops or truncates tables."
    ),
)
async def reset_demo(db: Annotated[AsyncSession, Depends(get_db)]) -> DemoResetResponse:
    result = await simulator.reset_demo(db)
    return DemoResetResponse(
        message="Demo reset: all services healthy, no incidents.",
        incidents_deleted=result.incidents_deleted,
        logs_deleted=result.logs_deleted,
        deployments_deleted=result.deployments_deleted,
        health_records_deleted=result.health_records_deleted,
    )
