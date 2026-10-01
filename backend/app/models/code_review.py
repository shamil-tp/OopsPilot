"""AI code review of a push to a monitored project (one row per reviewed commit)."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now_column
from app.models.enums import CodeReviewStatus, RiskLevel, enum_column


class CodeReview(Base):
    __tablename__ = "code_reviews"
    __table_args__ = (
        # Redeliveries and repeated pushes of the same commit never review it twice.
        UniqueConstraint("repository", "commit_sha"),
        Index("ix_code_reviews_service_name_created_at", "service_name", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository: Mapped[str] = mapped_column(String(140))
    service_name: Mapped[str] = mapped_column(String(64))
    cicd_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("cicd_events.id", ondelete="SET NULL")
    )
    commit_sha: Mapped[str] = mapped_column(String(40))
    base_sha: Mapped[str | None] = mapped_column(String(40))
    branch: Mapped[str | None] = mapped_column(String(255))
    commit_message: Mapped[str | None] = mapped_column(String(200))
    author: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[CodeReviewStatus] = mapped_column(
        enum_column(CodeReviewStatus), default=CodeReviewStatus.PENDING
    )
    risk: Mapped[RiskLevel | None] = mapped_column(enum_column(RiskLevel))
    summary: Mapped[str | None] = mapped_column(Text)
    # Validated findings: [{severity, category, file, line, title, explanation, recommendation}]
    findings: Mapped[list[Any]] = mapped_column(default=list)
    # Reviewed files with their change stats, and files left out (with the reason).
    files: Mapped[list[Any]] = mapped_column(default=list)
    skipped_files: Mapped[list[Any]] = mapped_column(default=list)
    truncated: Mapped[bool] = mapped_column(default=False)
    model: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = utc_now_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
