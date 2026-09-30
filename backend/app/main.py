from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings, redact_database_url
from app.core.logging import configure_logging, get_logger
from app.db.session import engine

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "startup",
        extra={
            "environment": settings.app_env,
            "database": redact_database_url(settings.database_url),
            "ai_provider": settings.ai_provider,
            "gemini_keys_configured": len(settings.gemini_api_keys),
        },
    )
    yield
    await engine.dispose()
    logger.info("shutdown")


app = FastAPI(
    title="OpsPilot API",
    summary="AI-Powered Autonomous Incident Response & DevOps Copilot",
    version=settings.app_version,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
