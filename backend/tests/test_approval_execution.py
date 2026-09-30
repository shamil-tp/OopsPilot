"""Human approval gate + simulated remediation execution (Phase 8). No AI is involved here."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import approval as approval_gate
from app.agents import investigation, remediation, root_cause
from app.agents.common import AgentConflictError, AgentFailedError
from app.db.session import SessionLocal
from app.models import AgentRun, Approval, Deployment, Incident
from app.models.enums import (
    AgentName,
    AgentRunStatus,
    ApprovalStatus,
    DeploymentStatus,
    IncidentStatus,
)
from app.schemas.common import as_utc
from app.services import simulator
from tests.investigation_fakes import GOOD_REMEDIATION, FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    for module in (investigation, root_cause, remediation):
        monkeypatch.setattr(module, "get_ai_provider", lambda: provider)
    return provider


async def awaiting_approval(client: AsyncClient) -> int:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    for step in ("investigate", "analyze", "remediate"):
        assert (await client.post(f"/api/incidents/{incident_id}/{step}")).status_code == 201
    return incident_id


async def status_of(incident_id: int) -> IncidentStatus:
    async with SessionLocal() as db:
        incident = await db.get(Incident, incident_id)
        assert incident is not None
        return incident.status


async def payment_deployments(db: AsyncSession) -> list[tuple[str, DeploymentStatus]]:
    rows = await db.scalars(
        select(Deployment)
        .where(Deployment.service_name == "payment-api")
        .order_by(Deployment.timestamp.desc(), Deployment.id.desc())
        .execution_options(populate_existing=True)
    )
    return [(d.version, d.status) for d in rows]


async def execution_runs(db: AsyncSession, incident_id: int) -> list[AgentRun]:
    rows = await db.scalars(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id, AgentRun.agent_name == AgentName.ORCHESTRATOR)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def the_approval(db: AsyncSession, incident_id: int) -> Approval:
    approval = await approval_gate.latest_approval(db, incident_id)
    assert approval is not None
    return approval


BEFORE = [
    ("v1.8.2", DeploymentStatus.SUCCEEDED),
    ("v1.8.1", DeploymentStatus.SUCCEEDED),
    ("v1.8.0", DeploymentStatus.SUCCEEDED),
]


async def assert_unchanged(client: AsyncClient, db: AsyncSession) -> None:
    assert await payment_deployments(db) == BEFORE
    health = (await client.get("/api/services/payment-api/health")).json()
    assert (health["status"], health["error_rate"]) == ("DEGRADED", 37.0)


# --- approve: rollback --------------------------------------------------------------------------


async def test_approve_executes_the_approved_rollback(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)
    ai_calls = len(fake.calls)
    await assert_unchanged(client, db)  # nothing happens before approval

    response = await client.post(f"/api/incidents/{incident_id}/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["incident_status"] == "VERIFYING"
    approval = body["approval"]
    assert (approval["status"], approval["action_type"], approval["target"]) == (
        "APPROVED",
        "ROLLBACK_DEPLOYMENT",
        "v1.8.2",
    )
    assert approval["decided_at"] is not None
    execution = body["execution"]
    assert execution["status"] == "COMPLETED"
    result = execution["result"]
    assert result["parameters"] == {
        "service": "payment-api",
        "from_version": "v1.8.2",
        "to_version": "v1.8.1",
    }
    assert result["simulated"] is True and result["next_step"] == "verification"
    assert result["before"] == {
        "active_version": "v1.8.2",
        "status": "DEGRADED",
        "error_rate": 37.0,
        "latency_ms": 2800.0,
    }
    assert result["after"] == {
        "active_version": "v1.8.1",
        "status": "HEALTHY",
        "error_rate": 0.8,
        "latency_ms": 180.0,
    }
    assert execution["summary"] == "rolled back payment-api from v1.8.2 to v1.8.1"

    # The simulated environment changed consistently.
    assert await payment_deployments(db) == [
        ("v1.8.1", DeploymentStatus.SUCCEEDED),  # redeployed (same commit)
        ("v1.8.2", DeploymentStatus.ROLLED_BACK),
        ("v1.8.1", DeploymentStatus.SUCCEEDED),
        ("v1.8.0", DeploymentStatus.SUCCEEDED),
    ]
    deployments = (await client.get("/api/services/payment-api/deployments")).json()
    assert deployments[0]["commit_sha"] == deployments[2]["commit_sha"] == "8f2d6b1"
    health = (await client.get("/api/services/payment-api/health")).json()
    assert (health["status"], health["error_rate"], health["latency_ms"]) == (
        "HEALTHY",
        0.8,
        180.0,
    )
    logs = [e["message"] for e in (await client.get("/api/services/payment-api/logs")).json()]
    assert "Rollback started: v1.8.2 -> v1.8.1" in logs

    assert len(fake.calls) == ai_calls  # no AI call to approve or execute
    assert await status_of(incident_id) is IncidentStatus.VERIFYING  # not RESOLVED: Phase 9
    remediation_view = (await client.get(f"/api/incidents/{incident_id}/remediation")).json()
    assert remediation_view["approval"]["status"] == "APPROVED"
    assert remediation_view["status"] == "COMPLETED"  # the Phase 7 proposal run is untouched
    fetched = await client.get(f"/api/incidents/{incident_id}/execution")
    assert fetched.status_code == 200 and fetched.json() == execution


async def test_events_form_an_audit_trail(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await awaiting_approval(client)
    await client.post(f"/api/incidents/{incident_id}/approve")

    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    ours = [e for e in events if e["agent"] == "orchestrator"]

    assert [e["event_type"] for e in ours] == [
        "approval_received",
        "remediation_started",
        "remediation_completed",
    ]
    received, started, completed = (e["metadata"] for e in ours)
    assert received["decision"] == "APPROVED"
    assert received["incident_status"] == {"from": "AWAITING_APPROVAL", "to": "REMEDIATING"}
    assert started["parameters"]["to_version"] == "v1.8.1"
    assert started["approval_id"] == received["approval_id"] == completed["approval_id"]
    assert completed["after"]["active_version"] == "v1.8.1"
    assert completed["next_step"] == "verification"
    types = {e["event_type"] for e in events}
    assert not types & {"verification_started", "incident_resolved", "error"}


async def test_request_body_cannot_change_what_is_executed(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)

    body = (
        await client.post(
            f"/api/incidents/{incident_id}/approve",
            json={"action": "RESTART_SERVICE", "to_version": "v1.8.0", "service": "database"},
        )
    ).json()

    assert body["execution"]["result"]["parameters"]["to_version"] == "v1.8.1"
    assert (await payment_deployments(db))[0] == ("v1.8.1", DeploymentStatus.SUCCEEDED)
    for path in ("execute", "rollback"):
        response = await client.post(f"/api/incidents/{incident_id}/{path}")
        assert response.status_code in (404, 405)


# --- approve: restart ---------------------------------------------------------------------------


async def test_approved_restart_is_simulated_without_inventing_recovery(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    fake.results["RemediationProposal"] = {
        **GOOD_REMEDIATION,
        "action": "RESTART_SERVICE",
        "rollback_to_version": None,
    }
    incident_id = await awaiting_approval(client)

    body = (await client.post(f"/api/incidents/{incident_id}/approve")).json()

    assert body["incident_status"] == "VERIFYING"
    result = body["execution"]["result"]
    assert result["action"] == "RESTART_SERVICE"
    assert result["parameters"] == {"service": "payment-api"}
    # A restart does not change the bad configuration: health is reported as it is.
    assert result["after"]["status"] == "DEGRADED"
    assert await payment_deployments(db) == BEFORE
    logs = [e["message"] for e in (await client.get("/api/services/payment-api/logs")).json()]
    assert "Service restarted" in logs


# --- reject -------------------------------------------------------------------------------------


async def test_reject_escalates_and_executes_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)

    response = await client.post(f"/api/incidents/{incident_id}/reject")

    assert response.status_code == 200
    body = response.json()
    assert body["incident_status"] == "ESCALATED"
    assert body["approval"]["status"] == "REJECTED"
    assert body["approval"]["decided_at"] is not None
    assert body["execution"] is None
    assert await execution_runs(db, incident_id) == []
    await assert_unchanged(client, db)
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    tail = [(e["event_type"], e["metadata"].get("decision")) for e in events[-2:]]
    assert tail == [("approval_received", "REJECTED"), ("incident_escalated", None)]
    assert (await client.get(f"/api/incidents/{incident_id}/execution")).status_code == 404


# --- repeated and conflicting decisions ---------------------------------------------------------


async def test_approving_twice_executes_once(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)
    first = await client.post(f"/api/incidents/{incident_id}/approve")

    second = await client.post(f"/api/incidents/{incident_id}/approve")

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(await execution_runs(db, incident_id)) == 1
    versions = await payment_deployments(db)
    assert versions.count(("v1.8.2", DeploymentStatus.ROLLED_BACK)) == 1
    assert [v for v, _ in versions].count("v1.8.1") == 2  # original + one redeploy


async def test_repeated_rejection_is_idempotent_and_opposite_decision_conflicts(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)
    await client.post(f"/api/incidents/{incident_id}/reject")

    again = await client.post(f"/api/incidents/{incident_id}/reject")
    approve = await client.post(f"/api/incidents/{incident_id}/approve")

    assert again.status_code == 200 and again.json()["approval"]["status"] == "REJECTED"
    assert approve.status_code == 409
    assert "already REJECTED" in approve.json()["detail"]


async def test_reject_after_approval_conflicts(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await awaiting_approval(client)
    await client.post(f"/api/incidents/{incident_id}/approve")

    response = await client.post(f"/api/incidents/{incident_id}/reject")

    assert response.status_code == 409
    assert "already APPROVED" in response.json()["detail"]


async def test_no_approval_request(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]

    for decision in ("approve", "reject"):
        response = await client.post(f"/api/incidents/{incident_id}/{decision}")
        assert response.status_code == 404
        assert "has no approval request" in response.json()["detail"]
    assert (await client.post("/api/incidents/999999/approve")).status_code == 404


async def test_pending_approval_in_wrong_incident_state_conflicts(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)
    incident = await db.get(Incident, incident_id)
    assert incident is not None
    incident.status = IncidentStatus.ANALYZING
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/approve")

    assert response.status_code == 409
    assert (await the_approval(db, incident_id)).status is ApprovalStatus.PENDING


# --- execution re-validation (stale or tampered approvals) --------------------------------------


async def assert_refused(
    client: AsyncClient, db: AsyncSession, incident_id: int, response: Any, message: str
) -> None:
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Execution refused" in detail and message in detail
    assert await status_of(incident_id) is IncidentStatus.FAILED
    run = (await execution_runs(db, incident_id))[0]
    assert run.status is AgentRunStatus.FAILED
    assert set(run.output or {}) == {"approval_id"}  # no execution result was stored
    assert (await the_approval(db, incident_id)).status is ApprovalStatus.APPROVED
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert events[-1]["event_type"] == "error"
    assert "remediation_completed" not in {e["event_type"] for e in events}


async def test_stale_active_deployment_is_refused(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)
    newest = (await payment_deployments(db))[0]
    assert newest[0] == "v1.8.2"
    latest = await db.scalar(select(Deployment.timestamp).where(Deployment.version == "v1.8.2"))
    assert latest is not None
    db.add(
        Deployment(
            service_name="payment-api",
            version="v1.8.3",
            timestamp=as_utc(latest).replace(microsecond=1),
            status=DeploymentStatus.SUCCEEDED,
            commit_sha="aaaaaaa",
        )
    )
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/approve")

    await assert_refused(client, db, incident_id, response, "the approval is stale")
    assert (await payment_deployments(db))[0] == ("v1.8.3", DeploymentStatus.SUCCEEDED)
    assert ("v1.8.2", DeploymentStatus.SUCCEEDED) in await payment_deployments(db)


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ({"to_version": "v9.9.9"}, "v9.9.9 is not a previous successful deployment"),
        ({"to_version": "v2.3.1"}, "v2.3.1 is not a previous successful deployment"),
        ({"service": "auth-api"}, "is not the affected service"),
        ({"to_version": "v1.8.2"}, "v1.8.2 is already the active version"),
    ],
)
async def test_invalid_stored_parameters_are_refused(
    client: AsyncClient,
    db: AsyncSession,
    fake: FakeProvider,
    tamper: dict[str, str],
    message: str,
) -> None:
    incident_id = await awaiting_approval(client)
    approval = await the_approval(db, incident_id)
    approval.parameters = {**approval.parameters, **tamper}
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/approve")

    await assert_refused(client, db, incident_id, response, message)
    await assert_unchanged(client, db)


async def test_target_deployment_that_disappeared_is_refused(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)
    target = await db.scalar(select(Deployment).where(Deployment.version == "v1.8.1"))
    assert target is not None
    await db.delete(target)
    await db.commit()

    response = await client.post(f"/api/incidents/{incident_id}/approve")

    await assert_refused(
        client, db, incident_id, response, "v1.8.1 is not a previous successful deployment"
    )


async def test_execution_requires_an_approved_approval(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)
    approval = await the_approval(db, incident_id)
    # Forge an execution run for the still-PENDING approval (no API can do this).
    incident = await db.get(Incident, incident_id)
    assert incident is not None
    incident.status = IncidentStatus.REMEDIATING
    run = AgentRun(
        incident_id=incident_id,
        agent_name=AgentName.ORCHESTRATOR,
        status=AgentRunStatus.RUNNING,
        started_at=approval.requested_at,
        output={"approval_id": approval.id},
    )
    db.add(run)
    await db.commit()

    with pytest.raises(AgentFailedError, match="is PENDING, not APPROVED"):
        await approval_gate.execute(run.id)

    await assert_unchanged(client, db)


# --- failures -----------------------------------------------------------------------------------


async def test_simulator_failure_changes_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await awaiting_approval(client)
    real = simulator.apply_rollback

    async def half_then_fail(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)  # writes happen ...
        raise OperationalError("INSERT", {}, Exception("postgres://u:pw@host"))  # ... then fail

    monkeypatch.setattr(simulator, "apply_rollback", half_then_fail)

    response = await client.post(f"/api/incidents/{incident_id}/approve")

    assert response.status_code == 503
    assert "pw@host" not in response.text
    assert await status_of(incident_id) is IncidentStatus.FAILED
    await assert_unchanged(client, db)  # the partial writes were rolled back


async def test_unexpected_error_is_generic_and_leaks_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await awaiting_approval(client)

    async def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("api_key=AIzaSyTOPSECRET password=hunter2")

    monkeypatch.setattr(simulator, "apply_rollback", boom)

    response = await client.post(f"/api/incidents/{incident_id}/approve")

    assert response.status_code == 500
    assert "Execution failed unexpectedly" in response.json()["detail"]
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    everything = response.text + str(events)
    assert "TOPSECRET" not in everything and "hunter2" not in everything
    await assert_unchanged(client, db)


# --- concurrency (the race window is forced, not left to timing) ---------------------------------


async def test_concurrent_approvals_produce_one_decision_and_one_execution(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await awaiting_approval(client)
    original = approval_gate.latest_approval

    async def slow_read(*args: Any, **kwargs: Any) -> Any:
        found = await original(*args, **kwargs)
        await asyncio.sleep(0.2)  # both requests see PENDING before either decides
        return found

    monkeypatch.setattr(approval_gate, "latest_approval", slow_read)

    responses = await asyncio.gather(
        client.post(f"/api/incidents/{incident_id}/approve"),
        client.post(f"/api/incidents/{incident_id}/approve"),
    )

    assert sorted(r.status_code for r in responses) == [200, 409]
    assert "decided concurrently" in next(r for r in responses if r.status_code == 409).text
    assert len(await execution_runs(db, incident_id)) == 1
    assert (await payment_deployments(db)).count(("v1.8.2", DeploymentStatus.ROLLED_BACK)) == 1


async def test_concurrent_approve_and_reject_produce_one_decision(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await awaiting_approval(client)
    original = approval_gate.latest_approval

    async def slow_read(*args: Any, **kwargs: Any) -> Any:
        found = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return found

    monkeypatch.setattr(approval_gate, "latest_approval", slow_read)

    responses = await asyncio.gather(
        client.post(f"/api/incidents/{incident_id}/approve"),
        client.post(f"/api/incidents/{incident_id}/reject"),
    )

    assert sorted(r.status_code for r in responses) == [200, 409]
    final = (await the_approval(db, incident_id)).status
    state = await status_of(incident_id)
    assert (final, state) in {
        (ApprovalStatus.APPROVED, IncidentStatus.VERIFYING),
        (ApprovalStatus.REJECTED, IncidentStatus.ESCALATED),
    }


async def test_concurrent_executions_run_once(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await awaiting_approval(client)
    async with SessionLocal() as session:
        _, run, changed = await approval_gate.decide(session, incident_id, approve=True)
    assert changed and run is not None
    real_lock = approval_gate.lock_incident

    async def slow_lock(*args: Any, **kwargs: Any) -> bool:
        await asyncio.sleep(0.2)  # both executors are past their first check
        return await real_lock(*args, **kwargs)

    monkeypatch.setattr(approval_gate, "lock_incident", slow_lock)

    outcomes = await asyncio.gather(
        approval_gate.execute(run.id), approval_gate.execute(run.id), return_exceptions=True
    )

    assert sum(o is None for o in outcomes) == 1
    assert sum(isinstance(o, AgentConflictError) for o in outcomes) == 1
    assert (await execution_runs(db, incident_id))[0].status is AgentRunStatus.COMPLETED
    versions = await payment_deployments(db)
    assert versions.count(("v1.8.2", DeploymentStatus.ROLLED_BACK)) == 1
    assert [v for v, _ in versions].count("v1.8.1") == 2
    assert await status_of(incident_id) is IncidentStatus.VERIFYING


async def test_completed_execution_cannot_run_again(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await awaiting_approval(client)
    await client.post(f"/api/incidents/{incident_id}/approve")
    run = (await execution_runs(db, incident_id))[0]

    with pytest.raises(AgentConflictError, match="not pending execution"):
        await approval_gate.execute(run.id)

    count = select(func.count()).where(Deployment.status == DeploymentStatus.ROLLED_BACK)
    assert await db.scalar(count) == 1
