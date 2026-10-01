"""Async database engine and session management."""

import time
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()
_database_url = _settings.database_url
# Fail fast (instead of asyncpg's 60s default) so health checks report an outage promptly.
_connect_args: dict[str, Any] = {"timeout": 5} if _database_url.startswith("postgresql") else {}

# A small pool: the Supabase pooler's connection limit is shared by every backend on the database.
# Requests beyond the pool wait for a free connection instead of failing at the pooler.
engine: AsyncEngine = create_async_engine(
    _database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    connect_args=_connect_args,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

if engine.dialect.name == "sqlite":
    # SQLite (used in tests) ignores ON DELETE CASCADE unless foreign keys are enabled.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        yield session


async def check_database(session: AsyncSession) -> float:
    """Run a trivial query and return its round-trip latency in milliseconds."""
    started = time.perf_counter()
    await session.execute(text("SELECT 1"))
    return round((time.perf_counter() - started) * 1000, 2)
