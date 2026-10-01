"""The services OpsPilot monitors and investigates.

Two telemetry sources feed the same tables and the same agents:
- the **real** application configured with MONITORED_SERVICE (health measured by the backend's
  health checks, deployments from GitHub);
- the **demo** simulation (payment-api, auth-api, database), available while DEMO_MODE is on and
  always used by the automated tests.

The catalog is read at call time from the settings, so every validator (tools, API filters,
webhook service mapping) accepts exactly the services that are currently configured.
"""

from dataclasses import dataclass
from typing import Literal

from app.core.config import get_settings


@dataclass(frozen=True)
class MonitoredService:
    name: str
    display_name: str
    description: str
    dependencies: tuple[str, ...] = ()
    kind: Literal["real", "demo"] = "demo"
    url: str | None = None


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


def real_service() -> MonitoredService | None:
    settings = get_settings()
    name = settings.monitored_service
    if not name or name in DEMO_SERVICE_NAMES:
        return None
    project = settings.monitored_project_name or name
    return MonitoredService(
        name=name,
        display_name=project,
        description=f"{project} ({settings.monitored_environment})",
        kind="real",
        url=settings.monitored_service_url,
    )


def services() -> tuple[MonitoredService, ...]:
    """The real service first (if configured), then the demo services (if DEMO_MODE is on)."""
    real = real_service()
    demo = DEMO_SERVICES if get_settings().demo_mode else ()
    return ((real,) if real else ()) + demo


def service_names() -> frozenset[str]:
    return frozenset(service.name for service in services())


def get_service(name: str) -> MonitoredService | None:
    return next((service for service in services() if service.name == name), None)


def is_demo_service(name: str) -> bool:
    return name in DEMO_SERVICE_NAMES
