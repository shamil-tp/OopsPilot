"""Code Review Agent: AI review of each push to a monitored project's default branch.

Flow (background, after the webhook has answered GitHub):
    push recorded -> eligible? (CODE_REVIEW_ENABLED, configured project, default branch)
    -> CodeReview PENDING (unique per repository + commit: never reviewed twice)
    -> diff from the GitHub API, filtered / redacted / capped (app.services.code_diff)
    -> syntax check of each source file (parsed, never run; app.services.static_checks)
    -> 1 structured AI call -> guardrails -> COMPLETED (or SKIPPED / FAILED with a safe reason)

Guardrails: findings must name a file that was in the reviewed diff (others are dropped), at most
10, most severe first. The review only reports and recommends; it never changes the repository.
If an incident is active on the project, the result is added to its timeline, and the
investigation of later incidents can cite the review as evidence.
"""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.base import AIProvider, GenerationOptions
from app.ai.factory import get_ai_provider
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import CicdEvent, CodeReview, Incident
from app.models.enums import CodeReviewStatus, IncidentStatus, RiskLevel
from app.prompts.code_review import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.code_review import SEVERITY_ORDER, CodeReviewAnalysis, ReviewFinding
from app.services import code_diff, static_checks
from app.services.agent_events import EventRecorder
from app.services.service_catalog import real_services

logger = get_logger(__name__)

MAX_FINDINGS = 10
MAX_SUMMARY_CHARS = 800
GENERATION = GenerationOptions(
    system_instruction=SYSTEM_INSTRUCTION, temperature=0.0, max_output_tokens=2048
)
_ACTIVE = (
    IncidentStatus.DETECTED,
    IncidentStatus.INVESTIGATING,
    IncidentStatus.ANALYZING,
    IncidentStatus.AWAITING_APPROVAL,
    IncidentStatus.REMEDIATING,
    IncidentStatus.VERIFYING,
)
_tasks: set[asyncio.Task[None]] = set()


def spawn(coroutine: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coroutine)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def eligible(event: CicdEvent) -> bool:
    """A push of a new commit to the default branch of a configured project's repository."""
    if not get_settings().code_review_enabled or event.event_type != "push":
        return False
    if not event.commit_sha or event.meta.get("deleted") or event.meta.get("ref_type") != "branch":
        return False
    project = next((s for s in real_services() if s.name == event.service_name), None)
    if project is None or not project.repository:
        return False
    default = event.meta.get("default_branch") or "main"
    return event.branch == default


def schedule(event_id: int) -> None:
    """Called by the webhook after a push was recorded; returns immediately."""
    spawn(review_push(event_id))


async def _create(db: AsyncSession, event: CicdEvent) -> CodeReview | None:
    review = CodeReview(
        repository=event.repository,
        service_name=event.service_name or "",
        cicd_event_id=event.id,
        commit_sha=event.commit_sha or "",
        base_sha=event.meta.get("before_sha"),
        branch=event.branch,
        commit_message=event.commit_message,
        author=event.actor,
        status=CodeReviewStatus.PENDING,
    )
    db.add(review)
    try:
        await db.commit()
    except IntegrityError:  # this commit was already reviewed (or is being reviewed)
        await db.rollback()
        return None
    return review


async def review_push(
    event_id: int,
    *,
    provider: AIProvider | None = None,
    http: httpx.AsyncClient | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> None:
    async with session_factory() as db:
        event = await db.get(CicdEvent, event_id)
        if event is None or not eligible(event):
            return
        review = await _create(db, event)
        if review is None:
            return
        await run_review(review.id, provider=provider, http=http, session_factory=session_factory)


async def run_review(
    review_id: int,
    *,
    provider: AIProvider | None = None,
    http: httpx.AsyncClient | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> None:
    """Review a PENDING code review. Never raises: failures are stored with a safe reason."""
    settings = get_settings()
    token = settings.github_token.get_secret_value() if settings.github_token else None
    async with session_factory() as db:
        review = await db.get(CodeReview, review_id)
        if review is None or review.status is not CodeReviewStatus.PENDING:
            return
        static: list[dict[str, Any]] = []
        try:
            client = http or httpx.AsyncClient(timeout=20, headers={"User-Agent": "OpsPilot"})
            try:
                changes = await code_diff.fetch_changes(
                    client, review.repository, review.base_sha, review.commit_sha, token or None
                )
                prepared = code_diff.prepare(changes, max_chars=settings.code_review_max_diff_chars)
                static = await _static_findings(client, review, prepared.files, token or None)
            finally:
                if http is None:
                    await client.aclose()
            review.files = prepared.files
            review.skipped_files = prepared.skipped
            review.truncated = prepared.truncated
            if not prepared.files:
                review.status = CodeReviewStatus.SKIPPED
                review.summary = (
                    "No reviewable code in this push "
                    "(only lockfiles, assets, build output or secret files)."
                )
            else:
                ai = provider or get_ai_provider()
                analysis = await ai.generate_structured(
                    build_prompt(
                        repository=review.repository,
                        branch=review.branch,
                        commit=review.commit_sha,
                        message=review.commit_message,
                        diff=prepared.text,
                        truncated=prepared.truncated,
                    ),
                    CodeReviewAnalysis,
                    options=GENERATION,
                )
                findings = _guard(analysis.findings, prepared.names)
                review.findings = _merge(static, [f.model_dump(mode="json") for f in findings])
                review.risk = RiskLevel.HIGH if static else analysis.risk
                review.summary = analysis.summary.strip()[:MAX_SUMMARY_CHARS]
                review.model = f"{ai.name}:{getattr(ai, 'model', 'unknown')}"
                review.status = CodeReviewStatus.COMPLETED
        except code_diff.DiffUnavailable as exc:
            review.status, review.error = CodeReviewStatus.FAILED, str(exc)[:300]
        except Exception as exc:  # stored as a safe, generic reason; never crashes the backend
            review.status = CodeReviewStatus.FAILED
            review.error = f"Review failed ({type(exc).__name__}); retry it from the dashboard"
            review.findings = static  # syntax errors found before the failure are still shown
        review.completed_at = datetime.now(UTC)
        await _announce(db, review)
        await db.commit()
        logger.info(
            "code_review_finished",
            extra={
                "review_id": review.id,
                "repository": review.repository,
                "status": review.status,
                "findings": len(review.findings),
            },
        )


async def _static_findings(
    client: httpx.AsyncClient, review: CodeReview, files: list[dict[str, Any]], token: str | None
) -> list[dict[str, Any]]:
    """Syntax check of each reviewed source file at the pushed commit (parsed, never run)."""
    found: list[dict[str, Any]] = []
    for name in static_checks.checkable(files):
        source = await code_diff.fetch_file(
            client,
            review.repository,
            name,
            review.commit_sha,
            token,
            max_bytes=static_checks.MAX_BYTES,
        )
        finding = static_checks.check(name, source) if source is not None else None
        if finding:
            found.append(finding)
    return found


def _merge(static: list[dict[str, Any]], ai: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Static findings first; an AI finding about the same spot is not repeated."""

    def same_spot(finding: dict[str, Any]) -> bool:
        return any(
            s["file"] == finding["file"]
            and finding.get("line") is not None
            and abs(s["line"] - finding["line"]) <= 2
            for s in static
        )

    return (static + [f for f in ai if not same_spot(f)])[:MAX_FINDINGS]


def _guard(findings: list[ReviewFinding], files: set[str]) -> list[ReviewFinding]:
    kept = [f for f in findings if f.file in files and f.title.strip() and f.recommendation.strip()]
    kept.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return kept[:MAX_FINDINGS]


async def _announce(db: AsyncSession, review: CodeReview) -> None:
    """Add the review to the timeline of an incident already active on the project."""
    incident_id = await db.scalar(
        select(Incident.id)
        .where(Incident.service_name == review.service_name, Incident.status.in_(_ACTIVE))
        .limit(1)
    )
    if incident_id is None:
        return
    worst = review.findings[0]["severity"] if review.findings else None
    EventRecorder(db, incident_id, None).emit(
        "code_review_completed",
        f"Code review of {review.commit_sha[:7]} ({review.status}): "
        + (
            f"{len(review.findings)} finding(s), most severe {worst}"
            if review.findings
            else review.summary or ""
        ),
        source="github",
        code_review_id=review.id,
        risk=review.risk,
    )


async def retry(db: AsyncSession, review_id: int) -> CodeReview | None:
    """Re-run a FAILED review (e.g. after a GitHub rate limit). Returns it, or None if unknown."""
    review = await db.get(CodeReview, review_id)
    if review is None:
        return None
    if review.status is CodeReviewStatus.FAILED:
        review.status, review.error, review.completed_at = CodeReviewStatus.PENDING, None, None
        await db.commit()
        spawn(run_review(review.id))
    return review
