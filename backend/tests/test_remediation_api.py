"""Remediation Agent end to end through the API, with a fake AI provider.

Phase 7 stops at the approval boundary: proposals and PENDING approvals only, no execution.
"""

import asyncio
from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import investigation, remediation, root_cause
from app.ai.errors import AIConfigurationError, AIRateLimitError
from app.db.session import SessionLocal
from app.models import AgentRun, Approval, Deployment, Incident
from app.models.enums import (
    ActionType,
    AgentName,
    AgentRunStatus,
    ApprovalStatus,
    DeploymentStatus,
    IncidentStatus,
    RiskLevel,
)
from app.schemas.remediation import RemediationProposal, RemediationResult
from app.services import telemetry
from app.services.remediation_policy import POLICY
from app.tools import ToolExecutor, ToolPermissionError
from tests.investigation_fakes import GOOD_REMEDIATION, FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    for module in (investigation, root_cause, remediation):
        monkeypatch.setattr(module, "get_ai_provider", lambda: provider)
    return provider


async def analyzed(client: AsyncClient) -> int:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    assert (await client.post(f"/api/incidents/{incident_id}/investigate")).status_code == 201
    assert (await client.post(f"/api/incidents/{incident_id}/analyze")).status_code == 201
    return incident_id


def propose(fake: FakeProvider, **changes: Any) -> None:
    fake.results["RemediationProposal"] = {**GOOD_REMEDIATION, **changes}


async def status_of(incident_id: int) -> IncidentStatus:
    async with SessionLocal() as db:
        incident = await db.get(Incident, incident_id)
        assert incident is not None
        return incident.status


async def approvals(db: AsyncSession, incident_id: int) -> list[Approval]:
    rows = await db.scalars(
        select(Approval)
        .where(Approval.incident_id == incident_id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def remediation_runs(db: AsyncSession, incident_id: int) -> list[AgentRun]:
    rows = await db.scalars(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id, AgentRun.agent_name == AgentName.REMEDIATION)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def assert_nothing_executed(client: AsyncClient, db: AsyncSession) -> None:
    latest = (await client.get("/api/services/payment-api/deployments")).json()[0]
    assert (latest["version"], latest["status"]) == ("v1.8.2", "SUCCEEDED")
    rolled_back = select(func.count()).where(Deployment.status == DeploymentStatus.ROLLED_BACK)
    assert await db.scalar(rolled_back) == 0
    assert (await client.get("/api/services/payment-api/health")).json()["status"] == "DEGRADED"


# --- happy path: rollback proposal + pending approval -------------------------------------------


async def test_rollback_proposal_creates_pending_approval(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)

    response = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["agent"], body["incident_status"]) == (
        "COMPLETED",
        "remediation",
        "AWAITING_APPROVAL",
    )
    result = RemediationResult.model_validate(body["result"])
    assert result.status == "approval_required"
    assert result.action is ActionType.ROLLBACK_DEPLOYMENT
    assert result.target == "v1.8.2"  # the version being rolled back (CLAUDE.md convention)
    assert result.parameters == {
        "service": "payment-api",
        "from_version": "v1.8.2",
        "to_version": "v1.8.1",
    }
    assert result.risk is RiskLevel.MEDIUM
    assert result.requires_approval is True
    assert result.executed is False
    assert [e.id for e in result.supporting_evidence] == GOOD_REMEDIATION["supporting_evidence"]

    approval = body["approval"]
    assert approval["id"] == result.approval_id
    assert (approval["status"], approval["action_type"], approval["target"], approval["risk"]) == (
        "PENDING",
        "ROLLBACK_DEPLOYMENT",
        "v1.8.2",
        "MEDIUM",
    )
    assert approval["parameters"]["to_version"] == "v1.8.1"
    assert approval["decided_at"] is None

    stored = await approvals(db, incident_id)
    assert len(stored) == 1 and stored[0].status is ApprovalStatus.PENDING
    assert stored[0].parameters["remediation_run_id"] == body["run_id"]
    run = (await remediation_runs(db, incident_id))[0]
    assert run.status is AgentRunStatus.COMPLETED
    assert run.summary == "roll back payment-api from v1.8.2 to v1.8.1"
    await assert_nothing_executed(client, db)


async def test_prompt_uses_stored_rca_and_real_deployment_options(
    client: AsyncClient, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await analyzed(client)

    async def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("remediation must not re-collect logs or health")

    monkeypatch.setattr(telemetry, "list_logs", forbidden)
    monkeypatch.setattr(telemetry, "latest_health", forbidden)

    assert (await client.post(f"/api/incidents/{incident_id}/remediate")).status_code == 201

    prompts = fake.calls_for(RemediationProposal)
    assert len(prompts) == 1
    prompt = prompts[0]
    assert "Active deployment: v1.8.2" in prompt
    assert "Rollback candidates: v1.8.1" in prompt and "v1.8.0" in prompt
    assert "Root cause (configuration_error" in prompt
    assert "Evidence:" in prompt and "L3 " in prompt
    assert "Access token issued" not in prompt  # only RCA-cited evidence + deployments
    assert len(prompt) < 4000
    options = fake.calls[-1][2]
    assert options is not None and options.temperature == 0.0
    assert "never execute" in (options.system_instruction or "")


async def test_events(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await analyzed(client)
    await client.post(f"/api/incidents/{incident_id}/remediate")

    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    ours = [e for e in events if e["agent"] == "remediation"]

    assert [e["event_type"] for e in ours] == [
        "agent_started",
        "remediation_analysis_started",
        "remediation_recommended",
        "approval_required",
    ]
    assert ours[1]["metadata"]["rollback_candidates"] == ["v1.8.1", "v1.8.0"]
    assert ours[2]["metadata"]["risk"] == "MEDIUM"
    assert ours[3]["metadata"]["approval_id"] is not None
    types = {e["event_type"] for e in events}
    # A proposal is not a remediation: nothing started, completed or approved.
    assert not types & {"remediation_started", "remediation_completed", "approval_received"}
    timestamps = [datetime.fromisoformat(e["timestamp"]) for e in events]
    assert timestamps == sorted(timestamps)


async def test_get_remediation(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await analyzed(client)
    missing = await client.get(f"/api/incidents/{incident_id}/remediation")
    created = (await client.post(f"/api/incidents/{incident_id}/remediate")).json()

    fetched = await client.get(f"/api/incidents/{incident_id}/remediation")

    assert missing.status_code == 404
    assert fetched.status_code == 200
    assert fetched.json() == created
    assert (await client.get("/api/incidents/999999/remediation")).status_code == 404


# --- policy: every action -----------------------------------------------------------------------


def test_policy_decides_risk_and_approval() -> None:
    assert POLICY[ActionType.ROLLBACK_DEPLOYMENT].requires_approval
    assert POLICY[ActionType.RESTART_SERVICE].requires_approval
    assert not POLICY[ActionType.NO_ACTION].requires_approval
    assert not POLICY[ActionType.ESCALATE_TO_HUMAN].requires_approval
    assert POLICY[ActionType.ROLLBACK_DEPLOYMENT].risk is RiskLevel.MEDIUM
    # The model's schema has no way to set risk or approval itself.
    fields = set(RemediationProposal.model_json_schema()["properties"])
    assert not fields & {"risk", "requires_approval", "approved", "execute", "command"}


async def test_restart_requires_approval(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    propose(fake, action="RESTART_SERVICE", target_service="payment-api", rollback_to_version=None)

    body = (await client.post(f"/api/incidents/{incident_id}/remediate")).json()

    assert body["result"]["requires_approval"] is True
    assert body["result"]["parameters"] == {"service": "payment-api"}
    assert body["approval"]["action_type"] == "RESTART_SERVICE"
    assert body["incident_status"] == "AWAITING_APPROVAL"
    assert len(await approvals(db, incident_id)) == 1


async def test_no_action_creates_no_approval(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    propose(fake, action="NO_ACTION", rollback_to_version=None)

    body = (await client.post(f"/api/incidents/{incident_id}/remediate")).json()

    assert body["result"]["status"] == "no_action"
    assert body["result"]["requires_approval"] is False
    assert body["approval"] is None
    assert body["incident_status"] == "ANALYZING"
    assert await approvals(db, incident_id) == []


async def test_escalation_executes_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    propose(fake, action="ESCALATE_TO_HUMAN", rollback_to_version=None, supporting_evidence=[])

    body = (await client.post(f"/api/incidents/{incident_id}/remediate")).json()

    assert body["result"]["status"] == "escalated"
    assert body["approval"] is None
    assert body["incident_status"] == "ESCALATED"
    assert await approvals(db, incident_id) == []
    await assert_nothing_executed(client, db)
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert events[-1]["event_type"] == "incident_escalated"


# --- policy: invalid proposals are rejected -----------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"rollback_to_version": "v9.9.9"}, "v9.9.9 is not a previous successful deployment"),
        ({"rollback_to_version": "v1.8.2"}, "v1.8.2 is already the active version"),
        ({"rollback_to_version": "v2.3.1"}, "v2.3.1 is not a previous successful deployment"),
        ({"rollback_to_version": None}, "did not name a version"),
        ({"target_service": "auth-api"}, "is not the affected service"),
        ({"target_service": "billing-api"}, "is not the affected service"),
        ({"supporting_evidence": ["L99", "X1"]}, "did not cite any of the supplied evidence"),
        (
            {"action": "RESTART_SERVICE", "target_service": "billing-api"},
            "restart target 'billing-api' is not payment-api or one of its dependencies",
        ),
        (
            {"action": "NO_ACTION", "supporting_evidence": []},
            "NO_ACTION proposal did not cite any of the supplied evidence",
        ),
    ],
)
async def test_invalid_proposals_are_rejected(
    client: AsyncClient,
    db: AsyncSession,
    fake: FakeProvider,
    changes: dict[str, Any],
    message: str,
) -> None:
    incident_id = await analyzed(client)
    propose(fake, **changes)

    response = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Proposal rejected by backend policy" in detail
    assert message in detail
    assert await approvals(db, incident_id) == []
    assert await status_of(incident_id) is IncidentStatus.ANALYZING
    run = (await remediation_runs(db, incident_id))[-1]
    assert run.status is AgentRunStatus.FAILED and run.output is None
    await assert_nothing_executed(client, db)


async def test_unsupported_action_type_is_rejected(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    propose(fake, action="DROP_DATABASE")

    response = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert response.status_code == 502
    assert "did not match RemediationProposal" in response.json()["detail"]
    assert await approvals(db, incident_id) == []


async def test_rejected_proposal_can_be_retried(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await analyzed(client)
    propose(fake, rollback_to_version="v9.9.9")
    assert (await client.post(f"/api/incidents/{incident_id}/remediate")).status_code == 502

    propose(fake)
    retried = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert retried.status_code == 201
    assert retried.json()["incident_status"] == "AWAITING_APPROVAL"


async def test_rollback_to_rolled_back_or_failed_version_is_rejected(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    v181 = await db.scalar(
        select(Deployment).where(
            Deployment.service_name == "payment-api", Deployment.version == "v1.8.1"
        )
    )
    assert v181 is not None
    v181.status = DeploymentStatus.FAILED
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert response.status_code == 502
    assert "v1.8.1 is not a previous successful deployment" in response.json()["detail"]


# --- preconditions, state, duplicates -----------------------------------------------------------


async def test_requires_completed_rca(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    before = await client.post(f"/api/incidents/{incident_id}/remediate")
    await client.post(f"/api/incidents/{incident_id}/investigate")
    after_investigation = await client.post(f"/api/incidents/{incident_id}/remediate")
    fake.error = AIRateLimitError("rate limit")
    await client.post(f"/api/incidents/{incident_id}/analyze")  # RCA FAILED
    after_failed_rca = await client.post(f"/api/incidents/{incident_id}/remediate")

    for response in (before, after_investigation, after_failed_rca):
        assert response.status_code == 409
        assert "no completed root cause analysis" in response.json()["detail"]
    assert fake.calls_for(RemediationProposal) == []


async def test_wrong_state_is_a_conflict(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    incident = await db.get(Incident, incident_id)
    assert incident is not None
    incident.status = IncidentStatus.RESOLVED
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert response.status_code == 409
    assert "remediation requires ANALYZING" in response.json()["detail"]


async def test_unknown_incident(client: AsyncClient, fake: FakeProvider) -> None:
    response = await client.post("/api/incidents/999999/remediate")

    assert response.status_code == 404


async def test_repeat_request_returns_existing_proposal(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    first = await client.post(f"/api/incidents/{incident_id}/remediate")

    second = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(fake.calls_for(RemediationProposal)) == 1
    assert len(await approvals(db, incident_id)) == 1
    assert len(await remediation_runs(db, incident_id)) == 1


async def test_running_proposal_is_a_conflict(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    db.add(
        AgentRun(
            incident_id=incident_id,
            agent_name=AgentName.REMEDIATION,
            status=AgentRunStatus.RUNNING,
            started_at=datetime.now().astimezone(),
        )
    )
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]


async def test_concurrent_requests_create_one_proposal(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await analyzed(client)
    original = remediation._completed

    async def slow_precondition(*args: Any, **kwargs: Any) -> Any:
        # Hold both requests after their early duplicate checks, so they race for the lock.
        result = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(remediation, "_completed", slow_precondition)

    responses = await asyncio.gather(
        client.post(f"/api/incidents/{incident_id}/remediate"),
        client.post(f"/api/incidents/{incident_id}/remediate"),
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    assert "already started" in next(r for r in responses if r.status_code == 409).text
    assert len(await remediation_runs(db, incident_id)) == 1
    assert len(await approvals(db, incident_id)) == 1
    assert len(fake.calls_for(RemediationProposal)) == 1


# --- approval boundary --------------------------------------------------------------------------


async def test_client_cannot_supply_or_approve_an_action(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)

    # Any body is ignored: the action always comes from the validated server-side proposal.
    body = (
        await client.post(
            f"/api/incidents/{incident_id}/remediate",
            json={"action": "RESTART_SERVICE", "target": "database", "command": "rm -rf /"},
        )
    ).json()
    assert body["result"]["action"] == "ROLLBACK_DEPLOYMENT"

    # There is no direct execute/rollback API (approval goes through /approve, see Phase 8).
    for path in ("execute", "rollback"):
        response = await client.post(f"/api/incidents/{incident_id}/{path}", json={})
        assert response.status_code in (404, 405)
    assert [a.status for a in await approvals(db, incident_id)] == [ApprovalStatus.PENDING]
    await assert_nothing_executed(client, db)


@pytest.mark.parametrize("agent", [AgentName.REMEDIATION, AgentName.INVESTIGATION])
@pytest.mark.parametrize("tool", ["rollback_deployment", "restart_service"])
async def test_no_agent_can_run_an_action_tool(
    db: AsyncSession, agent: AgentName, tool: str
) -> None:
    executor = ToolExecutor(agent, db, max_calls=8, max_retries=0)

    with pytest.raises(ToolPermissionError):
        await executor.call(tool, service="payment-api", version="v1.8.1")


# --- failures -----------------------------------------------------------------------------------


async def test_ai_not_configured_is_503(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await analyzed(client)

    def no_keys() -> None:
        raise AIConfigurationError("no Gemini API key is configured")

    monkeypatch.setattr(remediation, "get_ai_provider", no_keys)

    response = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert response.status_code == 503
    assert await status_of(incident_id) is IncidentStatus.ANALYZING
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert (events[-1]["event_type"], events[-1]["agent"]) == ("error", "remediation")


async def test_unexpected_error_is_generic_and_leaks_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await analyzed(client)
    fake.error = RuntimeError("api_key=AIzaSyTOPSECRET postgres://u:pw@host")

    response = await client.post(f"/api/incidents/{incident_id}/remediate")

    assert response.status_code == 500
    assert "failed unexpectedly" in response.json()["detail"]
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    everything = response.text + str(events)
    assert "TOPSECRET" not in everything and "pw@host" not in everything
    assert await approvals(db, incident_id) == []
    assert await status_of(incident_id) is IncidentStatus.ANALYZING
