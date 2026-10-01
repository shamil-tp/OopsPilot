"""AI code review of pushes: diff preparation (filtering, redaction, size cap), GitHub fetch,
eligibility, the review agent with its guardrails, API, and incident evidence.
Fake AI and fake GitHub only."""

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

from app.agents import code_review
from app.agents.evidence import EvidenceCollector, IncidentContext
from app.core.config import MonitoredProject, get_settings
from app.github.security import sign
from app.models import AgentEvent, CicdEvent, CodeReview, Incident
from app.models.enums import AgentName, IncidentStatus, Severity
from app.services import code_diff
from app.services.agent_events import EventRecorder
from app.tools import ToolExecutor
from tests.investigation_fakes import FakeProvider

pytestmark = pytest.mark.usefixtures("clean_demo", "isolated_real_projects")

SERVICE = "mallutyping-web"
REPO = "MrNihalT/mallutyping"
SECRET = "code-review-test-secret"
BEFORE = "1" * 40
AFTER = "2" * 40
GEMINI_LIKE = "AIza" + "B" * 35

FILES = [
    {
        "filename": "src/app/page.tsx",
        "status": "modified",
        "additions": 3,
        "deletions": 1,
        "patch": "@@ -10,4 +10,6 @@\n-const x = data.items\n"
        "+const x = data.items.map(i => i.name)\n"
        f'+const key = "{GEMINI_LIKE}"\n+password = "hunter2hunter2"\n',
    },
    {
        "filename": "package-lock.json",
        "status": "modified",
        "additions": 900,
        "deletions": 4,
        "patch": "@@ x",
    },
    {
        "filename": ".env.production",
        "status": "added",
        "additions": 2,
        "deletions": 0,
        "patch": "+DB=secret",
    },
    {"filename": "public/logo.png", "status": "added", "additions": 0, "deletions": 0},
]


def analysis(**overrides: Any) -> dict[str, Any]:
    return {
        "summary": "One crash risk on missing data.",
        "risk": "HIGH",
        "findings": [
            {
                "severity": "medium",
                "category": "maintainability",
                "file": "src/app/page.tsx",
                "line": 12,
                "title": "Hard-coded key",
                "explanation": "A credential is committed.",
                "recommendation": "Move it to an environment variable.",
            },
            {
                "severity": "high",
                "category": "bug",
                "file": "src/app/page.tsx",
                "line": 11,
                "title": "items may be undefined",
                "explanation": "data.items is unchecked: the page crashes on an empty API reply.",
                "recommendation": "Use (data.items ?? []).map(...).",
            },
            {
                "severity": "critical",
                "category": "security",
                "file": "src/server/not-in-diff.ts",
                "title": "Invented",
                "explanation": "x",
                "recommendation": "y",
            },
        ],
    } | overrides


@pytest.fixture(autouse=True)
async def project(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "github_webhook_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "github_repository", None)
    monkeypatch.setattr(settings, "code_review_enabled", True)
    monkeypatch.setattr(settings, "github_token", None)
    monkeypatch.setattr(
        settings,
        "monitored_projects",
        [
            MonitoredProject(
                name="Mallu Typing", service=SERVICE, url="https://m.example/", repository=REPO
            )
        ],
    )
    await db.execute(delete(CodeReview).where(CodeReview.service_name == SERVICE))
    for model in (Incident, CicdEvent):
        await db.execute(delete(model).where(model.service_name == SERVICE))
    await db.commit()


@pytest.fixture
def fake() -> FakeProvider:
    provider = FakeProvider()
    provider.results["CodeReviewAnalysis"] = analysis()
    return provider


def github(
    files: list[dict[str, Any]] = FILES, status: int = 200, seen: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={"files": files} if status == 200 else {"message": "x"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def push_payload(
    ref: str = "refs/heads/main", repo: str = REPO, before: str = BEFORE
) -> dict[str, Any]:
    return {
        "ref": ref,
        "before": before,
        "after": AFTER,
        "repository": {"full_name": repo, "default_branch": "main"},
        "head_commit": {"id": AFTER, "message": "Show lesson names", "author": {"username": "dev"}},
        "commits": [],
    }


async def deliver(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    response = await client.post(
        "/api/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )
    return response.json()


# --- diff preparation ---------------------------------------------------------------------------


def test_prepare_filters_redacts_and_reports() -> None:
    prepared = code_diff.prepare(FILES, max_chars=60_000)

    assert prepared.names == {"src/app/page.tsx"}
    reasons = {s["filename"]: s["reason"] for s in prepared.skipped}
    assert reasons["package-lock.json"] == "lockfile"
    assert "secret" in reasons[".env.production"]
    assert "binary" in reasons["public/logo.png"]
    assert GEMINI_LIKE not in prepared.text and "hunter2hunter2" not in prepared.text
    assert "DB=secret" not in prepared.text  # the .env file never reaches the review
    assert prepared.text.count("[REDACTED]") == 2 and prepared.redactions == 2


@pytest.mark.parametrize(
    "secret",
    [
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
        "postgres://user:s3cretpass@db.example.com/x",
        "ghp_" + "a" * 36,
        "api_key: 'abcdef123456'",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
    ],
)
def test_redaction(secret: str) -> None:
    clean, count = code_diff.redact(f"+{secret}\n")
    assert count >= 1 and "[REDACTED" in clean
    for fragment in ("MIIabc", "s3cretpass", "a" * 36, "abcdef123456", "SflKxwRJSMeKKF2QT4"):
        assert fragment not in clean


def test_size_limit_truncates_and_lists_files() -> None:
    big = [
        {"filename": f"src/f{i}.ts", "status": "modified", "patch": "+" + "x" * 1500}
        for i in range(5)
    ]
    prepared = code_diff.prepare(big, max_chars=3_500)
    assert prepared.truncated and len(prepared.files) == 2
    assert [s["reason"] for s in prepared.skipped] == ["review size limit reached"] * 3


# --- GitHub API ---------------------------------------------------------------------------------


async def test_fetch_uses_compare_or_single_commit_and_token() -> None:
    seen: list[httpx.Request] = []
    async with github(seen=seen) as http:
        await code_diff.fetch_changes(http, REPO, BEFORE, AFTER, "tok")
        await code_diff.fetch_changes(http, REPO, "0" * 40, AFTER, None)
    assert str(seen[0].url) == f"https://api.github.com/repos/{REPO}/compare/{BEFORE}...{AFTER}"
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert str(seen[1].url) == f"https://api.github.com/repos/{REPO}/commits/{AFTER}"
    assert "Authorization" not in seen[1].headers


@pytest.mark.parametrize(
    ("status", "message"), [(404, "GITHUB_TOKEN"), (403, "rate limit"), (500, "HTTP 500")]
)
async def test_fetch_errors_are_safe(status: int, message: str) -> None:
    async with github(status=status) as http:
        with pytest.raises(code_diff.DiffUnavailable, match=message):
            await code_diff.fetch_changes(http, REPO, BEFORE, AFTER, None)


async def test_fetch_rejects_unsafe_input() -> None:
    async with github() as http:
        for repo, sha in (("../../x", AFTER), (REPO, "main;rm -rf"), (REPO, "../" + AFTER)):
            with pytest.raises(code_diff.DiffUnavailable, match="invalid"):
                await code_diff.fetch_changes(http, repo, None, sha, None)


# --- which pushes are reviewed ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "reviewed"),
    [
        (push_payload(), True),
        (push_payload(ref="refs/heads/feature-x"), False),
        (push_payload(ref="refs/tags/v1.0.0"), False),
        (push_payload(repo="someone/else"), False),
    ],
)
async def test_webhook_schedules_reviews_only_for_the_default_branch(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], reviewed: bool
) -> None:
    scheduled: list[int] = []
    monkeypatch.setattr(code_review, "schedule", scheduled.append)
    event = (await deliver(client, payload))["event"]
    assert (scheduled == [event["id"]]) is reviewed


async def test_disabled_reviews_nothing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "code_review_enabled", False)
    scheduled: list[int] = []
    monkeypatch.setattr(code_review, "schedule", scheduled.append)
    await deliver(client, push_payload())
    assert scheduled == []


# --- the review ---------------------------------------------------------------------------------


async def reviewed(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeProvider,
    http: httpx.AsyncClient,
) -> int:
    monkeypatch.setattr(code_review, "schedule", lambda _: None)
    event_id = (await deliver(client, push_payload()))["event"]["id"]
    await code_review.review_push(event_id, provider=fake, http=http)
    return event_id


async def test_review_completes_with_guarded_findings(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with github() as http:
        event_id = await reviewed(client, monkeypatch, fake, http)
        await code_review.review_push(
            event_id, provider=fake, http=http
        )  # redelivery: no second review

    [review] = (await client.get("/api/code-reviews", params={"service": SERVICE})).json()
    assert review["status"] == "COMPLETED" and review["risk"] == "HIGH"
    assert [f["severity"] for f in review["findings"]] == [
        "high",
        "medium",
    ]  # sorted; invented file dropped
    assert all(f["file"] == "src/app/page.tsx" for f in review["findings"])
    assert (
        review["files"][0]["filename"] == "src/app/page.tsx" and len(review["skipped_files"]) == 3
    )
    assert (await client.get(f"/api/code-reviews/{review['id']}")).json()["id"] == review["id"]
    prompt = fake.calls[0][0]
    assert (
        GEMINI_LIKE not in prompt and "hunter2hunter2" not in prompt and "DB=secret" not in prompt
    )
    assert len(fake.calls) == 1
    assert await db.scalar(select(func.count()).select_from(CodeReview)) == 1


async def test_push_with_nothing_reviewable_is_skipped_without_ai(
    client: AsyncClient, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with github(files=FILES[1:]) as http:
        await reviewed(client, monkeypatch, fake, http)
    [review] = (await client.get("/api/code-reviews")).json()
    assert review["status"] == "SKIPPED" and fake.calls == []


async def test_github_failure_is_stored_and_can_be_retried(
    client: AsyncClient, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with github(status=403) as http:
        await reviewed(client, monkeypatch, fake, http)
    [review] = (await client.get("/api/code-reviews")).json()
    assert review["status"] == "FAILED" and "rate limit" in review["error"]

    started: list[Any] = []
    monkeypatch.setattr(code_review, "spawn", lambda coroutine: started.append(coroutine.close()))
    retried = await client.post(f"/api/code-reviews/{review['id']}/retry")
    assert (
        retried.status_code == 200 and retried.json()["status"] == "PENDING" and len(started) == 1
    )
    assert (await client.post(f"/api/code-reviews/{review['id']}/retry")).status_code == 409
    assert (await client.post("/api/code-reviews/999999/retry")).status_code == 404


async def test_review_appears_on_an_active_incident_and_in_its_evidence(
    client: AsyncClient, db: AsyncSession, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    incident = Incident(
        title="Mallu Typing is down",
        description="x",
        severity=Severity.HIGH,
        status=IncidentStatus.DETECTED,
        service_name=SERVICE,
        created_at=now - timedelta(seconds=5),
        updated_at=now,
    )
    db.add(incident)
    await db.commit()
    async with github() as http:
        await reviewed(client, monkeypatch, fake, http)

    events = list(await db.scalars(select(AgentEvent).where(AgentEvent.incident_id == incident.id)))
    assert any(e.event_type == "code_review_completed" for e in events)

    incident.created_at = datetime.now(UTC) + timedelta(seconds=5)  # the review precedes detection
    await db.commit()
    executor = ToolExecutor(AgentName.INVESTIGATION, db, max_calls=8, max_retries=2)
    package = await EvidenceCollector(
        executor, EventRecorder(db, incident.id, AgentName.INVESTIGATION), db.commit
    ).collect(IncidentContext.of(incident))
    [item] = package.of("code_review")
    assert (
        item.id == "R1" and "risk HIGH" in item.fact and "[high] src/app/page.tsx:11" in item.fact
    )
