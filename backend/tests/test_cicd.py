"""Phase 11: CI/CD normalization, the read-only CI/CD tool, investigation/RCA integration, the
CI/CD read API, and static safety checks (no shell, no AI, no GitHub API in webhook processing).
"""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import investigation, remediation, root_cause, verification
from app.agents.evidence import CICD_WINDOW
from app.github import normalize as n
from app.github.demo import RELEASE_SHA, demo_deliveries
from app.models import Incident
from app.models.enums import AgentName, CicdCategory, CicdConclusion, CicdStatus
from app.schemas.root_cause import RootCauseAnalysis
from app.services import scenario
from app.tools import ToolExecutor
from app.tools.registry import ToolArgumentError, ToolPermissionError
from tests.investigation_fakes import GOOD_ANALYSIS, GOOD_RCA, FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo")

APP = Path(__file__).resolve().parents[1] / "app"
NOW = datetime(2026, 9, 30, 11, 44, tzinfo=UTC)


def no_service(_: str) -> str | None:
    return None


def normalize(event: str, payload: dict[str, Any]) -> n.NormalizedEvent:
    result = n.normalize(event, "d-1", payload, received_at=NOW, repository_service=no_service)
    assert result is not None
    return result


def run(**fields: Any) -> dict[str, Any]:
    return {"workflow_run": {"id": 1} | fields, "repository": {"full_name": "acme/app"}}


# --- normalization (pure) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("v1.8.2", "v1.8.2"), ("1.2", "1.2"), ("v2.0.0-rc.1", "v2.0.0-rc.1"), ("main", None)],
)
def test_tag_version(value: str, expected: str | None) -> None:
    assert n.tag_version(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(RELEASE_SHA.upper(), RELEASE_SHA), ("0" * 40, None), ("xyz1234", None), ("abc12", None)],
)
def test_commit_sha(value: str, expected: str | None) -> None:
    assert n.commit_sha(value) == expected


def test_clip_removes_control_characters() -> None:
    assert n.clip("a\x00b\r\nc\x1b[31m", 100) == "a b c [31m"
    assert n.clip("x" * 500, 10) == "x" * 10
    assert n.clip("   ", 10) is None


def test_github_url_allowlist() -> None:
    assert n.github_url("https://github.com/a/b/actions/runs/1") is not None
    assert n.github_url("javascript:alert(1)") is None
    assert n.github_url("https://evil.example/github.com/") is None


@pytest.mark.parametrize(
    ("name", "category", "environment"),
    [
        ("deploy-production", CicdCategory.DEPLOYMENT, "production"),
        ("Release to prod", CicdCategory.DEPLOYMENT, "production"),
        ("deploy-staging", CicdCategory.DEPLOYMENT, "staging"),
        ("Deploy", CicdCategory.DEPLOYMENT, None),
        ("Backend CI", CicdCategory.BUILD, None),
        ("Run tests", CicdCategory.TEST, None),
        ("contest-build", CicdCategory.BUILD, None),  # "test" inside a word does not count
        ("productivity-report", CicdCategory.BUILD, None),
    ],
)
def test_workflow_classification(
    name: str, category: CicdCategory, environment: str | None
) -> None:
    event = normalize("workflow_run", run(name=name))
    assert (event.category, event.environment) == (category, environment)


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("completed", "success", (CicdStatus.COMPLETED, CicdConclusion.SUCCESS)),
        ("completed", "startup_failure", (CicdStatus.COMPLETED, CicdConclusion.FAILURE)),
        ("completed", "timed_out", (CicdStatus.COMPLETED, CicdConclusion.TIMED_OUT)),
        ("completed", "action_required", (CicdStatus.COMPLETED, CicdConclusion.OTHER)),
        ("in_progress", None, (CicdStatus.IN_PROGRESS, None)),
        ("waiting", None, (CicdStatus.QUEUED, None)),
        (None, None, (CicdStatus.QUEUED, None)),
    ],
)
def test_workflow_status_and_conclusion(
    status: str | None, conclusion: str | None, expected: tuple[Any, Any]
) -> None:
    event = normalize("workflow_run", run(status=status, conclusion=conclusion))
    assert (event.status, event.conclusion) == expected


def test_workflow_timing_and_tag_version() -> None:
    event = normalize(
        "workflow_run",
        run(
            name="release",
            head_branch="v3.1.0",
            status="completed",
            conclusion="success",
            run_started_at="2026-09-30T11:41:00Z",
            updated_at="2026-09-30T11:41:45Z",
        ),
    )
    assert event.started_at == datetime(2026, 9, 30, 11, 41, tzinfo=UTC)
    assert event.completed_at == event.occurred_at == datetime(2026, 9, 30, 11, 41, 45, tzinfo=UTC)
    assert (event.version, event.version_source) == ("v3.1.0", "tag")


@pytest.mark.parametrize(
    ("state", "status", "conclusion"),
    [
        ("success", CicdStatus.COMPLETED, CicdConclusion.SUCCESS),
        ("error", CicdStatus.COMPLETED, CicdConclusion.FAILURE),
        ("in_progress", CicdStatus.IN_PROGRESS, None),
        ("weird", CicdStatus.QUEUED, None),
    ],
)
def test_deployment_status_states(state: str, status: CicdStatus, conclusion: Any) -> None:
    event = normalize(
        "deployment_status",
        {
            "deployment_status": {"state": state},
            "deployment": {"id": 1, "ref": "v1.0.0", "payload": "not-an-object"},
            "repository": {"full_name": "acme/app"},
        },
    )
    assert (event.status, event.conclusion) == (status, conclusion)
    assert (event.version, event.version_source, event.branch) == ("v1.0.0", "tag", None)


def test_explicit_version_must_be_safe() -> None:
    event = normalize(
        "deployment_status",
        {
            "deployment_status": {"state": "success"},
            "deployment": {"id": 1, "ref": "main", "payload": {"version": "$(reboot)"}},
            "repository": {"full_name": "acme/app"},
        },
    )
    assert event.version is None and event.branch == "main"


def test_unsupported_and_malformed() -> None:
    assert n.normalize("issues", "d", {}, received_at=NOW, repository_service=no_service) is None
    with pytest.raises(n.PayloadError, match="workflow_run"):
        n.normalize("workflow_run", "d", {"x": 1}, received_at=NOW, repository_service=no_service)


def test_demo_story_matches_the_scenario() -> None:
    """The replayed deploy is the scenario's v1.8.2 commit, deployed before the first DB error."""
    deliveries = demo_deliveries(NOW)
    events = [normalize(event_type, payload) for event_type, _, payload in deliveries]
    push_main, push_tag, deploy = events
    v182 = next(
        d
        for d in scenario.build_environment(NOW, incident=True).deployments
        if d.version == "v1.8.2"
    )
    assert v182.commit_sha and RELEASE_SHA.startswith(v182.commit_sha)
    assert push_main.occurred_at < push_tag.occurred_at < deploy.started_at == v182.timestamp
    assert deploy.completed_at == NOW - timedelta(seconds=135)
    first_db_error = next(
        log
        for log in scenario.build_environment(NOW, incident=True).logs
        if log.message == "Database connection failed"
    )
    assert deploy.completed_at < first_db_error.timestamp
    assert deploy.is_production_deployment_success
    assert len({delivery_id for _, delivery_id, _ in deliveries}) == 3


# --- the read-only tool -------------------------------------------------------------------------


def executor(db: AsyncSession) -> ToolExecutor:
    return ToolExecutor(AgentName.INVESTIGATION, db, max_calls=8, max_retries=2)


async def test_cicd_tool_is_bounded_and_filtered(client: AsyncClient, db: AsyncSession) -> None:
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    incident = await db.get(Incident, incident_id)
    assert incident is not None
    t0 = incident.created_at.replace(tzinfo=UTC)
    tools = executor(db)

    events = await tools.call(
        "get_recent_cicd_events", service="payment-api", since=t0 - CICD_WINDOW, until=t0
    )
    assert [e.event_type for e in events] == ["push", "push", "workflow_run"]  # oldest first
    assert all(e.service_name == "payment-api" for e in events)
    assert (
        await tools.call(
            "get_recent_cicd_events", service="auth-api", since=t0 - CICD_WINDOW, until=t0
        )
        == []
    )

    for bad in (
        {"service": "payment-api", "since": t0 - timedelta(hours=25), "until": t0},
        {"service": "payment-api", "since": t0, "until": t0 - timedelta(minutes=1)},
        {"service": "payment-api", "since": t0, "until": t0, "limit": 11},
        {"service": "unknown-api", "since": t0, "until": t0},
        {"service": "payment-api", "since": t0, "until": t0, "sql": "DROP TABLE"},
    ):
        with pytest.raises(ToolArgumentError):
            await tools.call("get_recent_cicd_events", **bad)


async def test_agent_cannot_reach_github(db: AsyncSession) -> None:
    tools = executor(db)
    for name in ("call_github_api", "trigger_workflow", "http_request", "run_shell"):
        with pytest.raises(ToolPermissionError):
            await tools.call(name)


# --- investigation / RCA ------------------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    for module in (investigation, root_cause, remediation, verification):
        monkeypatch.setattr(module, "get_ai_provider", lambda: provider)
    return provider


async def test_cicd_evidence_is_cited_and_fabrications_dropped(
    client: AsyncClient, fake: FakeProvider
) -> None:
    fake.result = GOOD_ANALYSIS | {
        "findings": [
            *GOOD_ANALYSIS["findings"],
            {
                "kind": "observation",
                "statement": "deploy-production shipped v1.8.2 just before the errors.",
                "evidence_ids": ["C3", "L5"],
            },
            {"kind": "observation", "statement": "Made up.", "evidence_ids": ["C99"]},
        ]
    }
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    assert (await client.post(f"/api/incidents/{incident_id}/investigate")).status_code == 201

    result = (await client.get(f"/api/incidents/{incident_id}/investigation")).json()["result"]
    cicd = [e for e in result["evidence"] if e["source"] == "cicd"]
    assert [e["id"] for e in cicd] == ["C1", "C2", "C3"]
    assert "push to main" in cicd[0]["fact"] and "e4a7c52" in cicd[0]["fact"]
    assert "tag v1.8.2" in cicd[1]["fact"]
    deploy = cicd[2]
    assert "deploy-production" in deploy["fact"] and "COMPLETED/SUCCESS" in deploy["fact"]
    assert "version v1.8.2 (from tag)" in deploy["fact"]
    assert "delivery_id" not in deploy["data"] and deploy["data"]["commit_sha"] == RELEASE_SHA
    statements = [f["statement"] for f in result["findings"]]
    assert "deploy-production shipped v1.8.2 just before the errors." in statements
    assert "Made up." not in statements  # C99 was never supplied

    prompt = fake.calls_for(investigation.InvestigationAnalysis)[0]
    assert "[CI/CD (GitHub)]" in prompt and "C3 " in prompt
    assert "configuration updates" not in prompt and "delivery" not in prompt.lower()

    events = (await client.get(f"/api/incidents/{incident_id}/events")).json()
    cicd_found = [
        e
        for e in events
        if e["event_type"] == "evidence_found" and e["metadata"]["source"] == "cicd"
    ]
    assert (
        len(cicd_found) == 1 and "deploy-production: SUCCESS (v1.8.2)" in cicd_found[0]["message"]
    )


async def test_rca_correlates_cicd_and_the_report_keeps_it(
    client: AsyncClient, fake: FakeProvider
) -> None:
    fake.results["RootCauseAnalysis"] = GOOD_RCA | {
        "supporting_evidence": ["C3", *GOOD_RCA["supporting_evidence"]]
    }
    incident_id = (await client.post("/api/incidents/simulate")).json()["id"]
    for step in ("investigate", "analyze", "remediate"):
        assert (await client.post(f"/api/incidents/{incident_id}/{step}")).status_code == 201
    assert (await client.post(f"/api/incidents/{incident_id}/approve")).status_code == 200
    assert (await client.post(f"/api/incidents/{incident_id}/verify")).status_code == 201

    rca_prompt = fake.calls_for(RootCauseAnalysis)[0]
    timeline = rca_prompt.split("Evidence timeline:")[1]
    assert timeline.index("C3 ") < timeline.index("Database connection failed")
    report = (await client.get(f"/api/incidents/{incident_id}/report")).json()["report"]
    assert report["summary"]["final_status"] == "RESOLVED"
    assert any(e["id"] == "C3" for e in report["root_cause"]["supporting_evidence"])
    assert any(e["source"] == "cicd" for e in report["investigation"]["evidence"])
    assert "deployment_detected" in [e["event_type"] for e in report["timeline"]]
    assert report["execution"]["parameters"]["to_version"] == "v1.8.1"  # remediation unchanged


# --- read API -----------------------------------------------------------------------------------


async def test_cicd_api_filters_and_bounds(client: AsyncClient) -> None:
    await client.post("/api/incidents/simulate")

    all_events = (await client.get("/api/cicd/events")).json()
    assert [e["event_type"] for e in all_events] == ["workflow_run", "push", "push"]  # newest first
    deploys = (await client.get("/api/cicd/events", params={"category": "DEPLOYMENT"})).json()
    assert [e["workflow_name"] for e in deploys] == ["deploy-production"]
    by_sha = (await client.get("/api/cicd/events", params={"commit_sha": "E4A7C52"})).json()
    assert len(by_sha) == 3
    one = (await client.get(f"/api/cicd/events/{deploys[0]['id']}")).json()
    assert one["id"] == deploys[0]["id"]

    for params in (
        {"limit": 51},
        {"commit_sha": "%' OR 1=1 --"},
        {"service": "unknown-api"},
        {"repository": "not a repo"},
        {"category": "EVERYTHING"},
    ):
        assert (await client.get("/api/cicd/events", params=params)).status_code == 422
    assert (await client.get("/api/cicd/events/999999")).status_code == 404


async def test_reset_clears_demo_cicd_events(client: AsyncClient) -> None:
    await client.post("/api/incidents/simulate")
    reset = (await client.post("/api/demo/reset")).json()
    assert reset["cicd_events_deleted"] == 3
    assert (await client.get("/api/cicd/events")).json() == []


# --- static safety ------------------------------------------------------------------------------

WEBHOOK_MODULES = [
    *sorted((APP / "github").glob("*.py")),
    APP / "services" / "cicd.py",
    APP / "api" / "routes" / "webhooks.py",
    APP / "api" / "routes" / "cicd.py",
    APP / "tools" / "cicd_tools.py",
]


@pytest.mark.parametrize("path", WEBHOOK_MODULES, ids=lambda p: p.name)
def test_webhook_code_has_no_execution_ai_or_http_client(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile", "__import__", "open"}
        elif isinstance(node, ast.keyword):
            assert node.arg != "shell"
    forbidden = ("subprocess", "os", "shlex", "httpx", "requests", "urllib", "aiohttp", "app.ai")
    assert not {m for m in imported if m.split(".")[0] in forbidden or m.startswith("app.ai")}
    assert "text(" not in path.read_text(encoding="utf-8")  # no raw SQL strings
