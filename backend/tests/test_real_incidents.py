"""Real applications: Incident Manager detection, automatic response up to the approval gate,
operator-performed remediation, and verification on real health checks. Fake AI only."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import incident_manager, investigation, remediation, root_cause, verification
from app.core.config import MonitoredProject, get_settings
from app.models import AgentRun, CicdEvent, Deployment, Incident, IncidentReport, ServiceHealth
from app.models.enums import DeploymentStatus, IncidentStatus, ServiceStatus, Severity
from app.services.service_catalog import get_service
from tests.investigation_fakes import GOOD_RCA, GOOD_VERIFICATION, FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")

SERVICE = "mallutyping-web"
OLD, NEW = "aaaaaaa", "bbbbbbb"


@pytest.fixture(autouse=True)
async def real(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "github_repository", None)
    monkeypatch.setattr(settings, "auto_respond", True)
    monkeypatch.setattr(settings, "verify_min_checks", 2)
    monkeypatch.setattr(
        settings,
        "monitored_projects",
        [
            MonitoredProject(
                name="Mallu Typing",
                service=SERVICE,
                url="https://mallutyping.example/",
                repository="MrNihalT/mallutyping",
            )
        ],
    )
    for model in (Incident, ServiceHealth, Deployment, CicdEvent):
        await db.execute(delete(model).where(model.service_name == SERVICE))
    await db.commit()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider(
        {
            "summary": "mallutyping-web is down right after deployment bbbbbbb.",
            "findings": [
                {"kind": "observation", "statement": "The site is DOWN.", "evidence_ids": ["H1"]},
                {
                    "kind": "hypothesis",
                    "statement": "Deployment bbbbbbb broke it.",
                    "evidence_ids": ["D1", "H1"],
                },
            ],
            "confidence": 0.7,
            "next_step": "root_cause_analysis",
        }
    )
    provider.results["RootCauseAnalysis"] = GOOD_RCA | {
        "root_cause": "Deployment bbbbbbb made mallutyping-web unavailable.",
        "category": "deployment_regression",
        "supporting_evidence": ["D1", "H1"],
        "causal_chain": [
            {"statement": "bbbbbbb was deployed.", "evidence_ids": ["D1"]},
            {"statement": "Health checks started failing.", "evidence_ids": ["H1"]},
        ],
        "contributing_factors": [],
        "alternative_explanations": [],
    }
    provider.results["RemediationProposal"] = {
        "action": "ROLLBACK_DEPLOYMENT",
        "target_service": SERVICE,
        "rollback_to_version": OLD,
        "reason": "The site failed right after bbbbbbb; aaaaaaa was healthy.",
        "supporting_evidence": ["D1", "H1"],
        "confidence": 0.8,
    }
    provider.results["VerificationExplanation"] = GOOD_VERIFICATION | {
        "supporting_evidence": ["V1", "V3"]
    }
    for module in (investigation, root_cause, remediation, verification):
        monkeypatch.setattr(module, "get_ai_provider", lambda: provider)
    return provider


async def checks(db: AsyncSession, *statuses: ServiceStatus, start: datetime | None = None) -> None:
    at = start or datetime.now(UTC) - timedelta(minutes=len(statuses))
    for i, status in enumerate(statuses):
        db.add(
            ServiceHealth(
                service_name=SERVICE,
                timestamp=at + timedelta(seconds=30 * i),
                status=status,
                error_rate=0 if status is ServiceStatus.HEALTHY else 100,
                latency_ms=300 if status is ServiceStatus.HEALTHY else 10000,
            )
        )
    await db.commit()


async def deployments(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            Deployment(
                service_name=SERVICE,
                version=OLD,
                timestamp=now - timedelta(days=2),
                status=DeploymentStatus.SUCCEEDED,
                commit_sha=OLD + "1" * 33,
            ),
            Deployment(
                service_name=SERVICE,
                version=NEW,
                timestamp=now - timedelta(minutes=10),
                status=DeploymentStatus.SUCCEEDED,
                commit_sha=NEW + "2" * 33,
            ),
        ]
    )
    await db.commit()


def service() -> Any:
    found = get_service(SERVICE)
    assert found is not None
    return found


async def count(db: AsyncSession, model: type, *where: object) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


# --- detection (deterministic) ------------------------------------------------------------------


async def test_one_failed_check_is_not_an_incident(db: AsyncSession) -> None:
    await checks(db, ServiceStatus.HEALTHY, ServiceStatus.DOWN)
    assert await incident_manager.detect(db, service()) is None


async def test_two_down_checks_open_one_high_incident(
    client: AsyncClient, db: AsyncSession
) -> None:
    await checks(db, ServiceStatus.HEALTHY, ServiceStatus.DOWN, ServiceStatus.DOWN)

    incident = await incident_manager.detect(db, service())
    again = await incident_manager.detect(db, service())

    assert incident is not None and again is None  # never a duplicate while one is active
    assert (incident.title, incident.severity, incident.status) == (
        "Mallu Typing is down",
        Severity.HIGH,
        IncidentStatus.DETECTED,
    )
    events = (await client.get(f"/api/incidents/{incident.id}/events")).json()
    assert events[0]["event_type"] == "incident_created"
    assert events[0]["metadata"]["source"] == "health_check"
    assert await count(db, Incident, Incident.service_name == SERVICE) == 1


async def test_three_degraded_checks_open_a_medium_incident(db: AsyncSession) -> None:
    await checks(db, ServiceStatus.DEGRADED, ServiceStatus.DOWN, ServiceStatus.DEGRADED)
    incident = await incident_manager.detect(db, service())
    assert incident is not None
    assert (incident.title, incident.severity) == ("Mallu Typing is degraded", Severity.MEDIUM)


async def test_demo_simulation_is_never_detected_as_real(
    client: AsyncClient, db: AsyncSession
) -> None:
    await client.post("/api/incidents/simulate")  # payment-api is DEGRADED in the simulation
    await checks(db, ServiceStatus.HEALTHY, ServiceStatus.HEALTHY)
    assert await incident_manager.detect(db, service()) is None


# --- automatic response stops at the human ------------------------------------------------------


async def detected_and_proposed(db: AsyncSession, fake: FakeProvider) -> int:
    await deployments(db)
    await checks(db, ServiceStatus.HEALTHY, ServiceStatus.DOWN, ServiceStatus.DOWN)
    incident = await incident_manager.detect(db, service())
    assert incident is not None
    await incident_manager.respond(incident.id)
    return incident.id


async def test_auto_response_stops_at_human_approval(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await detected_and_proposed(db, fake)

    incident = (await client.get(f"/api/incidents/{incident_id}")).json()
    assert incident["status"] == "AWAITING_APPROVAL"
    approval = (await client.get(f"/api/incidents/{incident_id}/remediation")).json()["approval"]
    assert approval["status"] == "PENDING"
    assert (approval["parameters"]["from_version"], approval["parameters"]["to_version"]) == (
        NEW,
        OLD,
    )
    assert len(fake.calls) == 3  # investigation, root cause, remediation: no execution, no verify
    assert await count(db, Deployment, Deployment.status == DeploymentStatus.ROLLED_BACK) == 0


async def test_after_check_runs_the_response_in_the_background(
    db: AsyncSession, fake: FakeProvider
) -> None:
    await deployments(db)
    await checks(db, ServiceStatus.HEALTHY, ServiceStatus.DOWN, ServiceStatus.DOWN)

    await incident_manager.after_check(db, service())
    await asyncio.gather(*list(incident_manager._tasks))

    incident = await db.scalar(select(Incident).where(Incident.service_name == SERVICE))
    assert incident is not None
    await db.refresh(incident)
    assert incident.status is IncidentStatus.AWAITING_APPROVAL


async def test_auto_respond_off_only_detects(
    db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "auto_respond", False)
    await checks(db, ServiceStatus.DOWN, ServiceStatus.DOWN)
    await incident_manager.after_check(db, service())
    assert not incident_manager._tasks and fake.calls == []
    incident = await db.scalar(select(Incident).where(Incident.service_name == SERVICE))
    assert incident is not None and incident.status is IncidentStatus.DETECTED


# --- operator-performed remediation and verification -------------------------------------------


async def test_operator_flow_to_resolution(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await detected_and_proposed(db, fake)
    base = f"/api/incidents/{incident_id}"

    decision = (await client.post(f"{base}/approve")).json()
    assert decision["incident_status"] == "REMEDIATING"
    execution = decision["execution"]
    assert execution["mode"] == "operator" and execution["status"] == "RUNNING"
    assert OLD in execution["instructions"] and "Mark as done" in execution["instructions"]
    assert (
        await count(db, Deployment, Deployment.status == DeploymentStatus.ROLLED_BACK) == 0
    )  # OpsPilot did nothing

    confirmed = (await client.post(f"{base}/execution/confirm")).json()
    assert (
        confirmed["result"]["performed_by"] == "operator"
        and confirmed["result"]["simulated"] is False
    )
    assert (await client.get(base)).json()["status"] == "VERIFYING"
    assert (await client.post(f"{base}/execution/confirm")).status_code == 200  # idempotent

    early = await client.post(f"{base}/verify")
    assert early.status_code == 409 and "0 of 2" in early.json()["detail"]
    assert (await client.get(base)).json()["status"] == "VERIFYING"  # waiting, not failed

    await checks(
        db,
        ServiceStatus.HEALTHY,
        ServiceStatus.HEALTHY,
        start=datetime.now(UTC) + timedelta(seconds=1),
    )
    await incident_manager.verify_when_ready(SERVICE)

    assert (await client.get(base)).json()["status"] == "RESOLVED"
    result = (await client.get(f"{base}/verification")).json()["result"]
    names = {c["name"]: c for c in result["checks"]}
    assert "target_deployment_active" not in names  # not observable for a real host
    assert names["remediation_executed"]["actual"].endswith("performed by an operator")
    assert names["error_rate_recovered"]["actual"] == "2 of 2 succeeded"
    assert all(c["passed"] for c in result["checks"]) and result["recovered"] is True
    report = (await client.get(f"{base}/report")).json()["report"]
    assert (
        report["execution"]["performed_by"] == "operator" and report["outcome"]["recovered"] is True
    )
    assert await count(db, IncidentReport, IncidentReport.incident_id == incident_id) == 1


async def test_failed_checks_after_the_action_are_not_recovery(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await detected_and_proposed(db, fake)
    base = f"/api/incidents/{incident_id}"
    await client.post(f"{base}/approve")
    await client.post(f"{base}/execution/confirm")

    await checks(
        db,
        ServiceStatus.HEALTHY,
        ServiceStatus.DOWN,
        start=datetime.now(UTC) + timedelta(seconds=1),
    )
    await incident_manager.verify_when_ready(SERVICE)

    assert (await client.get(base)).json()["status"] == "FAILED"
    result = (await client.get(f"{base}/verification")).json()["result"]
    assert result["recovered"] is False and "error_rate_recovered" in result["failed_checks"]


async def test_confirm_guards(client: AsyncClient, db: AsyncSession) -> None:
    demo = (await client.post("/api/incidents/simulate")).json()["id"]
    assert (await client.post(f"/api/incidents/{demo}/execution/confirm")).status_code == 409
    await checks(db, ServiceStatus.DOWN, ServiceStatus.DOWN)
    incident = await incident_manager.detect(db, service())
    assert incident is not None
    nothing = await client.post(f"/api/incidents/{incident.id}/execution/confirm")
    assert nothing.status_code == 409  # nothing approved yet
    assert (await client.post("/api/incidents/999999/execution/confirm")).status_code == 404
    assert await count(db, AgentRun, AgentRun.incident_id == incident.id) == 0
