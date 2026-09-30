from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now_column
from app.models.enums import ActionType, ApprovalStatus, RiskLevel, enum_column

if TYPE_CHECKING:
    from app.models.incident import Incident


class Approval(Base):
    """Human approval gate for an action that requires it (e.g. rollback)."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[ActionType] = mapped_column(enum_column(ActionType))
    target: Mapped[str] = mapped_column(String(64))
    risk: Mapped[RiskLevel] = mapped_column(enum_column(RiskLevel))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_column(ApprovalStatus), default=ApprovalStatus.PENDING
    )
    requested_at: Mapped[datetime] = utc_now_column()
    # When the human decided. Set for both approvals and rejections.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    incident: Mapped[Incident] = relationship(back_populates="approvals")
