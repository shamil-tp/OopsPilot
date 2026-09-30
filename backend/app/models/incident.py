from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now_column
from app.models.enums import IncidentStatus, Severity, enum_column

if TYPE_CHECKING:
    from app.models.agent import AgentEvent, AgentRun
    from app.models.approval import Approval
    from app.models.report import IncidentReport


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[Severity] = mapped_column(enum_column(Severity))
    status: Mapped[IncidentStatus] = mapped_column(
        enum_column(IncidentStatus), default=IncidentStatus.DETECTED, index=True
    )
    service_name: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = utc_now_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", passive_deletes=True
    )
    events: Mapped[list[AgentEvent]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", passive_deletes=True
    )
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", passive_deletes=True
    )
    report: Mapped[IncidentReport | None] = relationship(
        back_populates="incident", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def reference(self) -> str:
        """Human-friendly identifier, e.g. INC-001."""
        return f"INC-{self.id:03d}"
