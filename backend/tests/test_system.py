import pytest
from httpx import AsyncClient

from app.api.routes import system


async def test_system_health_ok(client: AsyncClient) -> None:
    response = await client.get("/api/system/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "OpsPilot"
    assert body["database"]["status"] == "ok"
    assert body["ai"]["provider"] == "gemini"


async def test_system_health_reports_database_outage(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable(_session: object) -> float:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(system, "check_database", unreachable)

    response = await client.get("/api/system/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == {
        "status": "unavailable",
        "latency_ms": None,
        "error": "ConnectionRefusedError",
    }


async def test_openapi_schema_is_served(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/system/health" in response.json()["paths"]
