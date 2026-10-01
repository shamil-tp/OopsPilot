from datetime import datetime

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.usefixtures("clean_demo")


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def test_list_services(client: AsyncClient) -> None:
    response = await client.get("/api/services")

    assert response.status_code == 200
    services = {s["name"]: s for s in response.json()}
    assert list(services) == ["payment-api", "auth-api", "database"]
    assert services["payment-api"]["dependencies"] == ["database", "auth-api"]
    assert {s["status"] for s in services.values()} == {"HEALTHY"}


async def test_baseline_health_is_healthy(client: AsyncClient) -> None:
    body = (await client.get("/api/services/payment-api/health")).json()

    assert body["status"] == "HEALTHY"
    assert body["error_rate"] == pytest.approx(0.2)
    assert body["latency_ms"] == pytest.approx(180)


async def test_incident_health_is_degraded(client: AsyncClient) -> None:
    await client.post("/api/incidents/simulate")

    response = await client.get("/api/services/payment-api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["service_name"] == "payment-api"
    assert body["status"] == "DEGRADED"
    assert body["error_rate"] == pytest.approx(37)
    assert body["latency_ms"] == pytest.approx(2800)
    assert body["cpu_usage"] == pytest.approx(43)
    assert body["memory_usage"] == pytest.approx(68)
    services = {s["name"]: s["status"] for s in (await client.get("/api/services")).json()}
    assert services == {"payment-api": "DEGRADED", "auth-api": "HEALTHY", "database": "HEALTHY"}


async def test_logs_are_chronological_and_show_the_causal_sequence(client: AsyncClient) -> None:
    await client.post("/api/incidents/simulate")

    logs = (await client.get("/api/services/payment-api/logs")).json()

    timestamps = [_ts(log["timestamp"]) for log in logs]
    assert timestamps == sorted(timestamps)
    messages = [log["message"] for log in logs]
    deploy = messages.index("Deployment v1.8.2 started")
    db_error = messages.index("Database connection failed")
    http_500 = messages.index("POST /payment 500")
    assert deploy < db_error < http_500
    # Normal traffic is present too, so the agent must pick out the relevant evidence.
    assert "Payment processed successfully" in messages[:deploy]
    assert logs[db_error]["level"] == "ERROR"
    assert logs[db_error]["metadata"]["host"] == "payments-db.internal"


async def test_logs_level_filter_and_limit(client: AsyncClient) -> None:
    await client.post("/api/incidents/simulate")

    errors = (
        await client.get("/api/services/payment-api/logs", params={"level": ["ERROR", "WARN"]})
    ).json()
    latest_two = (await client.get("/api/services/payment-api/logs", params={"limit": 2})).json()
    all_logs = (await client.get("/api/services/payment-api/logs")).json()

    assert errors
    assert {log["level"] for log in errors} <= {"ERROR", "WARN"}
    assert latest_two == all_logs[-2:]


async def test_logs_time_window(client: AsyncClient) -> None:
    await client.post("/api/incidents/simulate")
    all_logs = (await client.get("/api/services/payment-api/logs")).json()
    deploy = next(log for log in all_logs if log["message"] == "Deployment v1.8.2 started")

    window = (
        await client.get("/api/services/payment-api/logs", params={"since": deploy["timestamp"]})
    ).json()

    assert window[0]["message"] == "Deployment v1.8.2 started"
    assert all(_ts(log["timestamp"]) >= _ts(deploy["timestamp"]) for log in window)


async def test_deployments_newest_first_with_v182_before_errors(client: AsyncClient) -> None:
    await client.post("/api/incidents/simulate")

    deployments = (await client.get("/api/services/payment-api/deployments")).json()
    logs = (await client.get("/api/services/payment-api/logs", params={"level": "ERROR"})).json()

    assert [d["version"] for d in deployments] == ["v1.8.2", "v1.8.1", "v1.8.0"]
    assert deployments[0]["status"] == "SUCCEEDED"
    assert deployments[0]["commit_sha"]
    # v1.8.2 is the last deployment before the first error.
    first_error = _ts(logs[0]["timestamp"])
    assert _ts(deployments[0]["timestamp"]) < first_error
    assert all(_ts(d["timestamp"]) < first_error for d in deployments)


async def test_database_service_has_no_deployments(client: AsyncClient) -> None:
    response = await client.get("/api/services/database/deployments")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("path", ["health", "logs", "deployments"])
async def test_unknown_service_returns_404(client: AsyncClient, path: str) -> None:
    response = await client.get(f"/api/services/billing-api/{path}")

    assert response.status_code == 404
    assert "Unknown service 'billing-api'" in response.json()["detail"]


async def test_invalid_query_parameters_return_422(client: AsyncClient) -> None:
    bad_limit = await client.get("/api/services/payment-api/logs", params={"limit": 0})
    bad_level = await client.get("/api/services/payment-api/logs", params={"level": "FATAL"})

    assert bad_limit.status_code == 422
    assert bad_level.status_code == 422


async def test_create_and_delete_monitored_project(client: AsyncClient) -> None:
    payload = {
        "name": "Acme Storefront",
        "service": "acme-store",
        "url": "https://store.acme.com/health",
        "repository": "acme-corp/storefront",
        "environment": "production",
    }
    created = await client.post("/api/services/projects", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["project"]["name"] == "Acme Storefront"
    assert body["project"]["service"] == "acme-store"
    assert body["project"]["repository"] == "acme-corp/storefront"
    assert body["webhook"]["github_setup_url"] == "https://github.com/acme-corp/storefront/settings/hooks/new"
    assert "/api/webhooks/github" in body["webhook"]["payload_url"]

    # Verify project shows up in list
    projects = (await client.get("/api/services/projects")).json()
    assert any(p["service"] == "acme-store" for p in projects["projects"])

    # Duplicate creation returns 409
    dup = await client.post("/api/services/projects", json=payload)
    assert dup.status_code == 409

    # Delete project
    deleted = await client.delete("/api/services/projects/acme-store")
    assert deleted.status_code == 204

    # Verify no longer in list
    projects_after = (await client.get("/api/services/projects")).json()
    assert not any(p["service"] == "acme-store" for p in projects_after["projects"])

