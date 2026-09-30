from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, Text, text
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
    # The exact, backend-validated parameters the human approves (e.g. service, from_version,
    # to_version). Execution uses these, never new values from a client or the model.
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'"))
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_column(ApprovalStatus), default=ApprovalStatus.PENDING
    )
    requested_at: Mapped[datetime] = utc_now_column()
    # When the human decided. Set for both approvals and rejections.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    incident: Mapped[Incident] = relationship(back_populates="approvals")
