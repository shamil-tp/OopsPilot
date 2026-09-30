"""Root Cause Analysis Agent end to end through the API, with a fake AI provider."""

import asyncio
from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import investigation, root_cause
from app.agents.common import latest_run
from app.ai.base import GenerationOptions, ModelT
from app.ai.errors import AIConfigurationError, AIRateLimitError, AIStructuredOutputError
from app.db.session import SessionLocal
from app.models import AgentRun, Approval, Deployment, Incident
from app.models.enums import AgentName, AgentRunStatus, DeploymentStatus, IncidentStatus
from app.schemas.root_cause import RootCauseAnalysis, RootCauseResult
from app.services import telemetry
from tests.investigation_fakes import GOOD_RCA, FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setattr(investigation, "get_ai_provider", lambda: provider)
    monkeypatch.setattr(root_cause, "get_ai_provider", lambda: provider)
    return provider


async def investigated(client: AsyncClient) -> int:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    response = await client.post(f"/api/incidents/{incident_id}/investigate")
    assert response.status_code == 201
    return incident_id


async def status_of(incident_id: int) -> IncidentStatus:
    async with SessionLocal() as db:
        incident = await db.get(Incident, incident_id)
        assert incident is not None
        return incident.status


async def rca_runs(db: AsyncSession, incident_id: int) -> list[AgentRun]:
    rows = await db.scalars(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id, AgentRun.agent_name == AgentName.ROOT_CAUSE)
        .execution_options(populate_existing=True)
    )
    return list(rows)


# --- happy path ---------------------------------------------------------------------------------


async def test_analysis_runs_on_stored_investigation(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)
    stored = (await client.post(f"/api/incidents/{incident_id}/investigate")).json()["result"]

    response = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["agent"], body["incident_status"]) == (
        "COMPLETED",
        "root_cause",
        "ANALYZING",
    )
    result = RootCauseResult.model_validate(body["result"])
    assert result.status == "root_cause_identified"
    assert "v1.8.2" in result.root_cause
    assert 0 <= result.confidence <= 1
    assert result.recommended_next_step == "propose_remediation"
    assert result.category == "configuration_error"
    # Citations resolve to the exact evidence the investigation stored.
    evidence = {e["id"]: e for e in stored["evidence"]}
    assert [e.id for e in result.supporting_evidence] == GOOD_RCA["supporting_evidence"]
    for item in result.supporting_evidence:
        assert item.model_dump(mode="json") == evidence[item.id]
    for statement in [*result.causal_chain, *result.contributing_factors]:
        assert set(statement.evidence_ids) <= set(evidence)
    assert len(result.alternative_explanations) == 2
    assert result.model == "fake:fake-model"

    run = (await rca_runs(db, incident_id))[0]
    assert run.status is AgentRunStatus.COMPLETED
    assert run.summary == result.root_cause
    source = await latest_run(db, incident_id, AgentName.INVESTIGATION)
    assert source is not None
    assert result.investigation_run_id == source.id


async def test_prompt_is_a_compact_correlated_timeline(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)

    await client.post(f"/api/incidents/{incident_id}/analyze")

    prompts = fake.calls_for(RootCauseAnalysis)
    assert len(prompts) == 1  # exactly one RCA call
    prompt = prompts[0]
    assert len(prompt) < 4000
    assert "depends on: database, auth-api" in prompt
    assert "Investigation findings" in prompt
    lines = prompt.splitlines()
    timeline = lines[lines.index("Evidence timeline:") + 1 :]
    ids = [line.split()[0] for line in timeline if line[:1] in "LHDP" and line[1:2].isdigit()]
    # One chronological list: v1.8.2 (D1) before the first DB error (L5) before DEGRADED (H1).
    assert ids.index("D1") < ids.index("L3") < ids.index("L5") < ids.index("L8") < ids.index("H1")
    options = fake.calls[-1][2]
    assert options is not None and options.temperature == 0.0
    assert "Consider competing explanations" in (options.system_instruction or "")


async def test_analysis_does_not_recollect_telemetry(
    client: AsyncClient, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await investigated(client)

    async def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("RCA must use the stored investigation, not re-query telemetry")

    for name in ("list_logs", "latest_health", "list_deployments"):
        monkeypatch.setattr(telemetry, name, forbidden)

    response = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert response.status_code == 201


async def test_events_are_stored_in_order(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await investigated(client)
    await client.post(f"/api/incidents/{incident_id}/analyze")

    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    rca = [e for e in events if e["agent"] == "root_cause"]

    assert [e["event_type"] for e in rca] == [
        "agent_started",
        "evidence_evaluated",
        "root_cause_analysis_started",
        "root_cause_identified",
    ]
    timestamps = [datetime.fromisoformat(e["timestamp"]) for e in events]
    assert timestamps == sorted(timestamps)
    identified = rca[-1]["metadata"]
    assert identified["supporting_evidence"] == GOOD_RCA["supporting_evidence"]
    assert identified["unsupported_citations_removed"] == 0
    assert rca[1]["metadata"]["evidence_by_source"]["logs"] > 0


async def test_get_analysis(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await investigated(client)
    missing = await client.get(f"/api/incidents/{incident_id}/analysis")
    created = (await client.post(f"/api/incidents/{incident_id}/analyze")).json()

    fetched = await client.get(f"/api/incidents/{incident_id}/analysis")

    assert missing.status_code == 404
    assert "no root cause analysis yet" in missing.json()["detail"]
    assert fetched.status_code == 200
    assert fetched.json() == created
    assert (await client.get("/api/incidents/999999/analysis")).status_code == 404


# --- state machine ------------------------------------------------------------------------------


async def test_incident_is_analyzing_during_and_after_rca(
    client: AsyncClient, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await investigated(client)
    seen: list[IncidentStatus] = []
    original = fake.generate_structured

    async def spy(
        prompt: str, response_model: type[ModelT], *, options: GenerationOptions | None = None
    ) -> ModelT:
        if response_model is RootCauseAnalysis:
            seen.append(await status_of(incident_id))
        return await original(prompt, response_model, options=options)

    monkeypatch.setattr(fake, "generate_structured", spy)
    assert await status_of(incident_id) is IncidentStatus.INVESTIGATING

    await client.post(f"/api/incidents/{incident_id}/analyze")

    assert seen == [IncidentStatus.ANALYZING]
    assert await status_of(incident_id) is IncidentStatus.ANALYZING  # no further transition


async def test_analysis_requires_completed_investigation(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]

    before = await client.post(f"/api/incidents/{incident_id}/analyze")

    fake.error = AIRateLimitError("rate limit")
    await client.post(f"/api/incidents/{incident_id}/investigate")  # investigation FAILED
    after_failure = await client.post(f"/api/incidents/{incident_id}/analyze")

    for response in (before, after_failure):
        assert response.status_code == 409
        assert "no completed investigation" in response.json()["detail"]
    assert fake.calls_for(RootCauseAnalysis) == []


@pytest.mark.parametrize("state", [IncidentStatus.RESOLVED, IncidentStatus.DETECTED])
async def test_wrong_state_is_a_conflict(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, state: IncidentStatus
) -> None:
    incident_id = await investigated(client)
    incident = await db.get(Incident, incident_id)
    assert incident is not None
    incident.status = state
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert response.status_code == 409
    assert "requires INVESTIGATING" in response.json()["detail"]


async def test_unknown_incident(client: AsyncClient, fake: FakeProvider) -> None:
    response = await client.post("/api/incidents/999999/analyze")

    assert response.status_code == 404
    assert response.json() == {"detail": "Incident 999999 not found"}


# --- duplicates and concurrency -----------------------------------------------------------------


async def test_repeat_request_returns_stored_result_without_ai_call(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)
    first = await client.post(f"/api/incidents/{incident_id}/analyze")

    second = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(fake.calls_for(RootCauseAnalysis)) == 1
    assert len(await rca_runs(db, incident_id)) == 1


async def test_running_analysis_is_a_conflict(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)
    db.add(
        AgentRun(
            incident_id=incident_id,
            agent_name=AgentName.ROOT_CAUSE,
            status=AgentRunStatus.RUNNING,
            started_at=datetime.now().astimezone(),
        )
    )
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]


async def test_concurrent_requests_start_one_run(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)
    fake.delay = 0.3  # keep the first analysis running while the second request arrives

    responses = await asyncio.gather(
        client.post(f"/api/incidents/{incident_id}/analyze"),
        client.post(f"/api/incidents/{incident_id}/analyze"),
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    assert len(await rca_runs(db, incident_id)) == 1
    assert len(fake.calls_for(RootCauseAnalysis)) == 1


# --- guardrails ---------------------------------------------------------------------------------


async def test_fabricated_citations_are_removed(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await investigated(client)
    fake.results["RootCauseAnalysis"] = {
        **GOOD_RCA,
        "supporting_evidence": ["D1", "L99", "L5"],
        "causal_chain": [
            *GOOD_RCA["causal_chain"],
            {"statement": "Disk filled up.", "evidence_ids": ["X9"]},
        ],
        "alternative_explanations": [
            *GOOD_RCA["alternative_explanations"],
            {
                "explanation": "DNS outage",
                "assessment": "ruled_out",
                "reason": "Invented",
                "evidence_ids": ["Z1"],
            },
            {
                "explanation": "Network partition",
                "assessment": "ruled_out",
                "reason": "No evidence either way",
                "evidence_ids": [],
            },
        ],
    }

    body = (await client.post(f"/api/incidents/{incident_id}/analyze")).json()
    result = body["result"]

    assert [e["id"] for e in result["supporting_evidence"]] == ["D1", "L5"]
    assert "Disk filled up." not in [c["statement"] for c in result["causal_chain"]]
    alternatives = {a["explanation"]: a for a in result["alternative_explanations"]}
    assert "DNS outage" not in alternatives  # cited only fabricated evidence
    # Without real evidence an alternative cannot be "ruled out".
    assert alternatives["Network partition"]["assessment"] == "less_likely"
    assert alternatives["Database infrastructure failure"]["assessment"] == "ruled_out"
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert events[-1]["metadata"]["unsupported_citations_removed"] == 3


async def test_root_cause_without_valid_evidence_fails(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)
    fake.results["RootCauseAnalysis"] = {**GOOD_RCA, "supporting_evidence": ["L99", "Q1"]}

    response = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert response.status_code == 502
    assert "did not cite any of the supplied evidence" in response.json()["detail"]
    assert await status_of(incident_id) is IncidentStatus.INVESTIGATING


async def test_out_of_range_confidence_is_rejected(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await investigated(client)
    fake.results["RootCauseAnalysis"] = {**GOOD_RCA, "confidence": 1.7}

    response = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert response.status_code == 502
    assert "did not match RootCauseAnalysis" in response.json()["detail"]


# --- failures -----------------------------------------------------------------------------------


async def assert_restored(
    client: AsyncClient, db: AsyncSession, incident_id: int
) -> list[dict[str, Any]]:
    runs = await rca_runs(db, incident_id)
    assert runs[-1].status is AgentRunStatus.FAILED
    assert runs[-1].output is None
    assert await status_of(incident_id) is IncidentStatus.INVESTIGATING
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert events[-1]["event_type"] == "error"
    assert events[-1]["agent"] == "root_cause"
    return events


async def test_ai_failure_restores_state_and_can_be_retried(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)
    fake.error = AIStructuredOutputError("Gemini output did not match RootCauseAnalysis")

    failed = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert failed.status_code == 502
    assert "back in INVESTIGATING" in failed.json()["detail"]
    await assert_restored(client, db, incident_id)
    failed_view = (await client.get(f"/api/incidents/{incident_id}/analysis")).json()
    assert failed_view["status"] == "FAILED" and failed_view["result"] is None

    fake.error = None
    retried = await client.post(f"/api/incidents/{incident_id}/analyze")
    assert retried.status_code == 201
    assert await status_of(incident_id) is IncidentStatus.ANALYZING


async def test_ai_not_configured_is_503(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await investigated(client)

    def no_keys() -> None:
        raise AIConfigurationError("no Gemini API key is configured")

    monkeypatch.setattr(root_cause, "get_ai_provider", no_keys)

    response = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert response.status_code == 503
    await assert_restored(client, db, incident_id)


async def test_unexpected_error_is_generic_and_leaks_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)
    fake.error = RuntimeError("api_key=AIzaSyTOPSECRET postgres://u:pw@host")

    response = await client.post(f"/api/incidents/{incident_id}/analyze")

    assert response.status_code == 500
    assert "failed unexpectedly" in response.json()["detail"]
    events = await assert_restored(client, db, incident_id)
    everything = response.text + str(events)
    assert "TOPSECRET" not in everything and "pw@host" not in everything


# --- safety -------------------------------------------------------------------------------------


async def test_analysis_never_remediates(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await investigated(client)

    await client.post(f"/api/incidents/{incident_id}/analyze")

    latest = (await client.get("/api/services/payment-api/deployments")).json()[0]
    assert (latest["version"], latest["status"]) == ("v1.8.2", "SUCCEEDED")
    rolled_back = select(func.count()).where(Deployment.status == DeploymentStatus.ROLLED_BACK)
    assert await db.scalar(rolled_back) == 0
    assert await db.scalar(select(func.count()).select_from(Approval)) == 0
    assert (await client.get("/api/services/payment-api/health")).json()["status"] == "DEGRADED"
    assert await status_of(incident_id) not in {
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.REMEDIATING,
        IncidentStatus.VERIFYING,
        IncidentStatus.RESOLVED,
    }
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    rca_types = {e["event_type"] for e in events if e["agent"] == "root_cause"}
    assert not rca_types & {"tool_started", "remediation_started", "approval_required"}


def test_rca_output_schema_offers_no_actions() -> None:
    """The model can only choose a next phase; it has no field to request an action."""
    schema = RootCauseAnalysis.model_json_schema()
    steps = schema["properties"]["recommended_next_step"]["enum"]
    assert set(steps) == {"propose_remediation", "collect_more_evidence", "escalate_to_human"}
    assert not {"action", "tool", "command", "target"} & set(schema["properties"])
