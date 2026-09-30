import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import incidents as incidents_routes
from app.models import AgentEvent, Deployment, Incident, LogEntry
from app.models.enums import LogLevel

pytestmark = pytest.mark.usefixtures("clean_demo")


async def test_reset_removes_simulation_and_restores_healthy_state(
    client: AsyncClient, db: AsyncSession
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]

    response = await client.post("/api/demo/reset")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reset"
    assert body["incidents_deleted"] == 1
    assert body["logs_deleted"] > 0
    assert (await client.get("/api/incidents")).json() == []
    assert (await client.get(f"/api/incidents/{incident_id}")).status_code == 404
    # Child rows go with the incident (ON DELETE CASCADE).
    assert await db.scalar(select(func.count()).select_from(AgentEvent)) == 0
    assert await db.scalar(select(func.count()).where(Deployment.version == "v1.8.2")) == 0
    errors = select(func.count()).where(LogEntry.level == LogLevel.ERROR)
    assert await db.scalar(errors) == 0
    health = (await client.get("/api/services/payment-api/health")).json()
    assert health["status"] == "HEALTHY"
    assert health["error_rate"] == pytest.approx(0.2)


async def test_reset_then_simulate_again(client: AsyncClient) -> None:
    await client.post("/api/incidents/simulate")
    await client.post("/api/demo/reset")

    again = await client.post("/api/incidents/simulate")

    assert again.status_code == 201
    assert again.json()["reference"] == "INC-001"  # numbering restarts after a reset
    health = (await client.get("/api/services/payment-api/health")).json()
    assert health["status"] == "DEGRADED"


async def test_reset_is_idempotent(client: AsyncClient) -> None:
    first = await client.post("/api/demo/reset")
    second = await client.post("/api/demo/reset")

    assert first.status_code == second.status_code == 200
    assert second.json()["incidents_deleted"] == 0
    # The baseline replaces itself rather than accumulating.
    assert second.json()["logs_deleted"] == first.json()["logs_deleted"]


async def test_reset_leaves_non_simulated_telemetry_alone(
    client: AsyncClient, db: AsyncSession
) -> None:
    other = LogEntry(
        service_name="external-service",
        timestamp=(await db.scalar(select(func.max(LogEntry.timestamp)))),
        level=LogLevel.INFO,
        message="not part of the simulation",
    )
    db.add(other)
    await db.commit()

    await client.post("/api/demo/reset")

    remaining = select(func.count()).where(LogEntry.service_name == "external-service")
    assert await db.scalar(remaining) == 1
    await db.delete(other)
    await db.commit()


async def test_database_failure_returns_safe_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(*_args: object, **_kwargs: object) -> list[Incident]:
        raise ConnectionRefusedError("connect to postgres://user:secret@host failed")

    monkeypatch.setattr(incidents_routes.incidents, "list_incidents", unavailable)

    response = await client.get("/api/incidents")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable. Please try again shortly."}
    assert "secret" not in response.text
