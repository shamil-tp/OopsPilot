"""Phase 10: final incident report and the live event stream (WebSocket)."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.agents import investigation, remediation, report, root_cause, verification
from app.core.config import get_settings
from app.main import app
from app.models import AgentRun, IncidentReport
from app.models.enums import AgentName
from app.services.agent_events import EventRecorder
from app.websocket import stream
from tests.investigation_fakes import GOOD_REMEDIATION, FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")

LIFECYCLE = ("investigate", "analyze", "remediate")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    for module in (investigation, root_cause, remediation, verification):
        monkeypatch.setattr(module, "get_ai_provider", lambda: provider)
    return provider


async def run_to(client: AsyncClient, *, verify: bool = True) -> int:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    for step in LIFECYCLE:
        assert (await client.post(f"/api/incidents/{incident_id}/{step}")).status_code == 201
    assert (await client.post(f"/api/incidents/{incident_id}/approve")).status_code == 200
    if verify:
        assert (await client.post(f"/api/incidents/{incident_id}/verify")).status_code == 201
    return incident_id


async def report_rows(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(IncidentReport)) or 0


# --- report -------------------------------------------------------------------------------------


async def test_verification_creates_the_report_from_stored_results(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await run_to(client)
    ai_calls = len(fake.calls)

    response = await client.get(f"/api/incidents/{incident_id}/report")

    assert response.status_code == 200
    body = response.json()
    r = body["report"]
    summary = r["summary"]
    assert (summary["reference"], summary["service"], summary["severity"]) == (
        "INC-001",
        "payment-api",
        "HIGH",
    )
    assert summary["final_status"] == "RESOLVED" and summary["resolved_at"] is not None
    # Every section is the stored phase result, not a regeneration.
    stored = (await client.get(f"/api/incidents/{incident_id}/analysis")).json()["result"]
    assert r["root_cause"] == stored
    assert r["root_cause"]["root_cause"] == stored["root_cause"]
    remediation_view = (await client.get(f"/api/incidents/{incident_id}/remediation")).json()
    assert r["remediation"] == remediation_view["result"]
    assert r["approval"] == remediation_view["approval"]
    assert r["approval"]["status"] == "APPROVED"
    execution = (await client.get(f"/api/incidents/{incident_id}/execution")).json()["result"]
    assert r["execution"] == execution
    assert r["execution"]["parameters"] == {
        "service": "payment-api",
        "from_version": "v1.8.2",
        "to_version": "v1.8.1",
    }
    verification_view = (await client.get(f"/api/incidents/{incident_id}/verification")).json()
    assert r["verification"] == verification_view["result"]
    assert r["verification"]["before"]["error_rate"] == 37.0
    assert r["verification"]["after"]["error_rate"] == 0.8
    investigation_view = (await client.get(f"/api/incidents/{incident_id}/investigation")).json()
    assert r["investigation"] == investigation_view["result"]
    assert r["outcome"] == {
        "recovered": True,
        "final_status": "RESOLVED",
        "human_decision": "APPROVED",
        "remediation_performed": "rolled back payment-api from v1.8.2 to v1.8.1",
        "verification": "payment-api recovered: 6/6 checks passed",
        "recovery_status": "RECOVERED",
    }
    # Timeline: milestones only, oldest first, from the stored events.
    types = [e["event_type"] for e in r["timeline"]]
    assert types[0] == "incident_created" and types[-1] == "incident_resolved"
    for milestone in (
        "root_cause_identified",
        "approval_received",
        "remediation_completed",
        "verification_completed",
    ):
        assert milestone in types
    assert "tool_started" not in types and "recovery_check" not in types
    assert len(fake.calls) == ai_calls  # no AI call for the report


async def test_report_row_and_event(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await run_to(client)

    row = await db.scalar(select(IncidentReport).where(IncidentReport.incident_id == incident_id))
    assert row is not None
    assert row.recovery_status.value == "RECOVERED"
    assert row.action_taken == "rolled back payment-api from v1.8.2 to v1.8.1"
    assert row.root_cause and row.confidence is not None and row.evidence
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    generated = [e for e in events if e["event_type"] == "report_generated"]
    assert len(generated) == 1 and generated[0]["agent"] == "orchestrator"
    assert events[-1]["event_type"] == "report_generated"
    # The report never changes the incident's state.
    assert (await client.get(f"/api/incidents/{incident_id}")).json()["status"] == "RESOLVED"


async def test_report_is_created_once(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await run_to(client)

    first = (await client.get(f"/api/incidents/{incident_id}/report")).json()
    second = (await client.get(f"/api/incidents/{incident_id}/report")).json()
    again_verify = await client.post(f"/api/incidents/{incident_id}/verify")

    assert first == second
    assert again_verify.status_code == 200
    assert await report_rows(db) == 1
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    assert [e["event_type"] for e in events].count("report_generated") == 1


async def test_concurrent_report_creation_creates_one(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await run_to(client, verify=False)
    # Verify without the route's report step, so both requests below race to create it.
    run, _ = await verification.start_verification(db, incident_id)
    await verification.run_verification(run.id)
    original = report.build_report

    async def slow_build(*args: Any, **kwargs: Any) -> Any:
        content = await original(*args, **kwargs)
        await asyncio.sleep(0.2)  # both requests pass the "no report yet" check
        return content

    monkeypatch.setattr(report, "build_report", slow_build)

    responses = await asyncio.gather(
        client.get(f"/api/incidents/{incident_id}/report"),
        client.get(f"/api/incidents/{incident_id}/report"),
    )

    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert await report_rows(db) == 1


async def test_report_for_a_failed_recovery(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    fake.results["RemediationProposal"] = {
        **GOOD_REMEDIATION,
        "action": "RESTART_SERVICE",
        "rollback_to_version": None,
    }
    incident_id = await run_to(client)

    r = (await client.get(f"/api/incidents/{incident_id}/report")).json()["report"]

    assert r["summary"]["final_status"] == "FAILED" and r["summary"]["resolved_at"] is None
    assert r["outcome"]["recovered"] is False
    assert r["outcome"]["recovery_status"] == "NOT_RECOVERED"
    assert r["verification"]["failed_checks"]


async def test_report_not_ready_and_missing(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = await run_to(client, verify=False)

    not_verified = await client.get(f"/api/incidents/{incident_id}/report")
    missing = await client.get("/api/incidents/999999/report")

    assert not_verified.status_code == 409
    assert "has not been verified yet" in not_verified.json()["detail"]
    assert missing.status_code == 404


async def test_rejected_incident_has_no_report(client: AsyncClient, fake: FakeProvider) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    for step in LIFECYCLE:
        await client.post(f"/api/incidents/{incident_id}/{step}")
    await client.post(f"/api/incidents/{incident_id}/reject")

    response = await client.get(f"/api/incidents/{incident_id}/report")

    assert response.status_code == 409


async def test_investigation_can_be_read_without_rerunning(
    client: AsyncClient, fake: FakeProvider
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    missing = await client.get(f"/api/incidents/{incident_id}/investigation")
    created = (await client.post(f"/api/incidents/{incident_id}/investigate")).json()
    calls = len(fake.calls)

    fetched = await client.get(f"/api/incidents/{incident_id}/investigation")

    assert missing.status_code == 404
    assert fetched.status_code == 200 and fetched.json() == created
    assert len(fake.calls) == calls
    assert (await client.get("/api/incidents/999999/investigation")).status_code == 404


# --- event stream (generator) -------------------------------------------------------------------


async def collect(stream_iter: Any, count: int) -> list[dict[str, Any]]:
    messages = []
    async for message in stream_iter:
        messages.append(message)
        if len(messages) == count:
            break
    return messages


async def test_stream_sends_history_then_status_then_new_events(
    client: AsyncClient, db: AsyncSession
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    messages = stream.incident_messages(incident_id, poll_interval=0.01)

    first = await collect(messages, 2)
    EventRecorder(db, incident_id, AgentName.INVESTIGATION).emit("agent_started", "later event")
    await db.commit()
    later = await collect(messages, 1)
    await messages.aclose()

    assert first[0]["type"] == "incident_created"
    assert first[0]["event_type"] == "incident_created" and first[0]["incident_id"] == incident_id
    assert first[1] == {
        "type": "incident_status",
        "incident_id": incident_id,
        "incident_status": "DETECTED",
    }
    assert later[0]["type"] == "agent_started" and later[0]["message"] == "later event"
    assert later[0]["id"] > first[0]["id"]


async def test_stream_resumes_after_an_event_id(client: AsyncClient) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()

    messages = stream.incident_messages(incident_id, after=events[-1]["id"], poll_interval=0.01)
    first = await collect(messages, 1)
    await messages.aclose()

    assert first[0]["type"] == "incident_status"  # no duplicate history


async def test_stream_unknown_incident() -> None:
    messages = stream.incident_messages(999999, poll_interval=0.01)

    with pytest.raises(LookupError):
        await collect(messages, 1)


async def test_stream_survives_a_database_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    real = stream._read
    failures = iter([True])

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        if next(failures, False):
            raise OperationalError("SELECT", {}, Exception("postgres://u:hunter2@db"))
        return await real(*args, **kwargs)

    monkeypatch.setattr(stream, "_read", flaky)
    messages = stream.incident_messages(incident_id, poll_interval=0.001)

    got = await collect(messages, 2)
    await messages.aclose()

    assert got[0] == {"type": "error", "message": "Event stream temporarily unavailable; retrying"}
    assert "hunter2" not in str(got)
    assert got[1]["type"] == "incident_created"


# --- WebSocket endpoint -------------------------------------------------------------------------


@pytest.fixture
def ws_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The WebSocket test client runs the app on its own event loop, so the stream gets its own
    pool-free engine there (pooled connections cannot cross event loops)."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    monkeypatch.setattr(stream, "SessionLocal", async_sessionmaker(engine))
    monkeypatch.setattr(stream, "POLL_INTERVAL_SECONDS", 0.01)
    # Not used as a context manager: that would run the app lifespan, whose shutdown disposes
    # the shared engine from this client's event loop.
    yield TestClient(app)


@pytest.fixture
async def resolved_incident(client: AsyncClient, fake: FakeProvider) -> int:
    return await run_to(client)


def test_websocket_streams_the_incident_timeline(
    ws_client: TestClient, resolved_incident: int
) -> None:
    with ws_client.websocket_connect(f"/ws/incidents/{resolved_incident}") as ws:
        messages = []
        while not any(m["type"] == "incident_status" for m in messages):
            messages.append(ws.receive_json())

    types = [m["type"] for m in messages]
    assert types[0] == "incident_created"
    assert types[-1] == "incident_status" and messages[-1]["incident_status"] == "RESOLVED"
    assert types.index("approval_received") < types.index("verification_completed")
    assert "report_generated" in types
    event = next(m for m in messages if m["type"] == "remediation_completed")
    assert set(event) == {
        "type",
        "id",
        "incident_id",
        "agent",
        "event_type",
        "message",
        "metadata",
        "timestamp",
    }
    assert event["agent"] == "orchestrator"
    ids = [m["id"] for m in messages if "id" in m]
    assert ids == sorted(ids)
    assert "AIza" not in str(messages) and "password" not in str(messages).lower()


def test_websocket_unknown_incident_is_closed(ws_client: TestClient) -> None:
    with ws_client.websocket_connect("/ws/incidents/999999") as ws:
        assert ws.receive_json() == {"type": "error", "message": "Incident not found"}


def test_websocket_disconnect_does_not_affect_the_workflow(
    ws_client: TestClient, resolved_incident: int
) -> None:
    for _ in range(3):  # connect and drop repeatedly, mid-stream
        with ws_client.websocket_connect(f"/ws/incidents/{resolved_incident}") as ws:
            ws.receive_json()

    with ws_client.websocket_connect(f"/ws/incidents/{resolved_incident}") as ws:
        messages = [ws.receive_json()]
        while messages[-1]["type"] != "incident_status":
            messages.append(ws.receive_json())
    assert messages[-1]["incident_status"] == "RESOLVED"
    assert [m["type"] for m in messages].count("report_generated") == 1


async def test_no_agent_run_is_created_by_reading(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider
) -> None:
    incident_id = await run_to(client)
    before = await db.scalar(select(func.count()).select_from(AgentRun))

    for path in (
        "report",
        "investigation",
        "analysis",
        "remediation",
        "execution",
        "verification",
        "events",
    ):
        assert (await client.get(f"/api/incidents/{incident_id}/{path}")).status_code == 200

    assert await db.scalar(select(func.count()).select_from(AgentRun)) == before
