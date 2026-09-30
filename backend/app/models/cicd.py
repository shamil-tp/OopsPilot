"""CI/CD telemetry received from GitHub webhooks, normalized (never the raw payload)."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now_column
from app.models.enums import CicdCategory, CicdConclusion, CicdStatus, enum_column


class CicdEvent(Base):
    __tablename__ = "cicd_events"
    __table_args__ = (
        Index("ix_cicd_events_service_name_occurred_at", "service_name", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), default="github")
    # X-GitHub-Delivery: GitHub reuses it when it redelivers, so it makes ingestion idempotent.
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True)
    event_type: Mapped[str] = mapped_column(String(32))
    category: Mapped[CicdCategory] = mapped_column(enum_column(CicdCategory))
    repository: Mapped[str] = mapped_column(String(140), index=True)
    branch: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(40), index=True)
    commit_message: Mapped[str | None] = mapped_column(String(200))
    actor: Mapped[str | None] = mapped_column(String(100))
    workflow_name: Mapped[str | None] = mapped_column(String(100))
    workflow_run_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    run_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[CicdStatus] = mapped_column(enum_column(CicdStatus))
    conclusion: Mapped[CicdConclusion | None] = mapped_column(enum_column(CicdConclusion))
    # Resolved by the backend (see app.github.normalize); null when it cannot be determined.
    service_name: Mapped[str | None] = mapped_column(String(64))
    environment: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(32))
    version_source: Mapped[str | None] = mapped_column(String(32))
    html_url: Mapped[str | None] = mapped_column(String(300))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = utc_now_column()
    # The deployment this event created or was correlated with.
    deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="SET NULL")
    )
    # Set only on the event that created a deployment: one deployment per workflow run attempt,
    # enforced by the database even if two different deliveries race.
    deployment_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    # Small, sanitized extras (e.g. changed file paths); never the raw payload.
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
