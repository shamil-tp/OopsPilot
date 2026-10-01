"""Dynamically registered monitored projects (persisted in PostgreSQL)."""

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now_column


class MonitoredProject(Base):
    __tablename__ = "monitored_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    service: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    url: Mapped[str | None] = mapped_column(String(300))
    repository: Mapped[str] = mapped_column(String(140), index=True)
    environment: Mapped[str] = mapped_column(String(32), default="production")
    created_at: Mapped[datetime] = utc_now_column()
