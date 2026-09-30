"""Investigation Agent end to end through the API, with a fake AI provider."""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import investigation
from app.ai.errors import AIConfigurationError, AIRateLimitError, AIStructuredOutputError
from app.models import AgentRun, Approval, Deployment, Incident
from app.models.enums import (
    AgentName,
    AgentRunStatus,
    DeploymentStatus,
    IncidentStatus,
)
from app.schemas.investigation import InvestigationAnalysis, InvestigationResult
from app.services import telemetry
from tests.investigation_fakes import GOOD_ANALYSIS, FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setattr(investigation, "get_ai_provider", lambda: provider)
    return provider


async def simulate(client: AsyncClient) -> int:
    return (await client.post("/api/incidents/simulate")).json()["id"]


async def incident_status(db: AsyncSession, incident_id: int) -> IncidentStatus:
    incident = await db.scalar(
        select(Incident).where(Incident.id == incident_id).execution_options(populate_existing=True)
    )
    assert incident is not None
    return incident.status


# --- happy path ---------------------------------------------------------------------------------


async def test_investigation_runs_and_is_stored(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["agent"] == "investigation"
    assert body["incident_status"] == "INVESTIGATING"
    result = InvestigationResult.model_validate(body["result"])
    assert result.status == "investigation_complete"
    assert result.next_step == "root_cause_analysis"
    assert result.service == "payment-api"
    assert result.incident_reference == "INC-001"
    assert [d.version for d in result.related_deployments][0] == "v1.8.2"
    assert result.related_previous_incidents == []
    assert result.model == "fake:fake-model"
    known = {e.id for e in result.evidence}
    assert all(set(f.evidence_ids) <= known for f in result.findings)

    # Exactly one structured AI call, with the investigation schema and rules.
    assert len(fake.calls) == 1
    prompt, model, options = fake.calls[0]
    assert model is InvestigationAnalysis
    assert options is not None and "Use ONLY the evidence" in (options.system_instruction or "")
    assert "payment-api DEGRADED" in prompt

    run = await db.scalar(select(AgentRun).where(AgentRun.incident_id == incident_id))
    assert run is not None
    assert run.agent_name is AgentName.INVESTIGATION
    assert run.status is AgentRunStatus.COMPLETED
    assert run.completed_at is not None
    assert run.summary == GOOD_ANALYSIS["summary"]
    assert run.output is not None and run.output["findings"]
    assert await incident_status(db, incident_id) is IncidentStatus.INVESTIGATING


async def test_events_are_stored_and_listed_chronologically(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    await client.post(f"/api/incidents/{incident_id}/investigate")

    response = await client.get(f"/api/incidents/{incident_id}/events")

    assert response.status_code == 200
    events = response.json()
    types = [e["event_type"] for e in events]
    assert types[0] == "incident_created"
    assert types[1] == "agent_started"
    assert types[-1] == "investigation_completed"
    for expected in ("tool_started", "tool_completed", "evidence_found"):
        assert expected in types
    timestamps = [datetime.fromisoformat(e["timestamp"]) for e in events]
    assert timestamps == sorted(timestamps)
    tools = {e["metadata"]["tool"] for e in events if e["event_type"] == "tool_started"}
    assert tools == {
        "get_service_health",
        "get_recent_deployments",
        "get_application_logs",
        "get_previous_incidents",
    }
    assert all(e["agent"] == "investigation" for e in events[1:])


async def test_investigation_does_not_remediate(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)

    await client.post(f"/api/incidents/{incident_id}/investigate")

    # No rollback, no approval request, service still degraded, incident not resolved.
    latest = (await client.get("/api/services/payment-api/deployments")).json()[0]
    assert (latest["version"], latest["status"]) == ("v1.8.2", "SUCCEEDED")
    rolled_back = select(func.count()).where(Deployment.status == DeploymentStatus.ROLLED_BACK)
    assert await db.scalar(rolled_back) == 0
    assert await db.scalar(select(func.count()).select_from(Approval)) == 0
    assert (await client.get("/api/services/payment-api/health")).json()["status"] == "DEGRADED"
    assert await incident_status(db, incident_id) is IncidentStatus.INVESTIGATING
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert not {"remediation_started", "approval_required"} & {e["event_type"] for e in events}


# --- duplicates and state -----------------------------------------------------------------------


async def test_repeat_request_returns_existing_result_without_ai_call(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    first = await client.post(f"/api/incidents/{incident_id}/investigate")

    second = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert second.status_code == 200
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["result"] == first.json()["result"]
    assert len(fake.calls) == 1


async def test_investigation_already_running_is_a_conflict(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    db.add(
        AgentRun(
            incident_id=incident_id,
            agent_name=AgentName.INVESTIGATION,
            status=AgentRunStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
    )
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]
    assert fake.calls == []


@pytest.mark.parametrize("state", [IncidentStatus.RESOLVED, IncidentStatus.ANALYZING])
async def test_wrong_incident_state_is_a_conflict(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, state: IncidentStatus
) -> None:
    incident_id = await simulate(client)
    incident = await db.get(Incident, incident_id)
    assert incident is not None
    incident.status = state
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 409
    assert "only DETECTED incidents" in response.json()["detail"]
    assert fake.calls == []


async def test_unknown_incident(client: AsyncClient, fake: FakeProvider) -> None:
    investigate = await client.post("/api/incidents/999999/investigate")
    events = await client.get("/api/incidents/999999/events")
    invalid = await client.post("/api/incidents/abc/investigate")

    assert investigate.status_code == events.status_code == 404
    assert investigate.json() == {"detail": "Incident 999999 not found"}
    assert invalid.status_code == 422


# --- failures -----------------------------------------------------------------------------------


async def assert_failed_and_retryable(
    client: AsyncClient, db: AsyncSession, incident_id: int
) -> list[dict[str, Any]]:
    run = await db.scalar(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id)
        .execution_options(populate_existing=True)
    )
    assert run is not None and run.status is AgentRunStatus.FAILED
    assert run.output is None
    assert await incident_status(db, incident_id) is IncidentStatus.DETECTED
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert events[-1]["event_type"] == "error"
    return events


async def test_malformed_ai_output_fails_safely_and_can_be_retried(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    fake.error = AIStructuredOutputError("Gemini output did not match InvestigationAnalysis")

    failed = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert failed.status_code == 502
    assert "AI analysis failed" in failed.json()["detail"]
    assert "can be retried" in failed.json()["detail"]
    await assert_failed_and_retryable(client, db, incident_id)

    fake.error = None
    retried = await client.post(f"/api/incidents/{incident_id}/investigate")
    assert retried.status_code == 201
    assert retried.json()["status"] == "COMPLETED"


async def test_invalid_structured_result_is_rejected(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    fake.result = {**GOOD_ANALYSIS, "confidence": 7, "next_step": "rollback_now"}

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 502
    assert "did not match InvestigationAnalysis" in response.json()["detail"]
    await assert_failed_and_retryable(client, db, incident_id)


async def test_findings_citing_unknown_evidence_are_dropped(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    fake.result = {
        **GOOD_ANALYSIS,
        "findings": [
            *GOOD_ANALYSIS["findings"],
            {"kind": "observation", "statement": "Disk is full.", "evidence_ids": ["L99"]},
        ],
    }

    body = (await client.post(f"/api/incidents/{incident_id}/investigate")).json()

    statements = [f["statement"] for f in body["result"]["findings"]]
    assert "Disk is full." not in statements
    assert len(statements) == 3
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert events[-1]["metadata"]["unsupported_findings_dropped"] == 1


async def test_output_without_supported_findings_fails(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    fake.result = {
        **GOOD_ANALYSIS,
        "findings": [{"kind": "observation", "statement": "Made up.", "evidence_ids": ["X1"]}],
    }

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 502
    assert "did not cite any of the supplied evidence" in response.json()["detail"]
    await assert_failed_and_retryable(client, db, incident_id)


async def test_ai_rate_limit_is_reported(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    fake.error = AIRateLimitError("Gemini request failed after 3 attempt(s): rate limit")

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 502
    assert "rate limit" in response.json()["detail"]
    await assert_failed_and_retryable(client, db, incident_id)


async def test_ai_not_configured(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_keys() -> None:
        raise AIConfigurationError("AI_PROVIDER=gemini but no Gemini API key is configured")

    monkeypatch.setattr(investigation, "get_ai_provider", no_keys)
    incident_id = await simulate(client)

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
    await assert_failed_and_retryable(client, db, incident_id)


async def test_telemetry_failure(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT", {}, Exception("postgres://user:secret@host"))

    incident_id = await simulate(client)
    monkeypatch.setattr(telemetry, "list_logs", broken)

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 503
    assert "Evidence collection failed" in response.json()["detail"]
    assert "secret" not in response.text
    assert fake.calls == []  # no AI call without evidence
    await assert_failed_and_retryable(client, db, incident_id)


async def test_transient_database_error_during_evidence_is_retried(
    client: AsyncClient, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the executor's rollback expires ORM objects; the agent must keep working.
    real = telemetry.list_logs
    failures = iter([True])

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        if next(failures, False):
            raise OperationalError("SELECT", {}, Exception("connection reset"))
        return await real(*args, **kwargs)

    incident_id = await simulate(client)
    monkeypatch.setattr(telemetry, "list_logs", flaky)

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 201
    assert response.json()["status"] == "COMPLETED"


async def test_unexpected_error_is_generic(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await simulate(client)
    fake.error = RuntimeError("internal detail /etc/secret")

    response = await client.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 500
    assert "failed unexpectedly" in response.json()["detail"]
    assert "/etc/secret" not in response.text
    await assert_failed_and_retryable(client, db, incident_id)
