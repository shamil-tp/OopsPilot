"""Backend remediation policy: which actions exist, their risk, whether they need approval, and
validation of targets and rollback versions against real deployment data.

The model only *proposes* an action. Risk and the approval requirement are never taken from model
output: they come from this table, and the approval requirement is derived from the tool
permission registry (REQUIRES_HUMAN_APPROVAL), so there is one source of truth. The same
validation is meant to run again right before execution (next phase).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deployment
from app.models.enums import ActionType, DeploymentStatus, RiskLevel
from app.services import telemetry
from app.services.service_catalog import get_service
from app.tools import ToolPermission, registry

MAX_ROLLBACK_CANDIDATES = 3
_DEPLOYMENT_HISTORY = 20


class PolicyViolationError(Exception):
    """A proposal the backend refuses. Messages are safe to return to clients."""


@dataclass(frozen=True)
class ActionPolicy:
    risk: RiskLevel
    tool: str | None  # the approval-gated tool that would execute it; None = nothing to execute
    needs_evidence: bool

    @property
    def requires_approval(self) -> bool:
        spec = registry.get(self.tool) if self.tool else None
        return spec is not None and spec.permission is ToolPermission.REQUIRES_HUMAN_APPROVAL


POLICY: dict[ActionType, ActionPolicy] = {
    # Reversible, so MEDIUM (CLAUDE.md §7), but it changes production: human approval.
    ActionType.ROLLBACK_DEPLOYMENT: ActionPolicy(RiskLevel.MEDIUM, "rollback_deployment", True),
    ActionType.RESTART_SERVICE: ActionPolicy(RiskLevel.MEDIUM, "restart_service", True),
    # Changes nothing; still has to be justified by evidence.
    ActionType.NO_ACTION: ActionPolicy(RiskLevel.LOW, None, True),
    # Hands the incident to a human; nothing is executed.
    ActionType.ESCALATE_TO_HUMAN: ActionPolicy(RiskLevel.LOW, None, False),
}


@dataclass(frozen=True)
class DeploymentState:
    active: Deployment | None
    rollback_candidates: list[Deployment]


async def deployment_state(db: AsyncSession, service: str) -> DeploymentState:
    """The active deployment (newest SUCCEEDED) and older SUCCEEDED versions to roll back to."""
    history = await telemetry.list_deployments(db, service, limit=_DEPLOYMENT_HISTORY)
    succeeded = [d for d in history if d.status is DeploymentStatus.SUCCEEDED]
    if not succeeded:
        return DeploymentState(None, [])
    active = succeeded[0]
    candidates = [
        d for d in succeeded[1:] if d.version != active.version and d.timestamp < active.timestamp
    ]
    return DeploymentState(active, candidates[:MAX_ROLLBACK_CANDIDATES])


def allowed_targets(incident_service: str) -> set[str]:
    """The affected service and its known dependencies; never an arbitrary name."""
    service = get_service(incident_service)
    return {incident_service, *(service.dependencies if service else ())}


async def validate_rollback(
    db: AsyncSession, *, incident_service: str, service: str, version: str | None
) -> tuple[str, str]:
    """Returns (from_version, to_version) or raises PolicyViolationError."""
    if service != incident_service:
        raise PolicyViolationError(
            f"rollback target '{service}' is not the affected service '{incident_service}'"
        )
    state = await deployment_state(db, service)
    if state.active is None:
        raise PolicyViolationError(f"{service} has no successful deployment to roll back from")
    if not version:
        raise PolicyViolationError("rollback proposal did not name a version")
    if version == state.active.version:
        raise PolicyViolationError(f"{version} is already the active version of {service}")
    if version not in {d.version for d in state.rollback_candidates}:
        raise PolicyViolationError(
            f"{version} is not a previous successful deployment of {service}"
        )
    return state.active.version, version


def validate_restart(*, incident_service: str, service: str) -> None:
    if service not in allowed_targets(incident_service):
        raise PolicyViolationError(
            f"restart target '{service}' is not {incident_service} or one of its dependencies"
        )
