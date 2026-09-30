"""The parts of GitHub webhook payloads OpsPilot reads. Everything else is ignored (never stored).

Every field is untrusted input: it is validated here, truncated during normalization, and only
ever stored or displayed as data.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UtcDatetime


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Repository(_Payload):
    # GitHub's "owner/name" character set, so a repository name can never carry markup/controls.
    full_name: str = Field(max_length=140, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    # Push events only: when the push happened (GitHub sends epoch seconds here).
    pushed_at: UtcDatetime | None = None


class User(_Payload):
    login: str | None = None


class CommitAuthor(_Payload):
    name: str | None = None
    username: str | None = None


class Commit(_Payload):
    id: str | None = None
    message: str | None = None
    timestamp: UtcDatetime | None = None
    author: CommitAuthor | None = None
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)


class PushPayload(_Payload):
    ref: str
    before: str | None = None
    after: str | None = None
    deleted: bool = False
    repository: Repository
    head_commit: Commit | None = None
    commits: list[Commit] = Field(default_factory=list)
    sender: User | None = None


class WorkflowRun(_Payload):
    id: int
    name: str | None = None
    path: str | None = None
    run_number: int | None = None
    run_attempt: int | None = None
    head_branch: str | None = None
    head_sha: str | None = None
    status: str | None = None
    conclusion: str | None = None
    created_at: UtcDatetime | None = None
    run_started_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    html_url: str | None = None
    display_title: str | None = None
    actor: User | None = None


class WorkflowRunPayload(_Payload):
    action: str | None = None
    workflow_run: WorkflowRun
    repository: Repository
    sender: User | None = None


class Deployment(_Payload):
    id: int
    sha: str | None = None
    ref: str | None = None
    environment: str | None = None
    # Free-form deployment metadata set by whoever created the deployment (object or string).
    payload: dict[str, Any] | str | None = None
    created_at: UtcDatetime | None = None
    creator: User | None = None


class DeploymentStatus(_Payload):
    id: int | None = None
    state: str
    environment: str | None = None
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    target_url: str | None = None


class DeploymentStatusPayload(_Payload):
    deployment_status: DeploymentStatus
    deployment: Deployment
    repository: Repository
    sender: User | None = None
