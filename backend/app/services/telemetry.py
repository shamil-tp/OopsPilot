"""Read access to persisted telemetry (logs, deployments, health) for the simulated services.

These queries back the REST API now and the agents' read-only tools later. Each one is served by
the `(service_name, timestamp)` composite index on its table.
"""

from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deployment, LogEntry, ServiceHealth
from app.models.enums import LogLevel, ServiceStatus


async def latest_health(
    db: AsyncSession,
    service_name: str,
    *,
    until: datetime | None = None,
    status: ServiceStatus | None = None,
) -> ServiceHealth | None:
    """Newest snapshot, optionally as of `until` and/or with a given status."""
    stmt = select(ServiceHealth).where(ServiceHealth.service_name == service_name)
    if until is not None:
        stmt = stmt.where(ServiceHealth.timestamp <= until)
    if status is not None:
        stmt = stmt.where(ServiceHealth.status == status)
    return await db.scalar(
        stmt.order_by(ServiceHealth.timestamp.desc(), ServiceHealth.id.desc()).limit(1)
    )


async def latest_health_by_service(db: AsyncSession) -> dict[str, ServiceHealth]:
    """Most recent health snapshot of every service, in a single query."""
    newest = (
        select(ServiceHealth.service_name, func.max(ServiceHealth.timestamp).label("timestamp"))
        .group_by(ServiceHealth.service_name)
        .subquery()
    )
    rows = await db.scalars(
        select(ServiceHealth)
        .join(
            newest,
            and_(
                ServiceHealth.service_name == newest.c.service_name,
                ServiceHealth.timestamp == newest.c.timestamp,
            ),
        )
        .order_by(ServiceHealth.id)
    )
    return {row.service_name: row for row in rows}


async def list_logs(
    db: AsyncSession,
    service_name: str,
    *,
    limit: int,
    levels: list[LogLevel] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[LogEntry]:
    """The newest `limit` matching entries, returned oldest first (chronological)."""
    stmt = select(LogEntry).where(LogEntry.service_name == service_name)
    if levels:
        stmt = stmt.where(LogEntry.level.in_(levels))
    if since is not None:
        stmt = stmt.where(LogEntry.timestamp >= since)
    if until is not None:
        stmt = stmt.where(LogEntry.timestamp <= until)
    newest_first = await db.scalars(
        stmt.order_by(LogEntry.timestamp.desc(), LogEntry.id.desc()).limit(limit)
    )
    return list(reversed(newest_first.all()))


async def list_deployments(
    db: AsyncSession, service_name: str, *, limit: int, until: datetime | None = None
) -> list[Deployment]:
    """Deployment history, newest first (optionally only those at or before `until`)."""
    stmt = select(Deployment).where(Deployment.service_name == service_name)
    if until is not None:
        stmt = stmt.where(Deployment.timestamp <= until)
    rows = await db.scalars(
        stmt.order_by(Deployment.timestamp.desc(), Deployment.id.desc()).limit(limit)
    )
    return list(rows)
