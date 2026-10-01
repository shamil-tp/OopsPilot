"""Simulated environment telemetry: application logs, deployments, and health snapshots."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DeploymentStatus, LogLevel, ServiceStatus, enum_column


class LogEntry(Base):
    __tablename__ = "logs"
    __table_args__ = (Index("ix_logs_service_name_timestamp", "service_name", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    service_name: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    level: Mapped[LogLevel] = mapped_column(enum_column(LogLevel))
    message: Mapped[str] = mapped_column(Text)
    # `metadata` is reserved on declarative classes, so the attribute is `meta`.
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (Index("ix_deployments_service_name_timestamp", "service_name", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    service_name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[DeploymentStatus] = mapped_column(enum_column(DeploymentStatus))
    commit_sha: Mapped[str | None] = mapped_column(String(64))


class ServiceHealth(Base):
    __tablename__ = "service_health"
    __table_args__ = (
        Index("ix_service_health_service_name_timestamp", "service_name", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_name: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ServiceStatus] = mapped_column(enum_column(ServiceStatus))
    error_rate: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    # Null when not measured (e.g. a real application checked over HTTP): never invented.
    cpu_usage: Mapped[float | None] = mapped_column(Float)
    memory_usage: Mapped[float | None] = mapped_column(Float)
