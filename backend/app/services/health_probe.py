"""Health checks of the monitored real applications (MONITORED_PROJECTS / MONITORED_SERVICE).

Every MONITORED_HEALTH_INTERVAL_SECONDS the backend sends one GET to each project's URL (no
credentials, no cookies, 10 s timeout, body not downloaded) and stores one `service_health` row
with only what it measured:

- status: HEALTHY (2xx/3xx within MONITORED_LATENCY_SLO_MS), DEGRADED (4xx, or slower than the
  SLO), DOWN (5xx, timeout or connection error)
- latency_ms: time until the response headers arrived
- error_rate: percentage of the last 10 checks (including this one) that were not HEALTHY
- cpu_usage / memory_usage: null; they cannot be measured from outside the application

The URL comes only from configuration: neither API input nor AI output can change what is
requested. This is the only outbound HTTP request the backend makes on its own.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import ServiceHealth
from app.models.enums import ServiceStatus
from app.services.service_catalog import MonitoredService, real_services

logger = get_logger(__name__)

HISTORY = 10
RETENTION = timedelta(days=7)
TIMEOUT_SECONDS = 10.0
USER_AGENT = "OpsPilot-health-check/1.0"


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


async def check(
    db: AsyncSession,
    service: MonitoredService,
    *,
    client: httpx.AsyncClient,
    slo_ms: float,
    now: datetime | None = None,
) -> ServiceHealth:
    """Run one health check of `service` and store the result."""
    assert service.url, "only services with a URL can be checked"
    started = time.perf_counter()
    try:
        async with client.stream("GET", service.url) as response:
            code = response.status_code
        latency = (time.perf_counter() - started) * 1000
        if code >= 500:
            status = ServiceStatus.DOWN
        elif code >= 400 or latency > slo_ms:
            status = ServiceStatus.DEGRADED
        else:
            status = ServiceStatus.HEALTHY
        outcome = f"HTTP {code}"
    except httpx.HTTPError as exc:
        latency = (time.perf_counter() - started) * 1000
        status = ServiceStatus.DOWN
        outcome = type(exc).__name__

    at = now or datetime.now(UTC)
    previous = await db.scalars(
        select(ServiceHealth.status)
        .where(ServiceHealth.service_name == service.name)
        .order_by(ServiceHealth.timestamp.desc(), ServiceHealth.id.desc())
        .limit(HISTORY - 1)
    )
    recent = [status, *previous]
    unhealthy = sum(s is not ServiceStatus.HEALTHY for s in recent)
    row = ServiceHealth(
        service_name=service.name,
        timestamp=at,
        status=status,
        error_rate=round(100 * unhealthy / len(recent), 1),
        latency_ms=round(latency, 1),
        cpu_usage=None,
        memory_usage=None,
    )
    db.add(row)
    await db.execute(
        delete(ServiceHealth).where(
            ServiceHealth.service_name == service.name, ServiceHealth.timestamp < at - RETENTION
        )
    )
    await db.commit()
    logger.info(
        "health_check",
        extra={
            "service": service.name,
            "status": status,
            "outcome": outcome,
            "latency_ms": row.latency_ms,
        },
    )
    return row


async def run_forever(session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
    """Check every monitored service with a URL until cancelled (started by the app lifespan)."""
    settings = get_settings()
    logger.info(
        "health_checks_started",
        extra={
            "interval_s": settings.monitored_health_interval_seconds,
        },
    )
    async with new_client() as client:
        while True:
            targets = [service for service in real_services() if service.url]
            for service in targets:  # one failing project never blocks the others
                try:
                    async with session_factory() as db:
                        await check(
                            db, service, client=client, slo_ms=settings.monitored_latency_slo_ms
                        )
                        # Imported here: the agents depend on services, not the other way round.
                        from app.agents import incident_manager

                        await incident_manager.after_check(db, service)
                except Exception as exc:  # never take the API down; try again next interval
                    logger.warning(
                        "health_check_failed",
                        extra={"service": service.name, "error": type(exc).__name__},
                    )
            await asyncio.sleep(settings.monitored_health_interval_seconds)
