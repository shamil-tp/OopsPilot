"""Deterministic, bounded evidence collection for the Investigation Agent.

The backend decides what the model sees. For an incident detected at T0 on service S:

- logs: one query for S in [T0-10min, T0+10min]; ERROR/WARN lines collapsed into (level,
  message) groups with a count (max 15), plus INFO lines logged in the first 60 s after a
  deployment inside the window (max 5), i.e. what the service said while it was being changed
- health: S now and S's last HEALTHY snapshot before T0 (the baseline), plus S's dependencies
- deployments: S's last 3 deployments at or before T0
- previous incidents: S's last 3 earlier incidents
- CI/CD: S's last 5 normalized GitHub events (pushes, workflow runs, deployments) in the 2 h
  before T0, read from the backend's cicd_events (never raw payloads, never GitHub itself)

Every item gets a citation id (L1, H1, D1, P1, C1, ...) so findings can be checked against it.
Identical input always yields the identical package; the same code serves every service.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.models import Incident
from app.models.enums import LogLevel, ServiceStatus, Severity
from app.schemas.cicd import CicdEventRead
from app.schemas.common import as_utc
from app.schemas.investigation import EvidenceItem, PreviousIncidentRead
from app.schemas.services import DeploymentRead, LogEntryRead, ServiceHealthRead
from app.services.agent_events import EventRecorder, json_safe
from app.services.service_catalog import get_service
from app.tools.registry import ToolExecutor

LOG_WINDOW = timedelta(minutes=10)
LOG_FETCH_LIMIT = 50
MAX_LOG_GROUPS = 15
MAX_CHANGE_EVENTS = 5
CHANGE_EVENT_WINDOW = timedelta(seconds=60)
MAX_DEPLOYMENTS = 3
MAX_PREVIOUS_INCIDENTS = 3
CICD_WINDOW = timedelta(hours=2)
MAX_CICD_EVENTS = 5
# Metadata keys that add tokens but no diagnostic value.
_NOISY_META = {"request_id"}


@dataclass(frozen=True)
class IncidentContext:
    """Plain snapshot of the incident. Agents never touch the ORM object after loading it: a
    rollback (e.g. a retried tool) expires ORM objects, and async sessions can't lazy-reload."""

    id: int
    reference: str
    title: str
    description: str
    severity: Severity
    service: str
    detected_at: datetime

    @classmethod
    def of(cls, incident: Incident) -> "IncidentContext":
        return cls(
            id=incident.id,
            reference=incident.reference,
            title=incident.title,
            description=incident.description,
            severity=incident.severity,
            service=incident.service_name,
            detected_at=as_utc(incident.created_at),
        )


@dataclass
class EvidencePackage:
    incident_id: int
    reference: str
    service: str
    detected_at: datetime
    items: list[EvidenceItem] = field(default_factory=list)
    deployments: list[DeploymentRead] = field(default_factory=list)
    previous_incidents: list[PreviousIncidentRead] = field(default_factory=list)
    cicd_events: list[CicdEventRead] = field(default_factory=list)

    @property
    def ids(self) -> set[str]:
        return {item.id for item in self.items}

    def of(self, source: str) -> list[EvidenceItem]:
        return [item for item in self.items if item.source == source]


def relative(ts: datetime, t0: datetime) -> str:
    """Compact offset from detection time, e.g. T-120s, T+30s, T-2d."""
    seconds = int((ts - t0).total_seconds())
    sign = "-" if seconds < 0 else "+"
    seconds = abs(seconds)
    if seconds >= 86400:
        return f"T{sign}{seconds // 86400}d"
    return f"T{sign}{seconds}s"


def _meta_text(meta: dict[str, Any]) -> str:
    parts = [f"{k}={v}" for k, v in meta.items() if k not in _NOISY_META][:5]
    return f" [{', '.join(parts)}]" if parts else ""


class EvidenceCollector:
    def __init__(self, executor: ToolExecutor, events: EventRecorder, commit: Callable[[], Any]):
        self._executor = executor
        self._events = events
        self._commit = commit

    async def collect(self, incident: IncidentContext) -> EvidencePackage:
        t0 = incident.detected_at
        service = incident.service
        package = EvidencePackage(incident.id, incident.reference, service, t0)

        current = await self._tool("get_service_health", service=service, until=t0)
        deployments = await self._tool(
            "get_recent_deployments", service=service, until=t0, limit=MAX_DEPLOYMENTS
        )
        logs = await self._tool(
            "get_application_logs",
            service=service,
            since=t0 - LOG_WINDOW,
            until=t0 + LOG_WINDOW,
            limit=LOG_FETCH_LIMIT,
        )
        baseline = await self._tool(
            "get_service_health", service=service, until=t0, status=ServiceStatus.HEALTHY
        )
        catalog = get_service(service)
        dependencies = [
            (dep, await self._tool("get_service_health", service=dep, until=t0))
            for dep in (catalog.dependencies if catalog else ())
        ]
        previous = await self._tool(
            "get_previous_incidents",
            service=service,
            before=t0,
            exclude_incident_id=incident.id,
            limit=MAX_PREVIOUS_INCIDENTS,
        )

        cicd_events = await self._tool(
            "get_recent_cicd_events",
            service=service,
            since=t0 - CICD_WINDOW,
            until=t0,
            limit=MAX_CICD_EVENTS,
        )

        package.items += self._log_items(service, logs, deployments, t0)
        health = [(current, "current"), (baseline, "last healthy before incident")]
        health += [(snapshot, f"dependency of {service}") for _, snapshot in dependencies]
        package.items += self._health_items(health, t0)
        package.items += self._deployment_items(deployments, t0)
        package.items += self._previous_items(service, previous)
        package.items += self._cicd_items(cicd_events, t0)
        package.deployments = deployments
        package.previous_incidents = previous
        package.cicd_events = cicd_events
        self._announce(package, current)
        return package

    async def _tool(self, name: str, **arguments: Any) -> Any:
        self._events.emit("tool_started", f"Running {name}", tool=name, arguments=arguments)
        result = await self._executor.call(name, **arguments)
        count = len(result) if isinstance(result, list) else int(result is not None)
        self._events.emit(
            "tool_completed", f"{name} returned {count} record(s)", tool=name, records=count
        )
        await self._commit()  # make progress visible through GET /events
        return result

    # --- rendering ------------------------------------------------------------------------------

    @staticmethod
    def _log_items(
        service: str, logs: list[LogEntryRead], deployments: list[DeploymentRead], t0: datetime
    ) -> list[EvidenceItem]:
        groups: dict[tuple[LogLevel, str], list[LogEntryRead]] = {}
        for log in logs:
            if log.level in (LogLevel.ERROR, LogLevel.WARN):
                key = (log.level, log.message)
                if key in groups or len(groups) < MAX_LOG_GROUPS:
                    groups.setdefault(key, []).append(log)

        window_start = t0 - LOG_WINDOW
        changes = [
            log
            for log in logs
            if log.level is LogLevel.INFO
            and any(
                d.timestamp <= log.timestamp <= d.timestamp + CHANGE_EVENT_WINDOW
                for d in deployments
                if d.timestamp >= window_start
            )
        ][:MAX_CHANGE_EVENTS]

        entries: list[tuple[datetime, str, dict[str, Any]]] = []
        for (level, message), group in groups.items():
            first, last = group[0], group[-1]
            text = f"{level} {message}"
            if len(group) > 1:
                text += f" (x{len(group)}, last at {relative(last.timestamp, t0)})"
            data = {
                "level": level,
                "message": message,
                "count": len(group),
                "first_seen": first.timestamp,
                "last_seen": last.timestamp,
                "metadata": first.metadata,
            }
            entries.append((first.timestamp, text + _meta_text(first.metadata), data))
        for log in changes:
            data = {
                "level": log.level,
                "message": log.message,
                "count": 1,
                "first_seen": log.timestamp,
                "last_seen": log.timestamp,
                "metadata": log.metadata,
            }
            entries.append((log.timestamp, f"INFO {log.message}{_meta_text(log.metadata)}", data))

        entries.sort(key=lambda e: e[0])
        return [
            EvidenceItem(
                id=f"L{n}",
                source="logs",
                service=service,
                timestamp=ts,
                fact=f"{ts:%H:%M:%S} ({relative(ts, t0)}) {text}",
                data=json_safe(data),
            )
            for n, (ts, text, data) in enumerate(entries, start=1)
        ]

    @staticmethod
    def _health_items(
        snapshots: list[tuple[ServiceHealthRead | None, str]], t0: datetime
    ) -> list[EvidenceItem]:
        items = []
        for snapshot, label in snapshots:
            if snapshot is None:
                continue
            items.append(
                EvidenceItem(
                    id=f"H{len(items) + 1}",
                    source="health",
                    service=snapshot.service_name,
                    timestamp=snapshot.timestamp,
                    fact=(
                        f"{snapshot.service_name} {snapshot.status} at "
                        f"{snapshot.timestamp:%H:%M:%S} ({relative(snapshot.timestamp, t0)}, "
                        f"{label}): "
                        + (
                            "failed health checks (last 10) "
                            if snapshot.cpu_usage is None
                            else "error_rate "
                        )
                        + f"{snapshot.error_rate:g}%, latency {snapshot.latency_ms:g} ms"
                        + (
                            f", cpu {snapshot.cpu_usage:g}%, memory {snapshot.memory_usage:g}%"
                            if snapshot.cpu_usage is not None and snapshot.memory_usage is not None
                            else ", cpu/memory not measured"
                        )
                    ),
                    data=snapshot.model_dump(mode="json") | {"label": label},
                )
            )
        return items

    @staticmethod
    def _deployment_items(deployments: list[DeploymentRead], t0: datetime) -> list[EvidenceItem]:
        return [
            EvidenceItem(
                id=f"D{n}",
                source="deployments",
                service=d.service_name,
                timestamp=d.timestamp,
                fact=(
                    f"{d.service_name} {d.version} deployed {d.timestamp:%Y-%m-%d %H:%M:%S} "
                    f"({relative(d.timestamp, t0)}), {d.status}, commit {d.commit_sha or 'unknown'}"
                ),
                data=d.model_dump(mode="json"),
            )
            for n, d in enumerate(deployments, start=1)
        ]

    @staticmethod
    def _previous_items(service: str, previous: list[PreviousIncidentRead]) -> list[EvidenceItem]:
        return [
            EvidenceItem(
                id=f"P{n}",
                source="previous_incidents",
                service=service,
                timestamp=p.created_at,
                fact=(
                    f"{p.reference} '{p.title}' ({p.severity}, {p.status}) at "
                    f"{p.created_at:%Y-%m-%d %H:%M}; recorded root cause: "
                    f"{p.root_cause or 'none recorded'}"
                ),
                data=p.model_dump(mode="json"),
            )
            for n, p in enumerate(previous, start=1)
        ]

    @staticmethod
    def _cicd_items(events: list[CicdEventRead], t0: datetime) -> list[EvidenceItem]:
        items = []
        for n, e in enumerate(events, start=1):
            sha = e.commit_sha[:7] if e.commit_sha else "unknown"
            at = f"{e.occurred_at:%H:%M:%S} ({relative(e.occurred_at, t0)})"
            if e.event_type == "push":
                tag = e.metadata.get("tag")
                if tag:
                    fact = f"{at} GitHub tag {tag} pushed for commit {sha} in {e.repository}"
                else:
                    files = e.metadata.get("changed_files") or []
                    fact = (
                        f"{at} GitHub push to {e.branch or 'unknown ref'} in {e.repository}: "
                        f"commit {sha} by {e.actor or 'unknown'}, "
                        f'message (quoted data) "{e.commit_message or ""}"'
                        + (f", files changed: {', '.join(files[:5])}" if files else "")
                    )
            else:
                name = e.workflow_name or "GitHub deployment"
                run = f" #{e.run_number}" if e.run_number else ""
                timing = ", ".join(
                    part
                    for part in (
                        f"started {relative(e.started_at, t0)}" if e.started_at else "",
                        f"completed {relative(e.completed_at, t0)}" if e.completed_at else "",
                    )
                    if part
                )
                version = (
                    f"version {e.version} (from {e.version_source})"
                    if e.version
                    else "version unknown"
                )
                fact = (
                    f"{at} GitHub {e.event_type} '{name}'{run} [{e.category}"
                    f"{', ' + e.environment if e.environment else ''}]: {e.status}"
                    f"{'/' + e.conclusion if e.conclusion else ''}"
                    f"{' (' + timing + ')' if timing else ''}, commit {sha}, {version}"
                    + (", recorded as a deployment" if e.deployment_id else "")
                )
            items.append(
                EvidenceItem(
                    id=f"C{n}",
                    source="cicd",
                    service=e.service_name or "unknown",
                    timestamp=e.occurred_at,
                    fact=fact,
                    data=e.model_dump(mode="json", exclude={"delivery_id", "html_url"}),
                )
            )
        return items

    def _announce(self, package: EvidencePackage, current: ServiceHealthRead | None) -> None:
        """Human-readable highlights for the timeline (deterministic, no AI)."""
        logs = package.of("logs")
        errors = [i for i in logs if i.data.get("level") == LogLevel.ERROR]
        self._events.emit(
            "evidence_found",
            f"{len(logs)} relevant log pattern(s) in the ±10 min window"
            + (f"; first error at {errors[0].timestamp:%H:%M:%S}" if errors else ""),
            source="logs",
            evidence_ids=[i.id for i in logs],
        )
        if current is not None:
            self._events.emit(
                "evidence_found",
                f"{current.service_name} is {current.status}: error rate "
                f"{current.error_rate:g}%, latency {current.latency_ms:g} ms",
                source="health",
                evidence_ids=[i.id for i in package.of("health")],
            )
        if package.deployments:
            latest = package.deployments[0]
            self._events.emit(
                "evidence_found",
                f"Latest deployment {latest.version} at {latest.timestamp:%H:%M:%S} "
                f"({relative(latest.timestamp, package.detected_at)} relative to detection)",
                source="deployments",
                evidence_ids=[i.id for i in package.of("deployments")],
            )
        if package.cicd_events:
            deploys = [e for e in package.cicd_events if e.category == "DEPLOYMENT"]
            latest = deploys[-1] if deploys else package.cicd_events[-1]
            outcome = latest.conclusion or latest.status
            self._events.emit(
                "evidence_found",
                f"{len(package.cicd_events)} GitHub CI/CD event(s) in the 2 h before detection; "
                f"latest {latest.workflow_name or latest.event_type}: {outcome}"
                + (f" ({latest.version})" if latest.version else "")
                + f" at {latest.occurred_at:%H:%M:%S}",
                source="cicd",
                evidence_ids=[i.id for i in package.of("cicd")],
            )
        self._events.emit(
            "evidence_found",
            f"{len(package.previous_incidents)} previous incident(s) for {package.service}",
            source="previous_incidents",
            evidence_ids=[i.id for i in package.of("previous_incidents")],
        )
