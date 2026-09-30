"""Deterministic evidence collection and prompt construction (no AI involved)."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.evidence import (
    LOG_WINDOW,
    MAX_CHANGE_EVENTS,
    MAX_LOG_GROUPS,
    EvidenceCollector,
    EvidencePackage,
    IncidentContext,
)
from app.models import Incident
from app.models.enums import AgentName, IncidentStatus, Severity
from app.prompts.investigation import build_prompt
from app.services.agent_events import EventRecorder
from app.tools import ToolExecutor

pytestmark = pytest.mark.usefixtures("clean_demo")


async def collect(
    client: AsyncClient, db: AsyncSession
) -> tuple[IncidentContext, EvidencePackage, int]:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    loaded = await db.get(Incident, incident_id)
    assert loaded is not None
    incident = IncidentContext.of(loaded)
    executor = ToolExecutor(AgentName.INVESTIGATION, db, max_calls=8, max_retries=2)
    events = EventRecorder(db, incident.id, AgentName.INVESTIGATION)
    package = await EvidenceCollector(executor, events, db.commit).collect(incident)
    return incident, package, executor.calls


async def test_logs_are_bounded_filtered_and_chronological(
    client: AsyncClient, db: AsyncSession
) -> None:
    incident, package, _ = await collect(client, db)
    logs = package.of("logs")
    t0 = incident.detected_at

    assert 0 < len(logs) <= MAX_LOG_GROUPS + MAX_CHANGE_EVENTS
    assert [item.id for item in logs] == [f"L{n}" for n in range(1, len(logs) + 1)]
    assert all(item.service == "payment-api" for item in logs)  # no other service's logs
    assert all(t0 - LOG_WINDOW <= item.timestamp <= t0 + LOG_WINDOW for item in logs)
    assert [i.timestamp for i in logs] == sorted(i.timestamp for i in logs)
    levels = {item.data["level"] for item in logs}
    assert levels <= {"ERROR", "WARN", "INFO"}
    # INFO lines only from the deployment window; routine traffic is filtered out.
    info = [i.data["message"] for i in logs if i.data["level"] == "INFO"]
    assert "Deployment v1.8.2 started" in info
    assert "Payment processed successfully" not in info
    # Repeated lines are collapsed with a count.
    http_500 = next(i for i in logs if i.data["message"] == "POST /payment 500")
    assert http_500.data["count"] == 6
    assert "x6" in http_500.fact
    assert any(i.data["message"] == "Database connection failed" for i in logs)


async def test_health_includes_current_baseline_and_dependencies(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, package, _ = await collect(client, db)
    health = {(i.service, i.data["label"]): i.data for i in package.of("health")}

    current = health[("payment-api", "current")]
    assert (current["status"], current["error_rate"], current["latency_ms"]) == (
        "DEGRADED",
        37.0,
        2800.0,
    )
    assert health[("payment-api", "last healthy before incident")]["status"] == "HEALTHY"
    assert health[("database", "dependency of payment-api")]["status"] == "HEALTHY"
    assert health[("auth-api", "dependency of payment-api")]["status"] == "HEALTHY"
    for field in ("service_name", "timestamp", "cpu_usage", "memory_usage"):
        assert field in current


async def test_recent_deployments_are_bounded_newest_first(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, package, _ = await collect(client, db)

    assert [d.version for d in package.deployments] == ["v1.8.2", "v1.8.1", "v1.8.0"]
    assert [i.id for i in package.of("deployments")] == ["D1", "D2", "D3"]
    assert package.deployments[0].commit_sha == "e4a7c52"
    assert "T-180s" in package.of("deployments")[0].fact


async def test_previous_incidents_are_bounded_and_exclude_current(
    client: AsyncClient, db: AsyncSession
) -> None:
    for n in range(5):
        db.add(
            Incident(
                title=f"Old incident {n}",
                severity=Severity.MEDIUM,
                status=IncidentStatus.RESOLVED,
                service_name="payment-api",
                created_at=datetime(2026, 9, 1 + n, tzinfo=UTC),
            )
        )
    await db.commit()

    incident, package, _ = await collect(client, db)

    assert len(package.previous_incidents) == 3
    assert incident.reference not in {p.reference for p in package.previous_incidents}
    assert [p.title for p in package.previous_incidents] == [
        "Old incident 4",
        "Old incident 3",
        "Old incident 2",
    ]


async def test_no_previous_incidents_after_reset(client: AsyncClient, db: AsyncSession) -> None:
    incident, package, _ = await collect(client, db)

    assert package.previous_incidents == []
    assert "[Previous incidents]\nnone" in build_prompt(incident, package)


async def test_collection_is_deterministic_and_within_step_budget(
    client: AsyncClient, db: AsyncSession
) -> None:
    incident, package, calls = await collect(client, db)
    executor = ToolExecutor(AgentName.INVESTIGATION, db, max_calls=8, max_retries=2)
    again = await EvidenceCollector(
        executor, EventRecorder(db, incident.id, AgentName.INVESTIGATION), db.commit
    ).collect(incident)

    assert [i.fact for i in again.items] == [i.fact for i in package.items]
    assert calls <= 8  # MAX_AGENT_STEPS


async def test_prompt_is_compact_and_contains_the_key_facts(
    client: AsyncClient, db: AsyncSession
) -> None:
    incident, package, _ = await collect(client, db)

    prompt = build_prompt(incident, package)

    assert len(prompt) < 4000  # ~1k tokens: bounded, never the whole logs table
    for fact in (
        "payment-api DEGRADED",
        "error_rate 37%",
        "latency 2800 ms",
        "Database connection failed",
        "v1.8.2",
        "database HEALTHY",
    ):
        assert fact in prompt
    assert "Access token issued" not in prompt  # auth-api logs are not sent
    assert "req-10" not in prompt  # noisy request ids are dropped
