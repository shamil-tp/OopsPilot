"""Phase 12 final QA: cross-phase state machine, AI call budget, and concurrency not covered by the
per-phase test files."""

import asyncio
from collections import Counter

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import investigation, remediation, root_cause, verification
from app.models import AgentRun, Approval, CicdEvent, Deployment
from app.models.enums import AgentName
from tests.investigation_fakes import FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    for module in (investigation, root_cause, remediation, verification):
        monkeypatch.setattr(module, "get_ai_provider", lambda: provider)
    return provider


def ai_calls(fake: FakeProvider) -> Counter[str]:
    return Counter(model.__name__ for _, model, _ in fake.calls)


async def status(client: AsyncClient, incident_id: int) -> str:
    return (await client.get(f"/api/incidents/{incident_id}")).json()["status"]


async def count(db: AsyncSession, model: type, *where: object) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


async def test_each_phase_makes_exactly_its_intended_ai_calls(
    client: AsyncClient, fake: FakeProvider
) -> None:
    """Investigation 1, RCA 1, remediation 1, verification 1; approval, execution, report,
    simulation (incl. CI/CD replay), reads and repeated requests 0."""
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    assert ai_calls(fake) == Counter()

    expected = {
        "investigate": "InvestigationAnalysis",
        "analyze": "RootCauseAnalysis",
        "remediate": "RemediationProposal",
        "approve": None,
        "verify": "VerificationExplanation",
    }
    budget: Counter[str] = Counter()
    for step, model in expected.items():
        assert (await client.post(f"/api/incidents/{incident_id}/{step}")).status_code in (200, 201)
        if model:
            budget[model] += 1
        assert ai_calls(fake) == budget, step
        # Repeating a completed step returns the stored result without another AI call.
        assert (await client.post(f"/api/incidents/{incident_id}/{step}")).status_code == 200
        assert ai_calls(fake) == budget, f"repeated {step}"

    for path in ("report", "investigation", "analysis", "remediation", "execution", "events"):
        assert (await client.get(f"/api/incidents/{incident_id}/{path}")).status_code == 200
    assert (await client.get("/api/cicd/events")).status_code == 200
    assert (
        ai_calls(fake)
        == budget
        == Counter(
            InvestigationAnalysis=1,
            RootCauseAnalysis=1,
            RemediationProposal=1,
            VerificationExplanation=1,
        )
    )
    assert await status(client, incident_id) == "RESOLVED"


async def test_illegal_transitions_are_rejected_and_change_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    base = f"/api/incidents/{incident_id}"

    # DETECTED: nothing but investigation may run; no shortcut to RESOLVED/VERIFYING.
    for step in ("analyze", "remediate", "approve", "reject", "verify"):
        assert (await client.post(f"{base}/{step}")).status_code in (404, 409), step
    assert (await client.get(f"{base}/report")).status_code == 409
    assert await status(client, incident_id) == "DETECTED"

    await client.post(f"{base}/investigate")
    # INVESTIGATING (investigation done): no remediation, approval or verification yet.
    for step in ("remediate", "approve", "verify"):
        assert (await client.post(f"{base}/{step}")).status_code in (404, 409), step
    assert await status(client, incident_id) == "INVESTIGATING"

    await client.post(f"{base}/analyze")
    # ANALYZING: cannot jump to verification or resolution.
    for step in ("approve", "verify"):
        assert (await client.post(f"{base}/{step}")).status_code in (404, 409), step
    assert await status(client, incident_id) == "ANALYZING"

    await client.post(f"{base}/remediate")
    # AWAITING_APPROVAL: nothing is executed or verified without a human decision.
    assert (await client.post(f"{base}/verify")).status_code == 409
    assert await status(client, incident_id) == "AWAITING_APPROVAL"
    assert await count(db, Deployment, Deployment.status == "ROLLED_BACK") == 0

    await client.post(f"{base}/approve")
    await client.post(f"{base}/verify")
    assert await status(client, incident_id) == "RESOLVED"

    # RESOLVED: rejection conflicts; repeated commands return stored results and execute nothing.
    assert (await client.post(f"{base}/reject")).status_code == 409
    for step in ("investigate", "analyze", "remediate", "approve", "verify"):
        assert (await client.post(f"{base}/{step}")).status_code == 200, step
    assert await status(client, incident_id) == "RESOLVED"
    assert await count(db, Deployment, Deployment.status == "ROLLED_BACK") == 1
    assert await count(db, Approval) == 1


async def test_rejected_remediation_is_never_executed_or_verified(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    base = f"/api/incidents/{incident_id}"
    for step in ("investigate", "analyze", "remediate"):
        await client.post(f"{base}/{step}")

    assert (await client.post(f"{base}/reject")).status_code == 200
    assert (await client.post(f"{base}/approve")).status_code == 409
    assert (await client.post(f"{base}/verify")).status_code == 409
    assert (await client.get(f"{base}/report")).status_code == 409
    assert await status(client, incident_id) == "ESCALATED"
    assert await count(db, Deployment, Deployment.status == "ROLLED_BACK") == 0


async def test_concurrent_investigations_run_once(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    fake.delay = 0.3  # keep the first investigation running while the second request arrives

    responses = await asyncio.gather(
        *(client.post(f"/api/incidents/{incident_id}/investigate") for _ in range(2))
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    runs = await count(
        db,
        AgentRun,
        AgentRun.incident_id == incident_id,
        AgentRun.agent_name == AgentName.INVESTIGATION,
    )
    assert runs == 1
    assert ai_calls(fake) == Counter(InvestigationAnalysis=1)


async def test_reset_is_deterministic(client: AsyncClient, db: AsyncSession) -> None:
    """Three resets in a row (with a full simulation in between) give the same starting state."""
    states = []
    for _ in range(3):
        await client.post("/api/incidents/simulate")
        reset = await client.post("/api/demo/reset")
        assert reset.status_code == 200
        deployments = (await client.get("/api/services/payment-api/deployments")).json()
        services = {s["name"]: s["status"] for s in (await client.get("/api/services")).json()}
        states.append(
            (
                [(d["version"], d["status"], d["commit_sha"]) for d in deployments],
                services,
                (await client.get("/api/incidents")).json(),
                await count(db, CicdEvent),
            )
        )
    assert states[0] == states[1] == states[2]
    versions, services, incidents, cicd_events = states[0]
    assert versions[0] == ("v1.8.1", "SUCCEEDED", "8f2d6b1")  # v1.8.1 active
    assert set(services.values()) == {"HEALTHY"}
    assert incidents == [] and cicd_events == 0
