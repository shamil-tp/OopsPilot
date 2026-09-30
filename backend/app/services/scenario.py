"""Deterministic telemetry for the simulated environment.

Everything is defined as offsets (in seconds) from an anchor time, so every run tells the same
story, only shifted to "now". With `anchor` = the moment the incident is detected (11:44):

    -6d / -2d   payment-api v1.8.0 / v1.8.1 deployed (healthy ever since)
    -600..-190  normal traffic, healthy metrics                       (11:34 - 11:40)
    -180        v1.8.2 deployment starts                              (11:41)
    -135        v1.8.2 deployment completes
    -120        database connection failures begin                    (11:42)
    -108        POST /payment starts returning 500
    -60         error rate 37%, latency 2800 ms, status DEGRADED      (11:43)
      0         incident detected                                     (11:44)

The data is evidence only. Nothing here states the root cause: the investigation agents must
infer it from the timing and content of the logs, deployments, and health snapshots. Normal
traffic and a few unrelated warnings are mixed in so relevant evidence has to be picked out.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.models import Deployment, LogEntry, ServiceHealth
from app.models.enums import DeploymentStatus, LogLevel, ServiceStatus, Severity

SCENARIO_ID = "payment-api-db-connection-v1.8.2"
PRIMARY_SERVICE = "payment-api"
INCIDENT_TITLE = "Payment API Production Incident"
INCIDENT_DESCRIPTION = (
    "Payment API is returning HTTP 500 errors with elevated error rate and latency."
)

DAY = 24 * 60 * 60

# Service-level thresholds of the simulated payment-api: the incident's alerts fire above them and
# verification requires the service to be back below them.
ERROR_RATE_THRESHOLD = 5.0  # percent of failed requests
LATENCY_SLO_MS = 500  # p95 latency, ms

# (offset_seconds, service, version, status, commit_sha)
_DeploymentSpec = tuple[int, str, str, DeploymentStatus, str]
# (offset_seconds, service, level, message, metadata)
_LogSpec = tuple[int, str, LogLevel, str, dict[str, Any]]
# (offset_seconds, service, status, error_rate, latency_ms, cpu_usage, memory_usage)
_HealthSpec = tuple[int, str, ServiceStatus, float, float, float, float]

_OK = ServiceStatus.HEALTHY
_DEGRADED = ServiceStatus.DEGRADED
_INFO, _WARN, _ERROR = LogLevel.INFO, LogLevel.WARN, LogLevel.ERROR

# --- Deployment history -------------------------------------------------------------------------

_DEPLOYMENT_HISTORY: tuple[_DeploymentSpec, ...] = (
    (-9 * DAY, "auth-api", "v2.3.0", DeploymentStatus.SUCCEEDED, "5b8e1d4"),
    (-6 * DAY, "payment-api", "v1.8.0", DeploymentStatus.SUCCEEDED, "3c91f7a"),
    (-4 * DAY, "auth-api", "v2.3.1", DeploymentStatus.SUCCEEDED, "c07a9e3"),
    (-2 * DAY, "payment-api", "v1.8.1", DeploymentStatus.SUCCEEDED, "8f2d6b1"),
)
_INCIDENT_DEPLOYMENTS: tuple[_DeploymentSpec, ...] = (
    (-180, "payment-api", "v1.8.2", DeploymentStatus.SUCCEEDED, "e4a7c52"),
)

# --- Logs ---------------------------------------------------------------------------------------

# Normal activity before the deployment window; shared by the healthy and incident timelines.
_BASELINE_LOGS: tuple[_LogSpec, ...] = (
    (-600, "payment-api", _INFO, "Payment request received", {"request_id": "req-1001"}),
    (
        -598,
        "payment-api",
        _INFO,
        "Payment processed successfully",
        {"request_id": "req-1001", "duration_ms": 174},
    ),
    (-590, "database", _INFO, "Checkpoint complete", {"buffers_written": 412}),
    (-560, "auth-api", _INFO, "Access token issued", {"client": "web-checkout"}),
    (-540, "payment-api", _INFO, "GET /health 200", {"status": 200, "duration_ms": 3}),
    (-480, "payment-api", _INFO, "POST /payment 200", {"status": 200, "duration_ms": 181}),
    (-450, "auth-api", _WARN, "Rate limit at 80% for client mobile-app", {"client": "mobile-app"}),
    (
        -420,
        "payment-api",
        _WARN,
        "Slow request: GET /payment/history took 812ms",
        {"duration_ms": 812},
    ),
    (-400, "database", _INFO, "Active connections: 38/200", {"active": 38, "max": 200}),
    (-360, "payment-api", _INFO, "GET /health 200", {"status": 200, "duration_ms": 3}),
    (-330, "auth-api", _INFO, "Access token issued", {"client": "mobile-app"}),
    (
        -300,
        "payment-api",
        _INFO,
        "Payment processed successfully",
        {"request_id": "req-1002", "duration_ms": 177},
    ),
    (-240, "payment-api", _INFO, "POST /payment 200", {"status": 200, "duration_ms": 179}),
    (-200, "database", _INFO, "Active connections: 41/200", {"active": 41, "max": 200}),
    (
        -190,
        "payment-api",
        _INFO,
        "Payment processed successfully",
        {"request_id": "req-1003", "duration_ms": 183},
    ),
)

# The incident: deployment, then database connection errors, then 500s, then threshold alerts.
_INCIDENT_LOGS: tuple[_LogSpec, ...] = (
    (
        -180,
        "payment-api",
        _INFO,
        "Deployment v1.8.2 started",
        {"version": "v1.8.2", "previous_version": "v1.8.1", "commit_sha": "e4a7c52"},
    ),
    (
        -160,
        "payment-api",
        _INFO,
        "Application configuration reloaded",
        {"version": "v1.8.2", "changed_keys": ["database.host", "database.pool_size"]},
    ),
    (
        -135,
        "payment-api",
        _INFO,
        "Deployment v1.8.2 completed",
        {"version": "v1.8.2", "duration_s": 45},
    ),
    (-130, "auth-api", _INFO, "Access token issued", {"client": "web-checkout"}),
    (
        -120,
        "payment-api",
        _ERROR,
        "Database connection failed",
        {
            "error": "ConnectionRefusedError",
            "host": "payments-db.internal",
            "port": 5433,
            "attempt": 1,
        },
    ),
    (
        -115,
        "payment-api",
        _ERROR,
        "Database connection timeout",
        {"timeout_ms": 5000, "attempt": 2},
    ),
    (
        -110,
        "payment-api",
        _ERROR,
        "Failed to acquire database connection from pool",
        {"pool_size": 20, "available": 0},
    ),
    (
        -108,
        "payment-api",
        _ERROR,
        "POST /payment 500",
        {"status": 500, "request_id": "req-1004", "duration_ms": 5012},
    ),
    (
        -100,
        "payment-api",
        _ERROR,
        "POST /payment 500",
        {"status": 500, "request_id": "req-1005", "duration_ms": 5008},
    ),
    (-98, "database", _INFO, "Active connections: 36/200", {"active": 36, "max": 200}),
    (-95, "payment-api", _INFO, "GET /health 200", {"status": 200, "duration_ms": 4}),
    (-90, "payment-api", _ERROR, "Database connection timeout", {"timeout_ms": 5000, "attempt": 1}),
    (
        -80,
        "payment-api",
        _ERROR,
        "POST /payment 500",
        {"status": 500, "request_id": "req-1006", "duration_ms": 5011},
    ),
    (
        -70,
        "payment-api",
        _INFO,
        "Payment processed successfully",
        {"request_id": "req-1007", "duration_ms": 2410, "retried": True},
    ),
    (
        -60,
        "payment-api",
        _WARN,
        f"Error rate 37.0% exceeds threshold {ERROR_RATE_THRESHOLD:.1f}%",
        {"error_rate": 37.0, "threshold": ERROR_RATE_THRESHOLD},
    ),
    (
        -50,
        "payment-api",
        _ERROR,
        "POST /payment 500",
        {"status": 500, "request_id": "req-1008", "duration_ms": 5009},
    ),
    (-45, "auth-api", _INFO, "Access token issued", {"client": "mobile-app"}),
    (
        -40,
        "payment-api",
        _ERROR,
        "Database connection failed",
        {
            "error": "ConnectionRefusedError",
            "host": "payments-db.internal",
            "port": 5433,
            "attempt": 1,
        },
    ),
    (
        -30,
        "payment-api",
        _ERROR,
        "POST /payment 500",
        {"status": 500, "request_id": "req-1009", "duration_ms": 5010},
    ),
    (
        -20,
        "payment-api",
        _WARN,
        f"p95 latency 2800ms exceeds SLO {LATENCY_SLO_MS:g}ms",
        {"p95_ms": 2800, "slo_ms": LATENCY_SLO_MS},
    ),
    (
        -10,
        "payment-api",
        _ERROR,
        "POST /payment 500",
        {"status": 500, "request_id": "req-1010", "duration_ms": 5007},
    ),
)

# The same window with no deployment: what a healthy environment (after a reset) looks like.
_HEALTHY_LOGS: tuple[_LogSpec, ...] = (
    (-150, "payment-api", _INFO, "POST /payment 200", {"status": 200, "duration_ms": 178}),
    (-130, "auth-api", _INFO, "Access token issued", {"client": "web-checkout"}),
    (-98, "database", _INFO, "Active connections: 40/200", {"active": 40, "max": 200}),
    (-95, "payment-api", _INFO, "GET /health 200", {"status": 200, "duration_ms": 3}),
    (
        -70,
        "payment-api",
        _INFO,
        "Payment processed successfully",
        {"request_id": "req-1004", "duration_ms": 176},
    ),
    (-30, "payment-api", _INFO, "POST /payment 200", {"status": 200, "duration_ms": 180}),
)

# --- Health snapshots ---------------------------------------------------------------------------

_BASELINE_HEALTH: tuple[_HealthSpec, ...] = (
    (-600, "payment-api", _OK, 0.2, 180, 43, 68),
    (-480, "payment-api", _OK, 0.3, 176, 43, 68),
    (-360, "payment-api", _OK, 0.2, 183, 43, 68),
    (-240, "payment-api", _OK, 0.2, 179, 43, 68),
    (-600, "auth-api", _OK, 0.1, 95, 35, 52),
    (-300, "auth-api", _OK, 0.1, 97, 35, 52),
    (-600, "database", _OK, 0.0, 4, 31, 57),
    (-300, "database", _OK, 0.0, 4, 31, 57),
)
_INCIDENT_HEALTH: tuple[_HealthSpec, ...] = (
    (-180, "payment-api", _OK, 0.2, 180, 43, 68),
    (-90, "payment-api", _DEGRADED, 18.6, 1650, 43, 68),
    (-60, "payment-api", _DEGRADED, 37.0, 2800, 43, 68),
    (0, "payment-api", _DEGRADED, 37.0, 2800, 43, 68),
    (0, "auth-api", _OK, 0.1, 96, 35, 52),
    (0, "database", _OK, 0.0, 4, 31, 57),
)
_HEALTHY_HEALTH: tuple[_HealthSpec, ...] = (
    (-120, "payment-api", _OK, 0.2, 181, 43, 68),
    (0, "payment-api", _OK, 0.2, 180, 43, 68),
    (0, "auth-api", _OK, 0.1, 96, 35, 52),
    (0, "database", _OK, 0.0, 4, 31, 57),
)


@dataclass
class Environment:
    logs: list[LogEntry] = field(default_factory=list)
    deployments: list[Deployment] = field(default_factory=list)
    health: list[ServiceHealth] = field(default_factory=list)

    def records(self) -> list[LogEntry | Deployment | ServiceHealth]:
        return [*self.deployments, *self.logs, *self.health]


def build_environment(anchor: datetime, *, incident: bool) -> Environment:
    """Telemetry for every simulated service, ending at `anchor`.

    `incident=True` is the v1.8.2 incident timeline; `incident=False` is a healthy environment
    running v1.8.1 (the state the demo resets to).
    """

    def at(offset: int) -> datetime:
        return anchor + timedelta(seconds=offset)

    deployment_specs = _DEPLOYMENT_HISTORY + (_INCIDENT_DEPLOYMENTS if incident else ())
    log_specs = _BASELINE_LOGS + (_INCIDENT_LOGS if incident else _HEALTHY_LOGS)
    health_specs = _BASELINE_HEALTH + (_INCIDENT_HEALTH if incident else _HEALTHY_HEALTH)

    return Environment(
        deployments=[
            Deployment(
                service_name=service,
                version=version,
                timestamp=at(offset),
                status=status,
                commit_sha=sha,
            )
            for offset, service, version, status, sha in deployment_specs
        ],
        logs=[
            LogEntry(
                service_name=service,
                timestamp=at(offset),
                level=level,
                message=message,
                meta=dict(meta),
            )
            for offset, service, level, message, meta in sorted(log_specs, key=lambda s: s[0])
        ],
        health=[
            ServiceHealth(
                service_name=service,
                timestamp=at(offset),
                status=status,
                error_rate=error_rate,
                latency_ms=latency,
                cpu_usage=cpu,
                memory_usage=memory,
            )
            for offset, service, status, error_rate, latency, cpu, memory in health_specs
        ],
    )


# --- Simulated remediation effects (executed only after human approval) --------------------------

# Health right after the approved rollback (CLAUDE.md §8/§26: 37% -> 0.8%, 2800 ms -> 180 ms).
# (status, error_rate, latency_ms, cpu_usage, memory_usage)
RECOVERED_HEALTH = (ServiceStatus.HEALTHY, 0.8, 180.0, 43.0, 68.0)


def rollback_logs(at: datetime, service: str, from_version: str, to_version: str) -> list[LogEntry]:
    """What the service logs while being rolled back (offsets in seconds from `at`)."""
    specs: tuple[tuple[int, str, dict[str, Any]], ...] = (
        (
            0,
            f"Rollback started: {from_version} -> {to_version}",
            {"from_version": from_version, "to_version": to_version, "approved": True},
        ),
        (
            20,
            "Application configuration reloaded",
            {"version": to_version, "changed_keys": ["database.host", "database.pool_size"]},
        ),
        (30, f"Rollback completed: {to_version} active", {"version": to_version}),
        (35, "Database connection pool initialized", {"pool_size": 20, "available": 20}),
        (40, "POST /payment 200", {"status": 200, "duration_ms": 182}),
    )
    return [
        LogEntry(
            service_name=service,
            timestamp=at + timedelta(seconds=offset),
            level=LogLevel.INFO,
            message=message,
            meta=meta,
        )
        for offset, message, meta in specs
    ]


def restart_logs(at: datetime, service: str) -> list[LogEntry]:
    return [
        LogEntry(
            service_name=service,
            timestamp=at,
            level=LogLevel.INFO,
            message="Service restart started",
            meta={"approved": True},
        ),
        LogEntry(
            service_name=service,
            timestamp=at + timedelta(seconds=15),
            level=LogLevel.INFO,
            message="Service restarted",
            meta={},
        ),
    ]


def classify_severity(status: ServiceStatus, error_rate: float) -> Severity:
    """Deterministic severity rule (CLAUDE.md §47: severity never depends only on an LLM)."""
    if status is ServiceStatus.DOWN or error_rate >= 50:
        return Severity.CRITICAL
    if error_rate >= 20:
        return Severity.HIGH
    if error_rate >= 5:
        return Severity.MEDIUM
    return Severity.LOW
