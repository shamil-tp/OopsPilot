"""Deterministic normalization of GitHub webhook payloads into CI/CD telemetry (no AI).

Supported events: `push`, `workflow_run`, `deployment_status` (anything else is ignored).

Classification (documented in docs/architecture.md):
- category: push -> COMMIT; deployment_status -> DEPLOYMENT; workflow_run by its name/path:
  deploy|release|rollout -> DEPLOYMENT, test -> TEST, otherwise BUILD.
- environment: deployment_status's environment; for a deployment workflow, "production" when its
  name/path says prod/production (staging/preview/dev likewise); otherwise unknown.
- version (never invented): explicit deployment metadata (`deployment.payload.version`), else a
  release tag (push to refs/tags/<tag>, a tag-triggered workflow, or a deployment of a tag). A
  workflow on a branch may be resolved later from a recorded tag push of the same commit (see
  app.services.cicd). Otherwise the version stays null and only the commit SHA is kept.
- service: `deployment.payload.service`, else a known service named in the workflow name/path,
  else the service configured for the repository.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.github.payloads import DeploymentStatusPayload, PushPayload, WorkflowRunPayload
from app.models.enums import CicdCategory, CicdConclusion, CicdStatus
from app.services.service_catalog import service_names

SUPPORTED_EVENTS = ("push", "workflow_run", "deployment_status")
MAX_CHANGED_FILES = 20
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})

_TAG = re.compile(r"v?\d+(\.\d+){1,3}([-+][0-9A-Za-z.-]+)?")
_EXPLICIT_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}")
_SHA = re.compile(r"[0-9a-f]{7,40}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_DEPLOY = re.compile(r"deploy|release|rollout")
_TEST = re.compile(r"(?<![a-z])tests?(?![a-z])|testing")
_ENVIRONMENT = (
    (re.compile(r"(?<![a-z])prod(uction)?(?![a-z])"), "production"),
    (re.compile(r"(?<![a-z])(staging|stage)(?![a-z])"), "staging"),
    (re.compile(r"(?<![a-z])preview(?![a-z])"), "preview"),
    (re.compile(r"(?<![a-z])dev(elopment)?(?![a-z])"), "development"),
)

_WORKFLOW_STATUS = {
    "requested": CicdStatus.QUEUED,
    "queued": CicdStatus.QUEUED,
    "waiting": CicdStatus.QUEUED,
    "pending": CicdStatus.QUEUED,
    "in_progress": CicdStatus.IN_PROGRESS,
    "completed": CicdStatus.COMPLETED,
}
_CONCLUSION = {
    "success": CicdConclusion.SUCCESS,
    "failure": CicdConclusion.FAILURE,
    "startup_failure": CicdConclusion.FAILURE,
    "cancelled": CicdConclusion.CANCELLED,
    "timed_out": CicdConclusion.TIMED_OUT,
    "neutral": CicdConclusion.NEUTRAL,
    "skipped": CicdConclusion.SKIPPED,
}
_DEPLOYMENT_STATE: dict[str, tuple[CicdStatus, CicdConclusion | None]] = {
    "success": (CicdStatus.COMPLETED, CicdConclusion.SUCCESS),
    "failure": (CicdStatus.COMPLETED, CicdConclusion.FAILURE),
    "error": (CicdStatus.COMPLETED, CicdConclusion.FAILURE),
    "inactive": (CicdStatus.COMPLETED, CicdConclusion.NEUTRAL),
    "in_progress": (CicdStatus.IN_PROGRESS, None),
    "queued": (CicdStatus.QUEUED, None),
    "pending": (CicdStatus.QUEUED, None),
}

RepositoryService = Callable[[str], str | None]


class PayloadError(Exception):
    """The payload does not have the shape of the declared event. Safe to return."""


@dataclass
class NormalizedEvent:
    delivery_id: str
    event_type: str
    category: CicdCategory
    repository: str
    status: CicdStatus
    occurred_at: datetime
    conclusion: CicdConclusion | None = None
    branch: str | None = None
    commit_sha: str | None = None
    commit_message: str | None = None
    actor: str | None = None
    workflow_name: str | None = None
    workflow_run_id: int | None = None
    run_number: int | None = None
    service_name: str | None = None
    environment: str | None = None
    version: str | None = None
    version_source: str | None = None
    html_url: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Identifies "one deployment" (workflow run attempt / GitHub deployment) across deliveries.
    deployment_key: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_production_deployment_success(self) -> bool:
        return (
            self.category is CicdCategory.DEPLOYMENT
            and self.environment in PRODUCTION_ENVIRONMENTS
            and self.status is CicdStatus.COMPLETED
            and self.conclusion is CicdConclusion.SUCCESS
        )


# --- sanitizers ---------------------------------------------------------------------------------


def clip(value: str | None, limit: int) -> str | None:
    """Single line, no control characters (PostgreSQL rejects NUL), at most `limit` chars."""
    if value is None:
        return None
    text = _CONTROL.sub(" ", value).strip()
    return text[:limit] or None


def first_line(value: str | None, limit: int) -> str | None:
    return clip(value.splitlines()[0], limit) if value and value.strip() else None


def commit_sha(value: str | None) -> str | None:
    sha = (value or "").strip().lower()
    return sha if _SHA.fullmatch(sha) and sha.strip("0") else None


def tag_version(value: str | None) -> str | None:
    return value if value and len(value) <= 32 and _TAG.fullmatch(value) else None


def github_url(value: str | None) -> str | None:
    return (
        value if value and value.startswith("https://github.com/") and len(value) <= 300 else None
    )


def _known_service(text: str) -> str | None:
    lowered = text.lower()
    for name in sorted(service_names(), key=len, reverse=True):
        if name in lowered:
            return name
    return None


def _environment(text: str) -> str | None:
    lowered = text.lower()
    for pattern, name in _ENVIRONMENT:
        if pattern.search(lowered):
            return name
    return None


# --- per event ----------------------------------------------------------------------------------


def normalize(
    event_type: str,
    delivery_id: str,
    payload: dict[str, Any],
    *,
    received_at: datetime,
    repository_service: RepositoryService,
) -> NormalizedEvent | None:
    """None for unsupported event types. Raises PayloadError for a malformed payload."""
    try:
        if event_type == "push":
            return _push(
                delivery_id, PushPayload.model_validate(payload), received_at, repository_service
            )
        if event_type == "workflow_run":
            return _workflow_run(
                delivery_id,
                WorkflowRunPayload.model_validate(payload),
                received_at,
                repository_service,
            )
        if event_type == "deployment_status":
            return _deployment_status(
                delivery_id,
                DeploymentStatusPayload.model_validate(payload),
                received_at,
                repository_service,
            )
    except ValidationError as exc:
        fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})[:5]
        raise PayloadError(f"Invalid {event_type} payload: {', '.join(fields)}") from None
    return None


def _push(
    delivery_id: str, p: PushPayload, received_at: datetime, repository_service: RepositoryService
) -> NormalizedEvent:
    repository = p.repository.full_name
    tag = p.ref.removeprefix("refs/tags/") if p.ref.startswith("refs/tags/") else None
    branch = p.ref.removeprefix("refs/heads/") if p.ref.startswith("refs/heads/") else None
    head = p.head_commit
    changed: list[str] = []
    for commit in p.commits:
        for path in (*commit.added, *commit.modified, *commit.removed):
            if path not in changed:
                changed.append(path)
    version = tag_version(tag)
    author = head.author if head else None
    return NormalizedEvent(
        delivery_id=delivery_id,
        event_type="push",
        category=CicdCategory.COMMIT,
        repository=repository,
        status=CicdStatus.COMPLETED,
        occurred_at=p.repository.pushed_at or (head.timestamp if head else None) or received_at,
        branch=clip(branch, 255),
        commit_sha=None if p.deleted else commit_sha(p.after),
        commit_message=first_line(head.message if head else None, 200),
        actor=clip(
            (author.username or author.name if author else None)
            or (p.sender.login if p.sender else None),
            100,
        ),
        service_name=repository_service(repository),
        version=version,
        version_source="tag" if version else None,
        meta={
            "ref_type": "tag" if tag is not None else "branch" if branch is not None else "other",
            "tag": clip(tag, 100),
            "before_sha": commit_sha(p.before),
            "default_branch": clip(p.repository.default_branch, 255),
            "deleted": p.deleted,
            "commit_count": len(p.commits),
            "changed_file_count": len(changed),
            "changed_files": [clip(path, 200) for path in changed[:MAX_CHANGED_FILES]],
        },
    )


def _workflow_run(
    delivery_id: str,
    p: WorkflowRunPayload,
    received_at: datetime,
    repository_service: RepositoryService,
) -> NormalizedEvent:
    run = p.workflow_run
    repository = p.repository.full_name
    name = clip(run.name, 100)
    descriptor = f"{run.name or ''} {run.path or ''}".lower()
    if _DEPLOY.search(descriptor):
        category = CicdCategory.DEPLOYMENT
    elif _TEST.search(descriptor):
        category = CicdCategory.TEST
    else:
        category = CicdCategory.BUILD
    status = _WORKFLOW_STATUS.get((run.status or "").lower(), CicdStatus.QUEUED)
    conclusion = (
        _CONCLUSION.get(run.conclusion.lower(), CicdConclusion.OTHER) if run.conclusion else None
    )
    started = run.run_started_at or run.created_at
    completed = run.updated_at if status is CicdStatus.COMPLETED else None
    version = tag_version(run.head_branch)
    return NormalizedEvent(
        delivery_id=delivery_id,
        event_type="workflow_run",
        category=category,
        repository=repository,
        status=status,
        conclusion=conclusion,
        occurred_at=completed or started or run.updated_at or received_at,
        branch=clip(run.head_branch, 255),
        commit_sha=commit_sha(run.head_sha),
        commit_message=first_line(run.display_title, 200),
        actor=clip(
            (run.actor.login if run.actor else None) or (p.sender.login if p.sender else None), 100
        ),
        workflow_name=name,
        workflow_run_id=run.id,
        run_number=run.run_number,
        service_name=_known_service(descriptor) or repository_service(repository),
        environment=_environment(descriptor) if category is CicdCategory.DEPLOYMENT else None,
        version=version,
        version_source="tag" if version else None,
        html_url=github_url(run.html_url),
        started_at=started,
        completed_at=completed,
        deployment_key=f"github:{repository}:run:{run.id}:{run.run_attempt or 1}",
        meta={"action": clip(p.action, 32), "run_attempt": run.run_attempt},
    )


def _deployment_status(
    delivery_id: str,
    p: DeploymentStatusPayload,
    received_at: datetime,
    repository_service: RepositoryService,
) -> NormalizedEvent:
    d, ds = p.deployment, p.deployment_status
    repository = p.repository.full_name
    status, conclusion = _DEPLOYMENT_STATE.get(ds.state.lower(), (CicdStatus.QUEUED, None))
    metadata = d.payload if isinstance(d.payload, dict) else {}
    explicit_version = metadata.get("version")
    explicit_service = metadata.get("service")
    version, version_source = None, None
    if isinstance(explicit_version, str) and _EXPLICIT_VERSION.fullmatch(explicit_version):
        version, version_source = explicit_version, "deployment_payload"
    elif tag_version(d.ref):
        version, version_source = d.ref, "tag"
    occurred = ds.updated_at or ds.created_at or received_at
    environment = clip((ds.environment or d.environment or "").lower(), 64)
    return NormalizedEvent(
        delivery_id=delivery_id,
        event_type="deployment_status",
        category=CicdCategory.DEPLOYMENT,
        repository=repository,
        status=status,
        conclusion=conclusion,
        occurred_at=occurred,
        branch=None if tag_version(d.ref) else clip(d.ref, 255),
        commit_sha=commit_sha(d.sha),
        actor=clip(
            (d.creator.login if d.creator else None) or (p.sender.login if p.sender else None), 100
        ),
        service_name=(
            explicit_service
            if isinstance(explicit_service, str) and explicit_service in service_names()
            else repository_service(repository)
        ),
        environment="production" if environment in PRODUCTION_ENVIRONMENTS else environment,
        version=version,
        version_source=version_source,
        html_url=github_url(ds.target_url),
        started_at=d.created_at,
        completed_at=occurred if status is CicdStatus.COMPLETED else None,
        deployment_key=f"github:{repository}:deployment:{d.id}",
        meta={"deployment_id": d.id, "state": clip(ds.state, 32)},
    )
