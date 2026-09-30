"""The simulated production environment: the services OpsPilot monitors and investigates."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SimulatedService:
    name: str
    display_name: str
    description: str
    dependencies: tuple[str, ...] = ()


SERVICES: tuple[SimulatedService, ...] = (
    SimulatedService(
        name="payment-api",
        display_name="Payment API",
        description="Processes customer payments (POST /payment).",
        dependencies=("database", "auth-api"),
    ),
    SimulatedService(
        name="auth-api",
        display_name="Auth API",
        description="Issues and validates access tokens.",
        dependencies=("database",),
    ),
    SimulatedService(
        name="database",
        display_name="Database",
        description="PostgreSQL cluster backing payment-api and auth-api.",
    ),
)

SERVICE_NAMES: frozenset[str] = frozenset(service.name for service in SERVICES)

_BY_NAME = {service.name: service for service in SERVICES}


def get_service(name: str) -> SimulatedService | None:
    return _BY_NAME.get(name)
