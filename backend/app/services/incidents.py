"""Incident queries."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Incident, IncidentReport


async def list_incidents(db: AsyncSession, *, limit: int, offset: int = 0) -> list[Incident]:
    """Newest first."""
    rows = await db.scalars(
        select(Incident)
        .order_by(Incident.created_at.desc(), Incident.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows)


async def get_incident(db: AsyncSession, incident_id: int) -> Incident | None:
    return await db.get(Incident, incident_id)


async def list_previous_incidents(
    db: AsyncSession, service_name: str, *, before: datetime, exclude_id: int, limit: int
) -> list[tuple[Incident, str | None]]:
    """Earlier incidents of a service, newest first, with their report's root cause if any."""
    rows = await db.execute(
        select(Incident, IncidentReport.root_cause)
        .outerjoin(IncidentReport, IncidentReport.incident_id == Incident.id)
        .where(
            Incident.service_name == service_name,
            Incident.id != exclude_id,
            Incident.created_at <= before,
        )
        .order_by(Incident.created_at.desc(), Incident.id.desc())
        .limit(limit)
    )
    return [(incident, root_cause) for incident, root_cause in rows.all()]
