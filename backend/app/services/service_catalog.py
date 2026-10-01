"""The services OpsPilot monitors and investigates.

Two telemetry sources feed the same tables and the same agents:
- the **real** applications configured in MONITORED_PROJECTS (health measured by the backend's
  health checks, deployments and CI from each project's GitHub repository);
- the **demo** simulation (payment-api, auth-api, database), available while DEMO_MODE is on and
  always used by the automated tests.

The catalog is read at call time from the settings, so every validator (tools, API filters,
webhook service mapping) accepts exactly the services that are currently configured.
"""

from dataclasses import dataclass
from typing import Literal

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class MonitoredService:
    name: str
    display_name: str
    description: str
    dependencies: tuple[str, ...] = ()
    kind: Literal["real", "demo"] = "demo"
    url: str | None = None
    repository: str | None = None
    environment: str = "production"


DEMO_SERVICES: tuple[MonitoredService, ...] = (
    MonitoredService(
        name="payment-api",
        display_name="Payment API",
        description="Processes customer payments (POST /payment).",
        dependencies=("database", "auth-api"),
    ),
    MonitoredService(
        name="auth-api",
        display_name="Auth API",
        description="Issues and validates access tokens.",
        dependencies=("database",),
    ),
    MonitoredService(
        name="database",
        display_name="Database",
        description="PostgreSQL cluster backing payment-api and auth-api.",
    ),
)

DEMO_SERVICE_NAMES: frozenset[str] = frozenset(service.name for service in DEMO_SERVICES)

_db_services: dict[str, MonitoredService] = {}


def set_db_services(services_list: list[MonitoredService]) -> None:
    global _db_services
    _db_services = {s.name: s for s in services_list}


def add_db_service(service: MonitoredService) -> None:
    _db_services[service.name] = service


def remove_db_service(name: str) -> None:
    _db_services.pop(name, None)


def real_services() -> tuple[MonitoredService, ...]:
    """Every real application: MONITORED_PROJECTS in .env plus projects registered in the DB."""
    env_projects = {
        project.service: MonitoredService(
            name=project.service,
            display_name=project.name,
            description=f"{project.name} ({project.environment})",
            kind="real",
            url=project.url,
            repository=project.repository,
            environment=project.environment,
        )
        for project in get_settings().projects
        if project.service not in DEMO_SERVICE_NAMES
    }
    combined = {**env_projects, **_db_services}
    return tuple(combined.values())


def services() -> tuple[MonitoredService, ...]:
    """The real services first (if configured), then the demo services (if DEMO_MODE is on)."""
    demo = DEMO_SERVICES if get_settings().demo_mode else ()
    return real_services() + demo


def service_names() -> frozenset[str]:
    return frozenset(service.name for service in services())


def get_service(name: str) -> MonitoredService | None:
    return next((service for service in services() if service.name == name), None)


def is_demo_service(name: str) -> bool:
    return name in DEMO_SERVICE_NAMES


async def sync_from_db(session_factory=None) -> None:
    """Populate the in-memory service catalog with projects from the database."""
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.monitored_project import MonitoredProject

    factory = session_factory or SessionLocal
    try:
        async with factory() as db:
            rows = await db.scalars(select(MonitoredProject).order_by(MonitoredProject.id.asc()))
            set_db_services(
                [
                    MonitoredService(
                        name=p.service,
                        display_name=p.name,
                        description=f"{p.name} ({p.environment})",
                        kind="real",
                        url=p.url,
                        repository=p.repository,
                        environment=p.environment,
                    )
                    for p in rows
                    if p.service not in DEMO_SERVICE_NAMES
                ]
            )
    except Exception as exc:
        logger.warning("sync_projects_failed", extra={"error": type(exc).__name__})
