from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import check_database, get_db
from app.schemas.system import AIProviderHealth, DatabaseHealth, SystemHealth

router = APIRouter(prefix="/system", tags=["system"])
logger = get_logger(__name__)


@router.get(
    "/health",
    response_model=SystemHealth,
    responses={503: {"model": SystemHealth, "description": "A dependency is unavailable"}},
)
async def system_health(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SystemHealth:
    try:
        database = DatabaseHealth(status="ok", latency_ms=await check_database(db))
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("database_health_check_failed", extra={"error": type(exc).__name__})
        database = DatabaseHealth(status="unavailable", error=type(exc).__name__)

    health = SystemHealth(
        status="ok" if database.status == "ok" else "degraded",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        database=database,
        ai=AIProviderHealth(
            provider=settings.ai_provider,
            model=settings.gemini_model,
            configured_keys=len(settings.gemini_api_keys),
        ),
    )
    if health.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return health
