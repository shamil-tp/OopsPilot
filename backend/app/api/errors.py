"""Exception handlers that turn infrastructure failures into safe API errors."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # Log only the exception type: messages can contain connection details.
    logger.error(
        "database_error",
        extra={"error": type(exc).__name__, "method": request.method, "path": request.url.path},
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Database unavailable. Please try again shortly."},
    )


def register_exception_handlers(app: FastAPI) -> None:
    # OSError covers network failures raised by asyncpg (refused connection, DNS, timeout).
    app.add_exception_handler(SQLAlchemyError, _database_unavailable)
    app.add_exception_handler(OSError, _database_unavailable)
