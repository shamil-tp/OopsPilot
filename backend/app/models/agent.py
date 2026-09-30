from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now_column
from app.models.enums import AgentName, AgentRunStatus, enum_column

if TYPE_CHECKING:
    from app.models.incident import Incident


class AgentRun(Base):
    """One execution of a logical agent for an incident."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    agent_name: Mapped[AgentName] = mapped_column(enum_column(AgentName))
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_column(AgentRunStatus), default=AgentRunStatus.RUNNING
    )
    started_at: Mapped[datetime] = utc_now_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # User-safe reasoning summary only; never raw chain-of-thought.
    summary: Mapped[str | None] = mapped_column(Text)
    # Validated structured output of the agent (e.g. root cause JSON).
    output: Mapped[dict[str, Any] | None]

    incident: Mapped[Incident] = relationship(back_populates="agent_runs")


class AgentEvent(Base):
    """Timeline event, persisted and also broadcast over the incident WebSocket."""

    __tablename__ = "agent_events"
    __table_args__ = (Index("ix_agent_events_incident_id_timestamp", "incident_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    # Null for system events such as `incident_created`.
    agent_name: Mapped[AgentName | None] = mapped_column(enum_column(AgentName))
    event_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    timestamp: Mapped[datetime] = utc_now_column()

    incident: Mapped[Incident] = relationship(back_populates="events")
