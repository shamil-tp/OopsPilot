"""Real-application monitoring: configuration, service catalog, GitHub mapping, health checks,
demo-mode switch, and the guarantees that the simulation never touches the real service."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.evidence import EvidenceCollector
from app.core.config import get_settings
from app.github.security import sign
from app.models import CicdEvent, Deployment, Incident, ServiceHealth
from app.models.enums import DeploymentStatus, IncidentStatus, ServiceStatus, Severity
from app.schemas.services import ServiceHealthRead
from app.services import health_probe, simulator
from app.services.service_catalog import get_service, is_demo_service, service_names

pytestmark = pytest.mark.usefixtures("clean_demo")

SERVICE = "mallutyping-web"
REPO = "MrNihalT/mallutyping"
SECRET = "real-project-test-secret"
SHA = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c"


@pytest.fixture(autouse=True)
async def clean_real(db: AsyncSession) -> None:
    """Demo reset never touches the real service, so each test clears it itself."""
    for model in (Incident, ServiceHealth, Deployment, CicdEvent):
        await db.execute(delete(model).where(model.service_name == SERVICE))
    await db.commit()


@pytest.fixture
def real(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    for key, value in {
        "monitored_project_name": "Mallu Typing",
        "monitored_environment": "production",
        "monitored_service": SERVICE,
        "monitored_service_url": "https://mallutyping.example/",
        "github_repository": REPO,
        "github_service": None,
        "github_webhook_secret": SecretStr(SECRET),
    }.items():
        monkeypatch.setattr(settings, key, value)


@pytest.fixture
def demo_off(real: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "demo_mode", False)


async def deliver(client: AsyncClient, event: str, payload: dict[str, Any]) -> httpx.Response:
    body = json.dumps(payload).encode()
    return await client.post(
        "/api/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )


# --- configuration and catalog ------------------------------------------------------------------


def test_catalog(real: None) -> None:
    assert SERVICE in service_names() and "payment-api" in service_names()
    service = get_service(SERVICE)
    assert service is not None and service.kind == "real" and service.url
    assert not is_demo_service(SERVICE) and is_demo_service("payment-api")


async def test_project_and_services_endpoints(client: AsyncClient, real: None) -> None:
    projects = (await client.get("/api/services/projects")).json()
    assert projects == {
        "projects": [
            {
                "name": "Mallu Typing",
                "environment": "production",
                "service": SERVICE,
                "url": "https://mallutyping.example/",
                "repository": REPO,
            }
        ],
        "demo_mode": True,
        "health_check_interval_seconds": 60.0,
    }
    services = (await client.get("/api/services")).json()
    assert services[0]["name"] == SERVICE and services[0]["kind"] == "real"
    assert {s["kind"] for s in services[1:]} == {"demo"}


async def test_demo_mode_off_hides_and_refuses_the_simulation(
    client: AsyncClient, db: AsyncSession, demo_off: None
) -> None:
    assert [s["name"] for s in (await client.get("/api/services")).json()] == [SERVICE]
    assert (await client.get("/api/services/payment-api/health")).status_code == 404
    assert (await client.post("/api/incidents/simulate")).status_code == 409
    assert (await client.post("/api/demo/reset")).status_code == 409
    assert await db.scalar(select(func.count()).select_from(Incident)) == 0
    assert (await client.get("/api/services/projects")).json()["demo_mode"] is False


async def test_project_without_real_service_is_demo_only(client: AsyncClient) -> None:
    projects = (await client.get("/api/services/projects")).json()
    assert projects["projects"] == [] and projects["demo_mode"] is True


# --- GitHub → real service ----------------------------------------------------------------------


async def test_ci_workflow_maps_to_the_real_service_without_a_deployment(
    client: AsyncClient, db: AsyncSession, real: None
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = await deliver(
        client,
        "workflow_run",
        {
            "action": "completed",
            "workflow_run": {
                "id": 1,
                "name": "Malayalam Typing  CI",
                "path": ".github/workflows/main.yml",
                "head_branch": "main",
                "head_sha": SHA,
                "status": "completed",
                "conclusion": "failure",
                "updated_at": now,
            },
            "repository": {"full_name": REPO},
        },
    )
    event = response.json()["event"]
    assert (event["service_name"], event["category"], event["conclusion"]) == (
        SERVICE,
        "BUILD",
        "FAILURE",
    )
    assert event["deployment_id"] is None
    assert await db.scalar(select(func.count()).select_from(Incident)) == 0  # CI failure ≠ incident


async def test_vercel_production_deployment_becomes_real_deployment_history(
    client: AsyncClient, real: None
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "deployment_status": {"state": "success", "environment": "Production", "updated_at": now},
        "deployment": {
            "id": 4242,
            "sha": SHA,
            "ref": "main",
            "environment": "Production",
            "created_at": now,
        },
        "repository": {"full_name": REPO},
        "sender": {"login": "vercel[bot]"},
    }
    event = (await deliver(client, "deployment_status", payload)).json()["event"]

    assert (event["service_name"], event["environment"], event["version"]) == (
        SERVICE,
        "production",
        None,
    )
    deployments = (await client.get(f"/api/services/{SERVICE}/deployments")).json()
    assert [(d["version"], d["commit_sha"], d["status"]) for d in deployments] == [
        (SHA[:7], SHA, "SUCCEEDED")  # the commit, never an invented version
    ]


# --- health checks ------------------------------------------------------------------------------


def transport(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.mark.parametrize(
    ("handler", "slo_ms", "expected"),
    [
        (lambda request: httpx.Response(200), 3000, ServiceStatus.HEALTHY),
        (
            lambda request: (
                httpx.Response(301, headers={"Location": "/home"})
                if request.url.path == "/"
                else httpx.Response(200)
            ),
            3000,
            ServiceStatus.HEALTHY,
        ),
        (lambda request: httpx.Response(404), 3000, ServiceStatus.DEGRADED),
        (lambda request: httpx.Response(200), -1, ServiceStatus.DEGRADED),  # slower than the SLO
        (lambda request: httpx.Response(503), 3000, ServiceStatus.DOWN),
    ],
)
async def test_health_check_records_what_was_measured(
    db: AsyncSession, real: None, handler: Any, slo_ms: float, expected: ServiceStatus
) -> None:
    service = get_service(SERVICE)
    assert service is not None
    async with transport(handler) as http:
        row = await health_probe.check(db, service, client=http, slo_ms=slo_ms)

    assert row.status is expected and row.service_name == SERVICE
    assert row.latency_ms >= 0 and row.cpu_usage is None and row.memory_usage is None


async def test_unreachable_service_is_down(db: AsyncSession, real: None) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    service = get_service(SERVICE)
    assert service is not None
    async with transport(refuse) as http:
        row = await health_probe.check(db, service, client=http, slo_ms=3000)
    assert row.status is ServiceStatus.DOWN


async def test_error_rate_is_the_share_of_recent_failed_checks_and_old_rows_expire(
    client: AsyncClient, db: AsyncSession, real: None
) -> None:
    service = get_service(SERVICE)
    assert service is not None
    now = datetime.now(UTC)
    db.add(
        ServiceHealth(
            service_name=SERVICE,
            timestamp=now - timedelta(days=8),
            status=ServiceStatus.HEALTHY,
            error_rate=0,
            latency_ms=100,
        )
    )
    await db.commit()
    codes = iter([200, 200, 503, 200])
    async with transport(lambda request: httpx.Response(next(codes))) as http:
        for i in range(4):
            row = await health_probe.check(
                db, service, client=http, slo_ms=3000, now=now + timedelta(seconds=i)
            )

    assert row.error_rate == 25.0  # 1 of the last 4 checks failed
    count = await db.scalar(
        select(func.count()).select_from(ServiceHealth).where(ServiceHealth.service_name == SERVICE)
    )
    assert count == 4  # the 8-day-old row was pruned
    latest = (await client.get(f"/api/services/{SERVICE}/health")).json()
    assert latest["status"] == "HEALTHY" and latest["cpu_usage"] is None


def test_health_evidence_says_what_was_not_measured() -> None:
    now = datetime.now(UTC)
    snapshot = ServiceHealthRead(
        service_name=SERVICE,
        timestamp=now,
        status=ServiceStatus.DOWN,
        error_rate=100,
        latency_ms=10000,
        cpu_usage=None,
        memory_usage=None,
    )
    [item] = EvidenceCollector._health_items([(snapshot, "current")], now)
    assert "cpu/memory not measured" in item.fact and "cpu 0" not in item.fact


# --- the simulation never touches the real service ----------------------------------------------


async def test_reset_keeps_real_incidents_and_telemetry(
    client: AsyncClient, db: AsyncSession, real: None
) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            Incident(
                title="Mallu Typing unavailable",
                description="Health checks failing",
                severity=Severity.HIGH,
                status=IncidentStatus.DETECTED,
                service_name=SERVICE,
                created_at=now,
                updated_at=now,
            ),
            ServiceHealth(
                service_name=SERVICE,
                timestamp=now,
                status=ServiceStatus.DOWN,
                error_rate=100,
                latency_ms=9000,
            ),
            Deployment(
                service_name=SERVICE,
                version=SHA[:7],
                timestamp=now,
                status=DeploymentStatus.SUCCEEDED,
                commit_sha=SHA,
            ),
        ]
    )
    await db.commit()
    await client.post("/api/incidents/simulate")

    reset = (await client.post("/api/demo/reset")).json()

    assert reset["incidents_deleted"] == 1  # only the simulated incident
    remaining = (await client.get("/api/incidents")).json()
    assert [i["service_name"] for i in remaining] == [SERVICE]
    assert (await client.get(f"/api/services/{SERVICE}/health")).json()["status"] == "DOWN"
    assert len((await client.get(f"/api/services/{SERVICE}/deployments")).json()) == 1


async def test_simulated_execution_refuses_a_real_service(db: AsyncSession, real: None) -> None:
    at = datetime.now(UTC)
    with pytest.raises(LookupError, match="not a simulated service"):
        await simulator.apply_rollback(db, service=SERVICE, from_version="a", to_version="b", at=at)
    with pytest.raises(LookupError, match="not a simulated service"):
        await simulator.apply_restart(db, service=SERVICE, at=at)


# --- several projects ---------------------------------------------------------------------------

EDTECH = "edtech-web"
EDTECH_REPO = "MrNihalT/EdTech"


@pytest.fixture
def two_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import MonitoredProject

    settings = get_settings()
    monkeypatch.setattr(settings, "github_webhook_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "github_repository", None)
    monkeypatch.setattr(
        settings,
        "monitored_projects",
        [
            MonitoredProject(
                name="Mallu Typing",
                service=SERVICE,
                url="https://mallutyping.example/",
                repository=REPO,
            ),
            MonitoredProject(
                name="EdTech", service=EDTECH, url="https://edtech.example/", repository=EDTECH_REPO
            ),
        ],
    )


@pytest.fixture(autouse=True)
async def clean_edtech(db: AsyncSession) -> None:
    for model in (Incident, ServiceHealth, Deployment, CicdEvent):
        await db.execute(delete(model).where(model.service_name == EDTECH))
    await db.execute(delete(CicdEvent).where(CicdEvent.repository == "someone/else"))
    await db.commit()


async def test_projects_are_listed_and_validated(client: AsyncClient, two_projects: None) -> None:
    body = (await client.get("/api/services/projects")).json()
    assert [(p["name"], p["service"], p["repository"]) for p in body["projects"]] == [
        ("Mallu Typing", SERVICE, REPO),
        ("EdTech", EDTECH, EDTECH_REPO),
    ]
    services = [s["name"] for s in (await client.get("/api/services")).json()]
    assert services[:2] == [SERVICE, EDTECH]
    assert (await client.get(f"/api/services/{EDTECH}/health")).status_code == 404  # no check yet


async def test_each_repository_maps_to_its_own_project(
    client: AsyncClient, two_projects: None
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def deployment(repo: str, deployment_id: int) -> dict[str, Any]:
        return {
            "deployment_status": {
                "state": "success",
                "environment": "Production",
                "updated_at": now,
            },
            "deployment": {
                "id": deployment_id,
                "sha": SHA,
                "ref": "main",
                "environment": "Production",
            },
            "repository": {"full_name": repo},
        }

    mallu = (await deliver(client, "deployment_status", deployment(REPO, 1))).json()["event"]
    edtech = (
        await deliver(client, "deployment_status", deployment(EDTECH_REPO.lower(), 2))
    ).json()["event"]
    other = (await deliver(client, "deployment_status", deployment("someone/else", 3))).json()[
        "event"
    ]

    assert (mallu["service_name"], edtech["service_name"], other["service_name"]) == (
        SERVICE,
        EDTECH,
        None,
    )
    assert other["deployment_id"] is None  # stored as GitHub activity, attached to no project
    for service in (SERVICE, EDTECH):
        deployments = (await client.get(f"/api/services/{service}/deployments")).json()
        assert [d["commit_sha"] for d in deployments] == [SHA]


async def test_every_project_is_health_checked_and_one_failure_does_not_block_others(
    db: AsyncSession, two_projects: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mallutyping.example":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200)

    async def stop_after_one_round(_: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(health_probe, "new_client", lambda: transport(handler))
    monkeypatch.setattr(health_probe.asyncio, "sleep", stop_after_one_round)
    with pytest.raises(asyncio.CancelledError):
        await health_probe.run_forever()

    rows = {
        row.service_name: row.status
        for row in await db.scalars(
            select(ServiceHealth).where(ServiceHealth.service_name.in_([SERVICE, EDTECH]))
        )
    }
    assert rows == {SERVICE: ServiceStatus.DOWN, EDTECH: ServiceStatus.HEALTHY}
