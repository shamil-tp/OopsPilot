from collections.abc import Awaitable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentEvent, Deployment, Incident, LogEntry, ServiceHealth
from app.models.enums import IncidentStatus

pytestmark = pytest.mark.usefixtures("clean_demo")


async def test_simulate_creates_incident_and_persists_evidence(
    client: AsyncClient, db: AsyncSession
) -> None:
    response = await client.post("/api/incidents/simulate")

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Payment API Production Incident"
    assert body["service_name"] == "payment-api"
    assert body["severity"] == "HIGH"
    assert body["status"] == "DETECTED"
    assert body["reference"] == f"INC-{body['id']:03d}"

    incident = await db.get(Incident, body["id"])
    assert incident is not None

    def count(model: type, *where: Any) -> Awaitable[Any]:
        return db.scalar(select(func.count()).select_from(model).where(*where))

    assert await count(LogEntry, LogEntry.message == "Database connection failed") > 0
    assert await count(LogEntry, LogEntry.message == "POST /payment 500") > 0
    assert await count(Deployment, Deployment.version == "v1.8.2") == 1
    assert await count(ServiceHealth, ServiceHealth.service_name == "payment-api") > 0
    event = await db.scalar(select(AgentEvent).where(AgentEvent.incident_id == body["id"]))
    assert event is not None
    assert event.event_type == "incident_created"


async def test_simulate_reuses_active_incident(client: AsyncClient, db: AsyncSession) -> None:
    first = await client.post("/api/incidents/simulate")
    logs_after_first = await db.scalar(select(func.count()).select_from(LogEntry))

    second = await client.post("/api/incidents/simulate")

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len((await client.get("/api/incidents")).json()) == 1
    assert await db.scalar(select(func.count()).select_from(LogEntry)) == logs_after_first
    assert await db.scalar(select(func.count()).where(Deployment.version == "v1.8.2")) == 1


async def test_simulate_after_resolution_starts_a_fresh_incident(
    client: AsyncClient, db: AsyncSession
) -> None:
    first = (await client.post("/api/incidents/simulate")).json()
    incident = await db.get(Incident, first["id"])
    assert incident is not None
    incident.status = IncidentStatus.RESOLVED
    await db.commit()

    second = await client.post("/api/incidents/simulate")

    assert second.status_code == 201
    assert second.json()["id"] != first["id"]
    # Telemetry is rebuilt, not duplicated: still exactly one v1.8.2 deployment.
    assert await db.scalar(select(func.count()).where(Deployment.version == "v1.8.2")) == 1
    listed = (await client.get("/api/incidents")).json()
    assert [i["id"] for i in listed] == [second.json()["id"], first["id"]]  # newest first


async def test_get_incident(client: AsyncClient) -> None:
    created = (await client.post("/api/incidents/simulate")).json()

    response = await client.get(f"/api/incidents/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


async def test_unknown_incident_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/incidents/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Incident 999999 not found"}


async def test_invalid_incident_id_returns_422(client: AsyncClient) -> None:
    response = await client.get("/api/incidents/not-a-number")

    assert response.status_code == 422


async def test_incident_list_is_empty_after_reset(client: AsyncClient) -> None:
    response = await client.get("/api/incidents")

    assert response.status_code == 200
    assert response.json() == []
