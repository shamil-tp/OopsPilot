"""Verification Agent (Phase 9): backend recovery checks decide; AI only explains."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import investigation, remediation, root_cause, verification
from app.agents.approval import latest_approval
from app.ai.errors import AIConfigurationError, AIRateLimitError
from app.db.session import SessionLocal
from app.models import AgentRun, Approval, Deployment, Incident, ServiceHealth
from app.models.enums import (
    AgentName,
    AgentRunStatus,
    DeploymentStatus,
    IncidentStatus,
    ServiceStatus,
)
from app.schemas.common import as_utc
from app.schemas.verification import VerificationExplanation, VerificationResult
from tests.investigation_fakes import GOOD_REMEDIATION, GOOD_VERIFICATION, FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")

ALL_CHECKS = [
    "remediation_executed",
    "target_deployment_active",
    "service_healthy",
    "error_rate_recovered",
    "latency_recovered",
    "telemetry_fresh",
]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    for module in (investigation, root_cause, remediation, verification):
        monkeypatch.setattr(module, "get_ai_provider", lambda: provider)
    return provider


async def verifying(client: AsyncClient) -> int:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    for step in ("investigate", "analyze", "remediate"):
        assert (await client.post(f"/api/incidents/{incident_id}/{step}")).status_code == 201
    assert (await client.post(f"/api/incidents/{incident_id}/approve")).status_code == 200
    return incident_id


async def status_of(incident_id: int) -> IncidentStatus:
    async with SessionLocal() as db:
        incident = await db.get(Incident, incident_id)
        assert incident is not None
        return incident.status


async def runs(db: AsyncSession, incident_id: int, agent: AgentName) -> list[AgentRun]:
    rows = await db.scalars(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id, AgentRun.agent_name == agent)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def add_health(db: AsyncSession, **values: Any) -> None:
    """A newer payment-api health snapshot than anything recorded so far."""
    latest = await db.scalar(
        select(func.max(ServiceHealth.timestamp)).where(ServiceHealth.service_name == "payment-api")
    )
    assert latest is not None
    row = {
        "status": ServiceStatus.HEALTHY,
        "error_rate": 0.8,
        "latency_ms": 180.0,
        "cpu_usage": 43.0,
        "memory_usage": 68.0,
        **values,
    }
    db.add(
        ServiceHealth(
            service_name="payment-api", timestamp=as_utc(latest) + timedelta(seconds=30), **row
        )
    )
    await db.commit()


async def verify(client: AsyncClient, incident_id: int) -> Any:
    return await client.post(f"/api/incidents/{incident_id}/verify")


def checks_of(body: dict[str, Any]) -> dict[str, bool]:
    return {c["name"]: c["passed"] for c in body["result"]["checks"]}


# --- success ------------------------------------------------------------------------------------


async def test_successful_verification_resolves_the_incident(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    deployments_before = await db.scalar(select(func.count()).select_from(Deployment))

    response = await verify(client, incident_id)

    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["agent"], body["incident_status"]) == (
        "COMPLETED",
        "verification",
        "RESOLVED",
    )
    result = VerificationResult.model_validate(body["result"])
    assert result.recovered is True
    assert result.confidence == 1.0
    assert [c.name for c in result.checks] == ALL_CHECKS
    assert all(c.passed for c in result.checks) and result.failed_checks == []
    assert result.before.model_dump() == {
        "active_version": "v1.8.2",
        "status": "DEGRADED",
        "error_rate": 37.0,
        "latency_ms": 2800.0,
    }
    assert result.after.model_dump() == {
        "active_version": "v1.8.1",
        "status": "HEALTHY",
        "error_rate": 0.8,
        "latency_ms": 180.0,
    }
    assert [e.id for e in result.supporting_evidence] == ["V1", "V2", "V3", "V4"]
    assert result.next_step == "incident_report"
    assert body["summary"] == "payment-api recovered: 6/6 checks passed"

    run = (await runs(db, incident_id, AgentName.VERIFICATION))[0]
    assert run.status is AgentRunStatus.COMPLETED and run.completed_at is not None
    assert len(fake.calls_for(VerificationExplanation)) == 1
    # Verification is read-only for remediation: no deployment, approval or execution added.
    assert await db.scalar(select(func.count()).select_from(Deployment)) == deployments_before
    assert len(await runs(db, incident_id, AgentName.ORCHESTRATOR)) == 1
    assert await db.scalar(select(func.count()).select_from(Approval)) == 1

    fetched = await client.get(f"/api/incidents/{incident_id}/verification")
    assert fetched.status_code == 200 and fetched.json() == body


async def test_events(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await verifying(client)
    await verify(client, incident_id)

    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    ours = [e for e in events if e["agent"] == "verification"]

    assert [e["event_type"] for e in ours] == [
        "verification_started",
        *["recovery_check"] * 6,
        "verification_completed",
        "incident_resolved",
    ]
    checks = [e["metadata"] for e in ours if e["event_type"] == "recovery_check"]
    assert [c["check"] for c in checks] == ALL_CHECKS
    assert all(c["passed"] for c in checks)
    error_check = next(c for c in checks if c["check"] == "error_rate_recovered")
    assert error_check["expected"] == "< 5% (was 37%)" and error_check["actual"] == "0.8%"
    completed = ours[-2]["metadata"]
    assert completed["recovered"] is True
    assert completed["incident_status"] == {"from": "VERIFYING", "to": "RESOLVED"}
    assert completed["before"]["error_rate"] == 37.0 and completed["after"]["error_rate"] == 0.8
    types = [e["event_type"] for e in events]
    assert types.index("verification_started") > types.index("remediation_completed")


async def test_ai_cannot_override_the_backend_outcome(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    fake.results["VerificationExplanation"] = {
        "reasoning_summary": "The service did NOT recover; roll back again.",
        "supporting_evidence": ["V3"],
    }

    body = (await verify(client, incident_id)).json()

    assert body["result"]["recovered"] is True  # the checks decide, not the model's wording
    assert body["incident_status"] == "RESOLVED"
    schema = VerificationExplanation.model_json_schema()["properties"]
    assert not {"recovered", "confidence", "health", "error_rate", "next_step"} & set(schema)


# --- recovery not confirmed ---------------------------------------------------------------------


async def assert_not_recovered(
    client: AsyncClient, db: AsyncSession, incident_id: int, failed: set[str]
) -> dict[str, Any]:
    response = await verify(client, incident_id)
    assert response.status_code == 201
    body = response.json()
    result = body["result"]
    assert result["recovered"] is False
    assert set(result["failed_checks"]) == failed
    assert {n for n, ok in checks_of(body).items() if not ok} == failed
    assert result["next_step"] == "human_investigation"
    assert body["incident_status"] == "FAILED"
    assert await status_of(incident_id) is IncidentStatus.FAILED
    total = len(result["checks"])
    assert result["confidence"] == round((total - len(failed)) / total, 2)
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert "incident_resolved" not in {e["event_type"] for e in events}
    # Never re-remediates: still one execution, one approval.
    assert len(await runs(db, incident_id, AgentName.ORCHESTRATOR)) == 1
    return body


async def test_service_still_degraded(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    await add_health(db, status=ServiceStatus.DEGRADED, error_rate=37.0, latency_ms=2800.0)

    await assert_not_recovered(
        client, db, incident_id, {"service_healthy", "error_rate_recovered", "latency_recovered"}
    )


async def test_error_rate_still_high(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    await add_health(db, error_rate=12.0)

    body = await assert_not_recovered(client, db, incident_id, {"error_rate_recovered"})

    assert body["result"]["after"]["error_rate"] == 12.0


async def test_latency_still_high(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    await add_health(db, latency_ms=900.0)

    await assert_not_recovered(client, db, incident_id, {"latency_recovered"})


async def test_wrong_active_deployment(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    latest = await db.scalar(select(func.max(Deployment.timestamp)))
    assert latest is not None
    db.add(
        Deployment(
            service_name="payment-api",
            version="v1.8.3",
            timestamp=as_utc(latest) + timedelta(seconds=5),
            status=DeploymentStatus.SUCCEEDED,
            commit_sha="bbbbbbb",
        )
    )
    await db.commit()

    body = await assert_not_recovered(client, db, incident_id, {"target_deployment_active"})

    assert body["result"]["after"]["active_version"] == "v1.8.3"


async def test_target_deployment_missing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    await db.execute(delete(Deployment).where(Deployment.version == "v1.8.1"))
    await db.commit()

    body = await assert_not_recovered(client, db, incident_id, {"target_deployment_active"})

    assert body["result"]["after"]["active_version"] == "v1.8.0"


async def test_missing_telemetry_is_never_recovered(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    fake.results["VerificationExplanation"] = {**GOOD_VERIFICATION, "supporting_evidence": ["V1"]}
    await db.execute(delete(ServiceHealth).where(ServiceHealth.service_name == "payment-api"))
    await db.commit()

    body = await assert_not_recovered(
        client,
        db,
        incident_id,
        {"service_healthy", "error_rate_recovered", "latency_recovered", "telemetry_fresh"},
    )

    assert body["result"]["after"]["status"] is None
    assert "V3" not in [e["id"] for e in body["result"]["supporting_evidence"]]


async def test_stale_telemetry_is_never_recovered(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    # Drop the post-remediation snapshot and pretend an old healthy one is the latest.
    execution = (await runs(db, incident_id, AgentName.ORCHESTRATOR))[0]
    executed_at = as_utc(execution.completed_at)
    await db.execute(
        delete(ServiceHealth).where(
            ServiceHealth.service_name == "payment-api",
            ServiceHealth.timestamp >= executed_at - timedelta(minutes=5),
        )
    )
    await db.commit()

    # The only snapshot left is the old pre-incident baseline: HEALTHY, but it proves nothing
    # about the service after the remediation.
    body = await assert_not_recovered(client, db, incident_id, {"telemetry_fresh"})

    assert body["result"]["after"]["status"] == "HEALTHY"


async def test_restart_that_does_not_recover_is_reported_honestly(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    fake.results["RemediationProposal"] = {
        **GOOD_REMEDIATION,
        "action": "RESTART_SERVICE",
        "rollback_to_version": None,
    }
    incident_id = await verifying(client)

    body = await assert_not_recovered(
        client, db, incident_id, {"service_healthy", "error_rate_recovered", "latency_recovered"}
    )

    assert "target_deployment_active" not in checks_of(body)  # only for rollbacks
    assert len(body["result"]["checks"]) == 5


# --- preconditions ------------------------------------------------------------------------------


async def test_unknown_incident(client: AsyncClient, fake: FakeProvider) -> None:
    assert (await client.post("/api/incidents/999999/verify")).status_code == 404
    assert (await client.get("/api/incidents/999999/verification")).status_code == 404


async def test_requires_an_executed_approved_remediation(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    detected = await verify(client, incident_id)
    for step in ("investigate", "analyze", "remediate"):
        await client.post(f"/api/incidents/{incident_id}/{step}")
    awaiting_approval = await verify(client, incident_id)
    await client.post(f"/api/incidents/{incident_id}/reject")
    rejected = await verify(client, incident_id)

    for response in (detected, awaiting_approval, rejected):
        assert response.status_code == 409
        assert "no completed remediation execution" in response.json()["detail"]
    assert fake.calls_for(VerificationExplanation) == []
    assert (await client.get(f"/api/incidents/{incident_id}/verification")).status_code == 404


async def test_failed_execution_cannot_be_verified(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    for step in ("investigate", "analyze", "remediate"):
        await client.post(f"/api/incidents/{incident_id}/{step}")
    approval = await latest_approval(db, incident_id)
    assert approval is not None
    approval.parameters = {**approval.parameters, "to_version": "v9.9.9"}
    await db.commit()
    assert (await client.post(f"/api/incidents/{incident_id}/approve")).status_code == 409

    response = await verify(client, incident_id)

    assert response.status_code == 409
    assert await status_of(incident_id) is IncidentStatus.FAILED


async def test_wrong_state_is_a_conflict(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    incident = await db.get(Incident, incident_id)
    assert incident is not None
    incident.status = IncidentStatus.REMEDIATING
    await db.commit()

    response = await verify(client, incident_id)

    assert response.status_code == 409
    assert "verification requires VERIFYING" in response.json()["detail"]


# --- AI output guardrails and failures ----------------------------------------------------------


async def assert_retryable_failure(
    client: AsyncClient, db: AsyncSession, incident_id: int, response: Any, code: int, text: str
) -> None:
    assert response.status_code == code
    assert text in response.json()["detail"]
    assert await status_of(incident_id) is IncidentStatus.VERIFYING  # never resolved
    run = (await runs(db, incident_id, AgentName.VERIFICATION))[-1]
    assert run.status is AgentRunStatus.FAILED and run.output is None
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert (events[-1]["event_type"], events[-1]["agent"]) == ("error", "verification")


async def test_fabricated_evidence_fails_safely_and_can_be_retried(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    fake.results["VerificationExplanation"] = {
        **GOOD_VERIFICATION,
        "supporting_evidence": ["V9", "L3"],
    }

    response = await verify(client, incident_id)

    await assert_retryable_failure(
        client, db, incident_id, response, 502, "did not cite any supplied evidence"
    )
    fake.results["VerificationExplanation"] = GOOD_VERIFICATION
    retried = await verify(client, incident_id)
    assert retried.status_code == 201 and retried.json()["incident_status"] == "RESOLVED"


async def test_fabricated_ids_are_removed_from_valid_output(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    fake.results["VerificationExplanation"] = {
        **GOOD_VERIFICATION,
        "supporting_evidence": ["V3", "V99", "D1"],
    }

    body = (await verify(client, incident_id)).json()

    assert [e["id"] for e in body["result"]["supporting_evidence"]] == ["V3"]
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    completed = next(e for e in events if e["event_type"] == "verification_completed")
    assert completed["metadata"]["unsupported_citations_removed"] == 2


async def test_malformed_ai_output(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    fake.results["VerificationExplanation"] = {"summary": "missing required fields"}

    response = await verify(client, incident_id)

    await assert_retryable_failure(
        client, db, incident_id, response, 502, "did not match VerificationExplanation"
    )


async def test_ai_failures(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await verifying(client)
    fake.error = AIRateLimitError("rate limit")
    rate_limited = await verify(client, incident_id)
    await assert_retryable_failure(client, db, incident_id, rate_limited, 502, "rate limit")

    def no_keys() -> None:
        raise AIConfigurationError("no Gemini API key is configured")

    monkeypatch.setattr(verification, "get_ai_provider", no_keys)
    not_configured = await verify(client, incident_id)
    await assert_retryable_failure(client, db, incident_id, not_configured, 503, "not configured")


async def test_unexpected_error_is_generic_and_leaks_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    fake.error = RuntimeError("api_key=AIzaSyTOPSECRET password=hunter2")

    response = await verify(client, incident_id)

    await assert_retryable_failure(client, db, incident_id, response, 500, "failed unexpectedly")
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    everything = response.text + str(events)
    assert "TOPSECRET" not in everything and "hunter2" not in everything


# --- idempotency and concurrency ----------------------------------------------------------------


async def test_repeat_verification_returns_stored_result(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    first = await verify(client, incident_id)

    second = await verify(client, incident_id)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(fake.calls_for(VerificationExplanation)) == 1
    assert len(await runs(db, incident_id, AgentName.VERIFICATION)) == 1


async def test_running_verification_is_a_conflict(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await verifying(client)
    execution = (await runs(db, incident_id, AgentName.ORCHESTRATOR))[0]
    db.add(
        AgentRun(
            incident_id=incident_id,
            agent_name=AgentName.VERIFICATION,
            status=AgentRunStatus.RUNNING,
            started_at=execution.started_at,
        )
    )
    await db.commit()

    response = await verify(client, incident_id)

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]


async def test_concurrent_verifications_run_once(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await verifying(client)
    original = verification._completed_execution

    async def slow_precondition(*args: Any, **kwargs: Any) -> Any:
        found = await original(*args, **kwargs)
        await asyncio.sleep(0.2)  # both requests pass the early checks before either locks
        return found

    monkeypatch.setattr(verification, "_completed_execution", slow_precondition)

    responses = await asyncio.gather(verify(client, incident_id), verify(client, incident_id))

    assert sorted(r.status_code for r in responses) == [201, 409]
    assert "already started" in next(r for r in responses if r.status_code == 409).text
    assert len(await runs(db, incident_id, AgentName.VERIFICATION)) == 1
    assert len(fake.calls_for(VerificationExplanation)) == 1
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert [e["event_type"] for e in events].count("verification_completed") == 1
    assert await status_of(incident_id) is IncidentStatus.RESOLVED
