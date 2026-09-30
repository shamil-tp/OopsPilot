"""CI/CD telemetry: ingestion of normalized GitHub events, deployment correlation, and queries.

Ingestion is deterministic (no AI) and idempotent:
- `delivery_id` (X-GitHub-Delivery) is unique in the database; a redelivery returns the stored
  event and changes nothing. Concurrent duplicates are resolved by the constraint, not by a check.
- A successful *production deployment* event is correlated with the deployments table: it links
  to an existing deployment of the same service and commit (within an hour), or creates one.
  `deployment_key` (unique; one per workflow run attempt / GitHub deployment) guarantees that two
  different deliveries of the same run never create two deployments. Build/test workflows, failed
  deployments, and non-production environments never create deployments; they stay evidence.
- A CI/CD failure never creates an incident. If an incident is already active on the service,
  the event is added to its timeline (agent_events), which the incident WebSocket streams.
"""

from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.github.demo import DEMO_REPOSITORY, demo_deliveries
from app.github.normalize import NormalizedEvent, normalize
from app.models import CicdEvent, Deployment, Incident
from app.models.enums import (
    CicdCategory,
    CicdConclusion,
    CicdStatus,
    DeploymentStatus,
    IncidentStatus,
)
from app.services.agent_events import EventRecorder
from app.services.service_catalog import SERVICE_NAMES

logger = get_logger(__name__)

DEMO_SERVICE = "payment-api"
CORRELATION_WINDOW = timedelta(hours=1)
_ACTIVE = (
    IncidentStatus.DETECTED,
    IncidentStatus.INVESTIGATING,
    IncidentStatus.ANALYZING,
    IncidentStatus.AWAITING_APPROVAL,
    IncidentStatus.REMEDIATING,
    IncidentStatus.VERIFYING,
)


@dataclass(frozen=True)
class IngestResult:
    status: Literal["recorded", "duplicate", "ignored"]
    event: CicdEvent | None = None
    reason: str | None = None


def repository_service(repository: str) -> str | None:
    """The service a repository deploys: the built-in demo repository, or GITHUB_REPOSITORY."""
    if repository.lower() == DEMO_REPOSITORY:
        return DEMO_SERVICE
    settings = get_settings()
    configured = settings.github_repository
    if configured and repository.lower() == configured.lower():
        return settings.github_service if settings.github_service in SERVICE_NAMES else None
    return None


def repository_allowed(repository: str) -> bool:
    configured = get_settings().github_repository
    return configured is None or repository.lower() in {configured.lower(), DEMO_REPOSITORY}


def short_sha(sha: str | None) -> str:
    return sha[:7] if sha else "unknown"


def describe(event: CicdEvent) -> str:
    """One safe, human-readable line (used for timeline events)."""
    sha = short_sha(event.commit_sha)
    version = f", version {event.version}" if event.version else ""
    if event.event_type == "push":
        tag = event.meta.get("tag")
        if tag:
            return f"GitHub tag {tag} pushed to {event.repository} (commit {sha})"
        message = f" '{event.commit_message}'" if event.commit_message else ""
        by = f" by {event.actor}" if event.actor else ""
        return f"GitHub push to {event.branch or 'a ref'}: commit {sha}{message}{by}"
    result = event.conclusion or event.status
    if event.event_type == "workflow_run":
        run = f" #{event.run_number}" if event.run_number else ""
        env = f", {event.environment}" if event.environment else ""
        return (
            f"GitHub workflow {event.workflow_name or 'unnamed'}{run} "
            f"({event.category.lower()}{env}): {result} (commit {sha}{version})"
        )
    target = event.environment or "unknown"
    return f"GitHub deployment to {target}: {result} (commit {sha}{version})"


async def by_delivery(db: AsyncSession, delivery_id: str) -> CicdEvent | None:
    return await db.scalar(
        select(CicdEvent)
        .where(CicdEvent.delivery_id == delivery_id)
        .execution_options(populate_existing=True)
    )


async def ingest(
    db: AsyncSession,
    event_type: str,
    delivery_id: str,
    payload: dict[str, Any],
    *,
    received_at: datetime | None = None,
) -> IngestResult:
    """Store one (already authenticated) delivery. Raises PayloadError for malformed payloads."""
    existing = await by_delivery(db, delivery_id)
    if existing is not None:
        return IngestResult("duplicate", existing)
    normalized = normalize(
        event_type,
        delivery_id,
        payload,
        received_at=received_at or datetime.now(UTC),
        repository_service=repository_service,
    )
    if normalized is None:
        return IngestResult("ignored", reason=f"event '{event_type}' is not processed")
    if not repository_allowed(normalized.repository):
        return IngestResult("ignored", reason="repository is not the configured GITHUB_REPOSITORY")

    try:
        event = await _record(db, normalized, create_deployment=True)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await by_delivery(db, delivery_id)
        if existing is not None:  # the same delivery was stored concurrently
            logger.info("cicd_duplicate_delivery", extra={"delivery_id": delivery_id})
            return IngestResult("duplicate", existing)
        # Another delivery of the same run created the deployment first: link to it instead.
        event = await _record(db, normalized, create_deployment=False)
        await db.commit()
    await db.refresh(event)
    logger.info(
        "cicd_event_recorded",
        extra={
            "delivery_id": delivery_id,
            "event_type": event.event_type,
            "category": event.category,
            "repository": event.repository,
            "service": event.service_name,
            "deployment_id": event.deployment_id,
        },
    )
    return IngestResult("recorded", event)


async def _record(
    db: AsyncSession, normalized: NormalizedEvent, *, create_deployment: bool
) -> CicdEvent:
    values = {f.name: getattr(normalized, f.name) for f in fields(normalized)}
    deployment_key = values.pop("deployment_key")
    event = CicdEvent(provider="github", **values)

    if event.version is None and event.commit_sha and event.category is not CicdCategory.COMMIT:
        tag = await _tag_for_commit(db, event.repository, event.commit_sha)
        if tag is not None:
            event.version, event.version_source = tag, "tag"

    deployment, created = await _correlate(db, normalized, event, deployment_key, create_deployment)
    if deployment is not None:
        event.deployment_id = deployment.id
        event.deployment_key = deployment_key if created else None
    db.add(event)
    await db.flush()
    await _announce(db, event, deployment, created)
    return event


async def _tag_for_commit(db: AsyncSession, repository: str, sha: str) -> str | None:
    """A release tag already pushed for this commit (push to refs/tags/<version>)."""
    return await db.scalar(
        select(CicdEvent.version)
        .where(
            CicdEvent.repository == repository,
            CicdEvent.commit_sha == sha,
            CicdEvent.version_source == "tag",
            CicdEvent.event_type == "push",
        )
        .order_by(CicdEvent.occurred_at.desc(), CicdEvent.id.desc())
        .limit(1)
    )


async def _correlate(
    db: AsyncSession,
    normalized: NormalizedEvent,
    event: CicdEvent,
    deployment_key: str | None,
    create: bool,
) -> tuple[Deployment | None, bool]:
    """Returns (deployment, created) for a successful production deployment, else (None, False)."""
    service, sha = event.service_name, event.commit_sha
    if not (normalized.is_production_deployment_success and service and sha and deployment_key):
        return None, False
    deployed_at = event.started_at or event.occurred_at

    if not create:  # another delivery of this run already created it
        linked = await db.scalar(
            select(CicdEvent.deployment_id).where(CicdEvent.deployment_key == deployment_key)
        )
        return (await db.get(Deployment, linked) if linked else None), False

    candidates = await db.scalars(
        select(Deployment)
        .where(
            Deployment.service_name == service,
            Deployment.timestamp >= deployed_at - CORRELATION_WINDOW,
            Deployment.timestamp <= deployed_at + CORRELATION_WINDOW,
        )
        .order_by(Deployment.timestamp.desc(), Deployment.id.desc())
        .limit(20)
    )
    for deployment in candidates:
        known = (deployment.commit_sha or "").lower()
        if len(known) >= 7 and (sha.startswith(known) or known.startswith(sha)):
            return deployment, False

    deployment = Deployment(
        service_name=service,
        # Never invented: the resolved version, else the short commit SHA identifies the build.
        version=event.version or sha[:7],
        timestamp=deployed_at,
        status=DeploymentStatus.SUCCEEDED,
        commit_sha=sha,
    )
    db.add(deployment)
    await db.flush()
    return deployment, True


async def _announce(
    db: AsyncSession, event: CicdEvent, deployment: Deployment | None, created: bool
) -> None:
    """Add the event to the timeline of incidents already active on its service."""
    if event.service_name is None:
        return
    incidents = await db.scalars(
        select(Incident.id).where(
            Incident.service_name == event.service_name, Incident.status.in_(_ACTIVE)
        )
    )
    meta = {
        "source": "github",
        "cicd_event_id": event.id,
        "delivery_id": event.delivery_id,
        "category": event.category,
        "status": event.status,
        "conclusion": event.conclusion,
        "commit_sha": short_sha(event.commit_sha),
        "version": event.version,
        "workflow_name": event.workflow_name,
        "occurred_at": event.occurred_at,
    }
    for incident_id in incidents:
        recorder = EventRecorder(db, incident_id, None)
        recorder.emit("cicd_event_recorded", describe(event), **meta)
        if deployment is not None:
            verb = "recorded from" if created else "linked to"
            recorder.emit(
                "deployment_detected",
                f"Deployment {deployment.version} of {deployment.service_name} "
                f"(commit {short_sha(deployment.commit_sha)}) {verb} GitHub "
                f"{event.workflow_name or event.event_type}",
                **meta | {"deployment_id": deployment.id, "version": deployment.version},
            )


# --- queries ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CicdFilter:
    repository: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    workflow: str | None = None
    service: str | None = None
    category: CicdCategory | None = None
    status: CicdStatus | None = None
    conclusion: CicdConclusion | None = None
    since: datetime | None = None
    until: datetime | None = None


async def list_events(db: AsyncSession, f: CicdFilter, *, limit: int) -> list[CicdEvent]:
    """The newest `limit` matching events, newest first. Every filter is a bound parameter."""
    stmt = select(CicdEvent)
    if f.repository:
        stmt = stmt.where(CicdEvent.repository == f.repository)
    if f.branch:
        stmt = stmt.where(CicdEvent.branch == f.branch)
    if f.commit_sha:  # validated as hex by the API, so no LIKE wildcards can appear
        stmt = stmt.where(CicdEvent.commit_sha.startswith(f.commit_sha.lower()))
    if f.workflow:
        stmt = stmt.where(CicdEvent.workflow_name == f.workflow)
    if f.service:
        stmt = stmt.where(CicdEvent.service_name == f.service)
    if f.category:
        stmt = stmt.where(CicdEvent.category == f.category)
    if f.status:
        stmt = stmt.where(CicdEvent.status == f.status)
    if f.conclusion:
        stmt = stmt.where(CicdEvent.conclusion == f.conclusion)
    if f.since:
        stmt = stmt.where(CicdEvent.occurred_at >= f.since)
    if f.until:
        stmt = stmt.where(CicdEvent.occurred_at <= f.until)
    rows = await db.scalars(
        stmt.order_by(CicdEvent.occurred_at.desc(), CicdEvent.id.desc()).limit(limit)
    )
    return list(rows)


# --- demo ---------------------------------------------------------------------------------------


async def replay_demo(db: AsyncSession, anchor: datetime) -> int:
    """Ingest the deterministic demo deliveries (same path as the webhook, minus HTTP)."""
    recorded = 0
    for event_type, delivery_id, payload in demo_deliveries(anchor):
        result = await ingest(db, event_type, delivery_id, payload)
        recorded += result.status == "recorded"
    return recorded


async def clear_demo(db: AsyncSession) -> int:
    """Delete the demo repository's events (real repositories' events are kept). No commit."""
    result = await db.execute(delete(CicdEvent).where(CicdEvent.repository == DEMO_REPOSITORY))
    return result.rowcount or 0
