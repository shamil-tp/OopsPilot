"""Unit tests of the deterministic scenario data (no database)."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from app.models import LogEntry
from app.models.enums import LogLevel, ServiceStatus, Severity
from app.services.scenario import (
    PRIMARY_SERVICE,
    Environment,
    build_environment,
    classify_severity,
)
from app.services.service_catalog import SERVICE_NAMES

ANCHOR = datetime(2026, 9, 30, 11, 44, tzinfo=UTC)


def _first(environment: Environment, predicate: Callable[[LogEntry], bool]) -> datetime:
    return next(log.timestamp for log in environment.logs if predicate(log))


def test_incident_timeline_is_causally_ordered() -> None:
    env = build_environment(ANCHOR, incident=True)
    payment = [log for log in env.logs if log.service_name == PRIMARY_SERVICE]

    deploy_started = _first(env, lambda log: log.message == "Deployment v1.8.2 started")
    deploy_done = _first(env, lambda log: log.message == "Deployment v1.8.2 completed")
    first_db_error = _first(env, lambda log: log.message.startswith("Database connection"))
    first_500 = _first(env, lambda log: log.message == "POST /payment 500")
    first_degraded = min(
        h.timestamp
        for h in env.health
        if h.service_name == PRIMARY_SERVICE and h.status is ServiceStatus.DEGRADED
    )

    assert deploy_started < deploy_done < first_db_error < first_500 < first_degraded <= ANCHOR
    assert deploy_started.strftime("%H:%M") == "11:41"
    assert first_db_error.strftime("%H:%M") == "11:42"
    # No payment-api error before the deployment: the failure starts after v1.8.2.
    assert all(log.timestamp > deploy_started for log in payment if log.level is LogLevel.ERROR)
    assert [log.timestamp for log in env.logs] == sorted(log.timestamp for log in env.logs)


def test_incident_ends_degraded_and_v182_is_latest_deployment() -> None:
    env = build_environment(ANCHOR, incident=True)
    latest = max(
        (h for h in env.health if h.service_name == PRIMARY_SERVICE), key=lambda h: h.timestamp
    )
    versions = [
        d.version
        for d in sorted(env.deployments, key=lambda d: d.timestamp)
        if d.service_name == PRIMARY_SERVICE
    ]

    assert (latest.status, latest.error_rate, latest.latency_ms) == (
        ServiceStatus.DEGRADED,
        37.0,
        2800,
    )
    assert (latest.cpu_usage, latest.memory_usage) == (43, 68)
    assert versions == ["v1.8.0", "v1.8.1", "v1.8.2"]


def test_healthy_environment_has_no_errors_or_v182() -> None:
    env = build_environment(ANCHOR, incident=False)

    assert not [log for log in env.logs if log.level is LogLevel.ERROR]
    assert "v1.8.2" not in {d.version for d in env.deployments}
    assert all(h.status is ServiceStatus.HEALTHY for h in env.health)


def test_scenario_is_deterministic_and_covers_every_service() -> None:
    def snapshot(env: Environment) -> list[tuple[Any, ...]]:
        return [(r.timestamp, r.service_name, getattr(r, "message", None)) for r in env.records()]

    first, second = (build_environment(ANCHOR, incident=True) for _ in range(2))

    assert snapshot(first) == snapshot(second)
    assert {log.service_name for log in first.logs} == SERVICE_NAMES
    assert {h.service_name for h in first.health} == SERVICE_NAMES


def test_telemetry_never_states_the_root_cause() -> None:
    env = build_environment(ANCHOR, incident=True)
    text = " ".join(f"{log.message} {log.meta}" for log in env.logs).lower()

    assert "root cause" not in text
    assert "caused" not in text


@pytest.mark.parametrize(
    ("status", "error_rate", "expected"),
    [
        (ServiceStatus.HEALTHY, 0.2, Severity.LOW),
        (ServiceStatus.DEGRADED, 8, Severity.MEDIUM),
        (ServiceStatus.DEGRADED, 37, Severity.HIGH),
        (ServiceStatus.DEGRADED, 60, Severity.CRITICAL),
        (ServiceStatus.DOWN, 1, Severity.CRITICAL),
    ],
)
def test_severity_rule(status: ServiceStatus, error_rate: float, expected: Severity) -> None:
    assert classify_severity(status, error_rate) is expected
