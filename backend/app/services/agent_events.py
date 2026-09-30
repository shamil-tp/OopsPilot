"""Agent timeline events: recorded by agents, listed by the API (and streamed in a later phase)."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentEvent
from app.models.enums import AgentName


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(v) for v in value]
    return value


class EventRecorder:
    """Adds events for one incident/agent to the session; the caller decides when to commit."""

    def __init__(self, db: AsyncSession, incident_id: int, agent: AgentName | None) -> None:
        self._db = db
        self._incident_id = incident_id
        self._agent = agent

    def emit(self, event_type: str, message: str, **meta: Any) -> AgentEvent:
        event = AgentEvent(
            incident_id=self._incident_id,
            agent_name=self._agent,
            event_type=event_type,
            message=message,
            meta=json_safe(meta),
            # Set here (not by the database) so events in one transaction keep their order.
            timestamp=datetime.now(UTC),
        )
        self._db.add(event)
        return event


async def list_events(db: AsyncSession, incident_id: int) -> list[AgentEvent]:
    """Chronological (oldest first)."""
    rows = await db.scalars(
        select(AgentEvent)
        .where(AgentEvent.incident_id == incident_id)
        .order_by(AgentEvent.timestamp, AgentEvent.id)
    )
    return list(rows)
