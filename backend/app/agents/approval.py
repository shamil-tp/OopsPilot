"""Human approval gate and backend execution of the approved remediation (orchestrator role).

CLAUDE.md §18: approving executes the action in the backend; rejecting escalates the incident.
No AI is involved: the action, target and parameters are the ones Phase 7 validated and stored
on the approval record. The client only says approve or reject.

    decide(approve)   approval PENDING -> APPROVED (conditional UPDATE: one decision wins)
                      incident AWAITING_APPROVAL -> REMEDIATING; execution run RUNNING
                      approval_received
    execute           lock the incident (REMEDIATING), re-validate the stored parameters against
                      live deployment data, apply the simulated action, incident -> VERIFYING,
                      run COMPLETED; remediation_started, remediation_completed
                      (all changes in one transaction: all or nothing)
    decide(reject)    approval PENDING -> REJECTED, incident -> ESCALATED; approval_received,
                      incident_escalated. Nothing is executed.

If execution is refused (stale approval, changed deployments) or fails, nothing is changed, the
run is FAILED, an `error` event is stored and the incident moves to FAILED. Verification of the
outcome is the next phase.
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.common import (
    AgentConflictError,
    AgentFailedError,
    IncidentNotFoundError,
    claim_incident,
    complete_run,
    latest_run,
    lock_incident,
    now,
    record_failure,
    reload,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import AgentRun, Approval, Incident, ServiceHealth
from app.models.enums import (
    ActionType,
    AgentName,
    AgentRunStatus,
    ApprovalStatus,
    IncidentStatus,
)
from app.schemas.execution import DecisionRead, ExecutionRead, ExecutionResult, ServiceSnapshot
from app.schemas.remediation import ApprovalRead
from app.services import simulator, telemetry
from app.services.agent_events import EventRecorder
from app.services.remediation_policy import (
    PolicyViolationError,
    deployment_state,
    validate_restart,
    validate_rollback,
)
from app.services.service_catalog import is_demo_service

logger = get_logger(__name__)

AGENT = AgentName.ORCHESTRATOR
EXECUTABLE = {ActionType.ROLLBACK_DEPLOYMENT, ActionType.RESTART_SERVICE}


class ApprovalNotFoundError(IncidentNotFoundError):
    """The incident has no approval request (HTTP 404)."""


async def latest_approval(db: AsyncSession, incident_id: int) -> Approval | None:
    return await db.scalar(
        select(Approval)
        .where(Approval.incident_id == incident_id)
        .order_by(Approval.requested_at.desc(), Approval.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


async def decide(
    db: AsyncSession, incident_id: int, *, approve: bool
) -> tuple[Approval, AgentRun | None, bool]:
    """Record the human decision on the incident's pending approval.

    Returns `(approval, execution_run, changed)`. Repeating the same decision is idempotent
    (`changed=False`, nothing happens); the opposite decision is a conflict.
    """
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"Incident {incident_id} not found")
    ref = incident.reference
    approval = await latest_approval(db, incident_id)
    if approval is None:
        raise ApprovalNotFoundError(f"{ref} has no approval request")
    wanted = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    if approval.status is wanted:
        return approval, await latest_run(db, incident_id, AGENT), False
    if approval.status is not ApprovalStatus.PENDING:
        raise AgentConflictError(f"Approval {approval.id} of {ref} is already {approval.status}")
    if incident.status is not IncidentStatus.AWAITING_APPROVAL:
        raise AgentConflictError(f"{ref} is {incident.status}, not AWAITING_APPROVAL")

    approval_id = approval.id
    decided = await db.execute(
        update(Approval)
        .where(Approval.id == approval_id, Approval.status == ApprovalStatus.PENDING)
        .values(status=wanted, decided_at=now())
    )
    new_state = IncidentStatus.REMEDIATING if approve else IncidentStatus.ESCALATED
    if decided.rowcount != 1 or not await claim_incident(
        db, incident_id, expected=IncidentStatus.AWAITING_APPROVAL, new=new_state
    ):
        await db.rollback()
        raise AgentConflictError(f"Approval {approval_id} of {ref} was decided concurrently")

    events = EventRecorder(db, incident_id, AGENT)
    events.emit(
        "approval_received",
        f"Human {'approved' if approve else 'rejected'}: {approval.action_type} {approval.target}",
        approval_id=approval_id,
        decision=wanted,
        action=approval.action_type,
        target=approval.target,
        parameters=approval.parameters,
        incident_status={"from": IncidentStatus.AWAITING_APPROVAL, "to": new_state},
    )
    run = None
    if approve:
        run = AgentRun(
            incident_id=incident_id,
            agent_name=AGENT,
            status=AgentRunStatus.RUNNING,
            started_at=now(),
            output={"approval_id": approval_id},
        )
        db.add(run)
    else:
        events.emit(
            "incident_escalated",
            f"Remediation rejected by a human; {ref} escalated. Nothing was executed.",
            approval_id=approval_id,
        )
    await db.commit()
    logger.info(
        "approval_received",
        extra={"incident_id": incident_id, "approval_id": approval_id, "decision": wanted},
    )
    return approval, run, True


async def _running_run(db: AsyncSession, run_id: int) -> AgentRun:
    run = await reload(db, AgentRun, run_id)
    if run is None or run.agent_name is not AGENT or run.status is not AgentRunStatus.RUNNING:
        raise AgentConflictError(f"Execution run {run_id} is not pending execution")
    return run


async def _snapshot(db: AsyncSession, service: str) -> ServiceSnapshot:
    state = await deployment_state(db, service)
    health: ServiceHealth | None = await telemetry.latest_health(db, service)
    return ServiceSnapshot(
        active_version=state.active.version if state.active else None,
        status=health.status if health else None,
        error_rate=health.error_rate if health else None,
        latency_ms=health.latency_ms if health else None,
    )


async def execute(
    run_id: int, *, session_factory: async_sessionmaker[AsyncSession] = SessionLocal
) -> None:
    """Execute the approved action of a RUNNING execution run. Deterministic; no AI."""
    async with session_factory() as db:
        run = await _running_run(db, run_id)
        incident_id = run.incident_id
        approval_id = int((run.output or {}).get("approval_id", 0))
        events = EventRecorder(db, incident_id, AGENT)
        try:
            # One executor at a time: whoever holds the lock sees the other's committed result.
            if not await lock_incident(db, incident_id, status=IncidentStatus.REMEDIATING):
                raise AgentConflictError("the incident is no longer REMEDIATING")
            await _running_run(db, run_id)
            result = await _execute_locked(db, incident_id, approval_id, run_id, events)
            await complete_run(
                db, run_id, summary=_describe(result), output=result.model_dump(mode="json")
            )
            events.emit(
                "remediation_completed",
                f"Executed (simulated): {_describe(result)}. Verification pending.",
                approval_id=approval_id,
                execution_run_id=run_id,
                action=result.action,
                parameters=result.parameters,
                before=result.before.model_dump(mode="json"),
                after=result.after.model_dump(mode="json"),
                next_step="verification",
            )
            await db.commit()
            logger.info(
                "remediation_completed",
                extra={"incident_id": incident_id, "run_id": run_id, "action": result.action},
            )
        except AgentConflictError:
            await db.rollback()  # another executor owns (or finished) this run: change nothing
            raise
        except Exception as exc:
            failure = _failure(exc)
            logger.error(
                "remediation_failed",
                extra={"incident_id": incident_id, "run_id": run_id, "error": type(exc).__name__},
            )
            await record_failure(
                db,
                agent=AGENT,
                run_id=run_id,
                incident_id=incident_id,
                message=failure.message,
                claimed=IncidentStatus.REMEDIATING,
                restore=IncidentStatus.FAILED,
            )
            raise failure from None


async def _execute_locked(
    db: AsyncSession, incident_id: int, approval_id: int, run_id: int, events: EventRecorder
) -> ExecutionResult:
    approval = await reload(db, Approval, approval_id)
    incident = await reload(db, Incident, incident_id)
    if approval is None or approval.incident_id != incident_id:
        raise PolicyViolationError("the approval does not belong to this incident")
    if approval.status is not ApprovalStatus.APPROVED:
        raise PolicyViolationError(f"approval {approval_id} is {approval.status}, not APPROVED")
    if approval.action_type not in EXECUTABLE:
        raise PolicyViolationError(f"{approval.action_type} is not an executable action")

    params = dict(approval.parameters)
    service = str(params.get("service", ""))
    at = datetime.now(UTC).replace(microsecond=0)
    before = await _snapshot(db, service or incident.service_name)
    events.emit(
        "remediation_started",
        f"Executing approved {approval.action_type} (simulated)",
        approval_id=approval_id,
        execution_run_id=run_id,
        action=approval.action_type,
        parameters=params,
    )

    # OpsPilot can only execute inside the simulation. For a real application an approved action
    # is carried out by an operator; nothing here may pretend it happened.
    if not is_demo_service(service or incident.service_name):
        raise PolicyViolationError(
            f"{service or incident.service_name} is a real service with no execution integration; "
            "an operator must perform the approved action"
        )

    # Re-validate the stored, approved parameters against the live deployment data.
    if approval.action_type is ActionType.ROLLBACK_DEPLOYMENT:
        active, to_version = await validate_rollback(
            db,
            incident_service=incident.service_name,
            service=service,
            version=params.get("to_version"),
        )
        if active != params.get("from_version"):
            raise PolicyViolationError(
                f"active deployment of {service} is {active}, not the approved "
                f"{params.get('from_version')}; the approval is stale"
            )
        await simulator.apply_rollback(
            db, service=service, from_version=active, to_version=to_version, at=at
        )
    else:
        validate_restart(incident_service=incident.service_name, service=service)
        await simulator.apply_restart(db, service=service, at=at)

    if not await claim_incident(
        db, incident_id, expected=IncidentStatus.REMEDIATING, new=IncidentStatus.VERIFYING
    ):
        raise PolicyViolationError("the incident left REMEDIATING during execution")
    remediation_run_id = params.pop("remediation_run_id", None)
    return ExecutionResult(
        incident_id=incident_id,
        approval_id=approval_id,
        remediation_run_id=remediation_run_id,
        action=approval.action_type,
        target=approval.target,
        parameters=params,
        service=service,
        before=before,
        after=await _snapshot(db, service),
        executed_at=at,
    )


def _describe(result: ExecutionResult) -> str:
    p = result.parameters
    if result.action is ActionType.ROLLBACK_DEPLOYMENT:
        return f"rolled back {p['service']} from {p['from_version']} to {p['to_version']}"
    return f"restarted {p['service']}"


def _failure(exc: Exception) -> AgentFailedError:
    tail = " Nothing was changed; the incident is FAILED and needs a human."
    if isinstance(exc, PolicyViolationError):
        return AgentFailedError(f"Execution refused: {exc}.{tail}", 409)
    if isinstance(exc, SQLAlchemyError | OSError):
        return AgentFailedError(f"Database unavailable during execution.{tail}", 503)
    return AgentFailedError(f"Execution failed unexpectedly.{tail}", 500)


async def execution_view(db: AsyncSession, run: AgentRun | None) -> ExecutionRead | None:
    if run is None:
        return None
    fresh = await reload(db, AgentRun, run.id)
    completed = fresh.status is AgentRunStatus.COMPLETED and fresh.output
    result = ExecutionResult.model_validate(fresh.output) if completed else None
    output = fresh.output or {}
    operator = (result.performed_by == "operator") if result else bool(output.get("operator"))
    return ExecutionRead(
        run_id=fresh.id,
        status=fresh.status,
        started_at=fresh.started_at,
        completed_at=fresh.completed_at,
        summary=fresh.summary,
        result=result,
        mode="operator" if operator else "simulated",
        instructions=None if result else output.get("instructions"),
    )


# --- real applications: the approved action is performed by an operator --------------------------


def operator_instructions(approval: Approval) -> str:
    """Exactly what the approved action means for a real application (no automation exists)."""
    p = approval.parameters
    service = p.get("service", approval.target)
    if approval.action_type is ActionType.ROLLBACK_DEPLOYMENT:
        return (
            f"Roll back {service} from {p.get('from_version')} to {p.get('to_version')}: in your "
            f"hosting platform (e.g. Vercel → Deployments), promote or redeploy the production "
            f"deployment of commit {p.get('to_version')}. Then click Mark as done."
        )
    return (
        f"Restart {service}: redeploy its current production deployment in your hosting "
        "platform. Then click Mark as done."
    )


async def request_operator(
    run_id: int, *, session_factory: async_sessionmaker[AsyncSession] = SessionLocal
) -> None:
    """Approved action on a real service: record the state before it and what the operator must
    do. OpsPilot changes nothing; the incident stays REMEDIATING until the operator confirms."""
    async with session_factory() as db:
        run = await _running_run(db, run_id)
        incident = await reload(db, Incident, run.incident_id)
        approval_id = int((run.output or {}).get("approval_id", 0))
        approval = await reload(db, Approval, approval_id)
        if incident is None or approval is None:
            raise AgentConflictError("the approved remediation no longer exists")
        service = str(approval.parameters.get("service") or incident.service_name)
        before = await _snapshot(db, service)
        instructions = operator_instructions(approval)
        run.output = {
            "approval_id": approval_id,
            "operator": True,
            "service": service,
            "before": before.model_dump(mode="json"),
            "instructions": instructions,
        }
        run.summary = "Waiting for an operator to perform the approved action"
        EventRecorder(db, incident.id, AGENT).emit(
            "operator_action_required",
            f"Operator action required: {instructions}",
            approval_id=approval_id,
            execution_run_id=run_id,
            action=approval.action_type,
            parameters=approval.parameters,
        )
        await db.commit()
        logger.info(
            "operator_action_required", extra={"incident_id": incident.id, "run_id": run_id}
        )


async def confirm_operator(db: AsyncSession, incident_id: int) -> AgentRun:
    """The operator reports the approved action as done. Idempotent; no AI; changes only
    OpsPilot's records (REMEDIATING -> VERIFYING). Verification then measures the result."""
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"Incident {incident_id} not found")
    ref = incident.reference
    if is_demo_service(incident.service_name):
        raise AgentConflictError(f"{ref} is simulated; OpsPilot executes its remediation itself")
    run = await latest_run(db, incident_id, AGENT)
    if run is not None and run.status is AgentRunStatus.COMPLETED:
        return run
    output = (run.output if run else None) or {}
    if run is None or run.status is not AgentRunStatus.RUNNING or not output.get("operator"):
        raise AgentConflictError(f"{ref} has no approved remediation waiting for an operator")
    if not await lock_incident(db, incident_id, status=IncidentStatus.REMEDIATING):
        raise AgentConflictError(f"{ref} is no longer REMEDIATING")
    approval = await reload(db, Approval, int(output["approval_id"]))
    assert approval is not None
    params = dict(approval.parameters)
    remediation_run_id = params.pop("remediation_run_id", None)
    service = str(output.get("service") or incident.service_name)
    at = now().replace(microsecond=0)
    result = ExecutionResult(
        incident_id=incident_id,
        approval_id=approval.id,
        remediation_run_id=remediation_run_id,
        action=approval.action_type,
        target=approval.target,
        parameters=params,
        simulated=False,
        performed_by="operator",
        service=service,
        before=ServiceSnapshot.model_validate(output["before"]),
        after=await _snapshot(db, service),
        executed_at=at,
    )
    if not await claim_incident(
        db, incident_id, expected=IncidentStatus.REMEDIATING, new=IncidentStatus.VERIFYING
    ):
        await db.rollback()
        raise AgentConflictError(f"{ref} left REMEDIATING")
    await complete_run(
        db, run.id, summary=f"Operator: {_describe(result)}", output=result.model_dump(mode="json")
    )
    needed = get_settings().verify_min_checks
    EventRecorder(db, incident_id, AGENT).emit(
        "remediation_completed",
        f"Operator confirmed: {_describe(result)}. Verification runs after {needed} health "
        "check(s) recorded from now.",
        approval_id=approval.id,
        execution_run_id=run.id,
        action=result.action,
        parameters=result.parameters,
        performed_by="operator",
        next_step="verification",
    )
    await db.commit()
    logger.info("operator_confirmed", extra={"incident_id": incident_id, "run_id": run.id})
    return run


async def decision_view(db: AsyncSession, incident_id: int, approval_id: int) -> DecisionRead:
    incident = await reload(db, Incident, incident_id)
    approval = await reload(db, Approval, approval_id)
    run = await latest_run(db, incident_id, AGENT)
    return DecisionRead(
        incident_id=incident.id,
        incident_reference=incident.reference,
        incident_status=incident.status,
        approval=ApprovalRead.model_validate(approval),
        execution=await execution_view(db, run),
    )
