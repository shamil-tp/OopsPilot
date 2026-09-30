from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now_column
from app.models.enums import RecoveryStatus, enum_column

if TYPE_CHECKING:
    from app.models.incident import Incident


class IncidentReport(Base):
    __tablename__ = "incident_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), unique=True
    )
    root_cause: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    action_taken: Mapped[str | None] = mapped_column(Text)
    recovery_status: Mapped[RecoveryStatus] = mapped_column(
        enum_column(RecoveryStatus), default=RecoveryStatus.NOT_ATTEMPTED
    )
    # Full structured report (timeline, decision, verification, ...).
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = utc_now_column()

    incident: Mapped[Incident] = relationship(back_populates="report")
