"""Remediation Agent: proposes one supported action from the stored RCA. It never executes it.

Flow for one incident (after a completed root cause analysis):

    start_remediation   incident must be ANALYZING; row lock + duplicate check;
                        agent_run RUNNING, agent_started
    run_remediation     load the RCA and the evidence it cites + real deployment options ->
                        remediation_analysis_started -> 1 AIProvider call ->
                        backend policy: action, target, version, citations, risk, approval ->
                        (one transaction) approval PENDING + incident AWAITING_APPROVAL,
                        or incident ESCALATED, or no change for NO_ACTION;
                        agent_run COMPLETED, remediation_recommended (+ approval_required)

Execution (rollback/restart) is not part of this agent: approved actions are executed by the
backend in the next phase, after re-validating the approval's stored parameters. On failure the
run is FAILED, an `error` event is stored and the incident stays ANALYZING for a retry.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.common import (
    AgentConflictError,
    IncidentNotFoundError,
    claim_incident,
    complete_run,
    failure_for,
    keep_known,
    latest_run,
    lock_incident,
    now,
    record_failure,
    reload,
)
from app.ai.base import AIProvider, GenerationOptions
from app.ai.factory import get_ai_provider
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import AgentRun, Approval, Incident
from app.models.enums import (
    ActionType,
    AgentName,
    AgentRunStatus,
    ApprovalStatus,
    IncidentStatus,
)
from app.prompts.remediation import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.investigation import EvidenceItem, InvestigationResult
from app.schemas.remediation import (
    ApprovalRead,
    RemediationProposal,
    RemediationResult,
    RemediationRunRead,
)
from app.schemas.root_cause import RootCauseResult
from app.services.agent_events import EventRecorder
from app.services.remediation_policy import (
    POLICY,
    PolicyViolationError,
    deployment_state,
    validate_restart,
    validate_rollback,
)
from app.services.service_catalog import get_service

logger = get_logger(__name__)

AGENT = AgentName.REMEDIATION
MAX_REASON_CHARS = 600
GENERATION = GenerationOptions(
    system_instruction=SYSTEM_INSTRUCTION, temperature=0.0, max_output_tokens=512
)


async def _completed(db: AsyncSession, incident_id: int, agent: AgentName) -> AgentRun | None:
    run = await latest_run(db, incident_id, agent)
    if run is None or run.status is not AgentRunStatus.COMPLETED or not run.output:
        return None
    return run


async def start_remediation(db: AsyncSession, incident_id: int) -> tuple[AgentRun, bool]:
    """Create a RUNNING remediation run. Returns `(run, created)`.

    A completed proposal is returned as is (`created=False`, no AI call, no second approval).
    """
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"Incident {incident_id} not found")
    ref = incident.reference

    previous = await latest_run(db, incident_id, AGENT)
    if previous is not None and previous.status is AgentRunStatus.COMPLETED:
        return previous, False
    if previous is not None and previous.status is AgentRunStatus.RUNNING:
        raise AgentConflictError(f"A remediation proposal for {ref} is already in progress")
    if await _completed(db, incident_id, AgentName.ROOT_CAUSE) is None:
        raise AgentConflictError(
            f"{ref} has no completed root cause analysis; run POST /api/incidents/{incident_id}"
            "/analyze first"
        )
    if incident.status is not IncidentStatus.ANALYZING:
        raise AgentConflictError(f"{ref} is {incident.status}; remediation requires ANALYZING")

    # Serialize concurrent requests on the incident row, then re-check for a run another
    # request may have committed in the meantime.
    if not await lock_incident(db, incident_id, status=IncidentStatus.ANALYZING):
        await db.rollback()
        raise AgentConflictError(f"{ref} is no longer ANALYZING")
    current = await latest_run(db, incident_id, AGENT)
    if current is not None and current.status in (
        AgentRunStatus.RUNNING,
        AgentRunStatus.COMPLETED,
    ):
        await db.rollback()
        raise AgentConflictError(f"A remediation proposal for {ref} has already started")

    run = AgentRun(
        incident_id=incident_id, agent_name=AGENT, status=AgentRunStatus.RUNNING, started_at=now()
    )
    db.add(run)
    EventRecorder(db, incident_id, AGENT).emit(
        "agent_started", f"Remediation agent started for {ref}", service=incident.service_name
    )
    await db.commit()
    logger.info(
        "agent_started", extra={"agent": AGENT, "incident_id": incident_id, "run_id": run.id}
    )
    return run, True


def _evidence_for(rca: RootCauseResult, investigation: InvestigationResult) -> list[EvidenceItem]:
    """Only what the RCA relied on, plus the deployment records; in time order."""
    cited = {e.id for e in rca.supporting_evidence}
    for group in (rca.causal_chain, rca.contributing_factors, rca.alternative_explanations):
        for item in group:
            cited.update(item.evidence_ids)
    items = [e for e in investigation.evidence if e.id in cited or e.source == "deployments"]
    dated = sorted((e for e in items if e.timestamp is not None), key=lambda e: e.timestamp)
    return dated + [e for e in items if e.timestamp is None]


async def run_remediation(
    run_id: int,
    *,
    provider: AIProvider | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> None:
    """Execute a RUNNING remediation proposal. Uses its own session to outlive the request."""
    async with session_factory() as db:
        run = await db.get(AgentRun, run_id)
        assert run is not None and run.status is AgentRunStatus.RUNNING
        incident_id = run.incident_id
        loaded = await db.get(Incident, incident_id)
        assert loaded is not None
        service, ref = loaded.service_name, loaded.reference
        events = EventRecorder(db, incident_id, AGENT)
        try:
            rca_run = await _completed(db, incident_id, AgentName.ROOT_CAUSE)
            if rca_run is None or rca_run.output is None:
                raise PolicyViolationError("the completed root cause analysis is not available")
            rca = RootCauseResult.model_validate(rca_run.output)
            rca_run_id = rca_run.id
            inv_run = await reload(db, AgentRun, rca.investigation_run_id)
            investigation = InvestigationResult.model_validate(inv_run.output)
            evidence = _evidence_for(rca, investigation)
            catalog = get_service(service)
            dependencies = list(catalog.dependencies) if catalog else []
            state = await deployment_state(db, service)
            events.emit(
                "remediation_analysis_started",
                f"Choosing a remediation for: {rca.root_cause}",
                root_cause_run_id=rca_run_id,
                evidence_items=len(evidence),
                active_version=state.active.version if state.active else None,
                rollback_candidates=[d.version for d in state.rollback_candidates],
            )
            await db.commit()

            ai = provider or get_ai_provider()
            proposal = await ai.generate_structured(
                build_prompt(rca, evidence, dependencies, state.active, state.rollback_candidates),
                RemediationProposal,
                options=GENERATION,
            )
            result = await _validate(
                db,
                proposal=proposal,
                service=service,
                ref=ref,
                incident_id=incident_id,
                evidence=evidence,
                rca_run_id=rca_run_id,
                provider=ai,
            )
            await _apply(db, run_id=run_id, incident_id=incident_id, result=result, events=events)
            await db.commit()
            logger.info(
                "remediation_recommended",
                extra={
                    "agent": AGENT,
                    "incident_id": incident_id,
                    "run_id": run_id,
                    "action": result.action,
                    "requires_approval": result.requires_approval,
                },
            )
        except Exception as exc:  # recorded and re-raised as a safe AgentFailedError
            failure = failure_for(
                exc, step="remediation proposal", restored=IncidentStatus.ANALYZING
            )
            logger.error(
                "remediation_failed",
                extra={
                    "agent": AGENT,
                    "incident_id": incident_id,
                    "run_id": run_id,
                    "error": type(exc).__name__,
                },
            )
            await record_failure(
                db,
                agent=AGENT,
                run_id=run_id,
                incident_id=incident_id,
                message=failure.message,
                claimed=IncidentStatus.ANALYZING,
                restore=IncidentStatus.ANALYZING,
            )
            raise failure from None


async def _validate(
    db: AsyncSession,
    *,
    proposal: RemediationProposal,
    service: str,
    ref: str,
    incident_id: int,
    evidence: list[EvidenceItem],
    rca_run_id: int,
    provider: AIProvider,
) -> RemediationResult:
    """Backend policy. Nothing from the model is trusted without checking it here."""
    policy = POLICY[proposal.action]
    by_id = {e.id: e for e in evidence}
    cited = keep_known(proposal.supporting_evidence, set(by_id))
    if policy.needs_evidence and not cited:
        raise PolicyViolationError(
            f"{proposal.action} proposal did not cite any of the supplied evidence"
        )

    target, parameters = service, {}
    if proposal.action is ActionType.ROLLBACK_DEPLOYMENT:
        from_version, to_version = await validate_rollback(
            db,
            incident_service=service,
            service=proposal.target_service,
            version=proposal.rollback_to_version,
        )
        # As in CLAUDE.md, the approval target is the version being rolled back.
        target = from_version
        parameters = {"service": service, "from_version": from_version, "to_version": to_version}
    elif proposal.action is ActionType.RESTART_SERVICE:
        validate_restart(incident_service=service, service=proposal.target_service)
        target = proposal.target_service
        parameters = {"service": proposal.target_service}

    if policy.requires_approval:
        status = "approval_required"
    elif proposal.action is ActionType.ESCALATE_TO_HUMAN:
        status = "escalated"
    else:
        status = "no_action"
    return RemediationResult(
        incident_id=incident_id,
        incident_reference=ref,
        service=service,
        status=status,
        action=proposal.action,
        target=target,
        parameters=parameters,
        reason=proposal.reason.strip()[:MAX_REASON_CHARS],
        risk=policy.risk,
        requires_approval=policy.requires_approval,
        approval_id=None,
        supporting_evidence=[by_id[ref_id] for ref_id in cited],
        confidence=proposal.confidence,
        root_cause_run_id=rca_run_id,
        model=f"{provider.name}:{getattr(provider, 'model', 'unknown')}",
    )


async def _apply(
    db: AsyncSession,
    *,
    run_id: int,
    incident_id: int,
    result: RemediationResult,
    events: EventRecorder,
) -> None:
    """Approval + incident state + run output + events, committed together by the caller."""
    if result.requires_approval:
        approval = Approval(
            incident_id=incident_id,
            action_type=result.action,
            target=result.target,
            risk=result.risk,
            reason=result.reason,
            parameters={**result.parameters, "remediation_run_id": run_id},
            status=ApprovalStatus.PENDING,
            requested_at=now(),
        )
        db.add(approval)
        await db.flush()
        result.approval_id = approval.id
        new_state: IncidentStatus | None = IncidentStatus.AWAITING_APPROVAL
    elif result.action is ActionType.ESCALATE_TO_HUMAN:
        new_state = IncidentStatus.ESCALATED
    else:
        new_state = None  # NO_ACTION: the incident stays ANALYZING for a human to decide

    if new_state is not None and not await claim_incident(
        db, incident_id, expected=IncidentStatus.ANALYZING, new=new_state
    ):
        raise PolicyViolationError("the incident left ANALYZING while the proposal was prepared")

    summary = _describe(result)
    await complete_run(db, run_id, summary=summary, output=result.model_dump(mode="json"))
    events.emit(
        "remediation_recommended",
        f"Recommended: {summary}",
        action=result.action,
        target=result.target,
        parameters=result.parameters,
        risk=result.risk,
        requires_approval=result.requires_approval,
        supporting_evidence=[e.id for e in result.supporting_evidence],
        confidence=result.confidence,
    )
    if result.requires_approval:
        events.emit(
            "approval_required",
            f"Human approval required ({result.risk} risk): {summary}",
            approval_id=result.approval_id,
            action=result.action,
            parameters=result.parameters,
        )
    elif result.action is ActionType.ESCALATE_TO_HUMAN:
        events.emit("incident_escalated", f"Escalated to a human: {result.reason}")


def _describe(result: RemediationResult) -> str:
    p = result.parameters
    if result.action is ActionType.ROLLBACK_DEPLOYMENT:
        return f"roll back {p['service']} from {p['from_version']} to {p['to_version']}"
    if result.action is ActionType.RESTART_SERVICE:
        return f"restart {p['service']}"
    if result.action is ActionType.ESCALATE_TO_HUMAN:
        return f"escalate {result.incident_reference} to a human"
    return f"no action for {result.incident_reference}"


async def run_view(db: AsyncSession, run: AgentRun) -> RemediationRunRead:
    fresh = await reload(db, AgentRun, run.id)
    incident = await reload(db, Incident, run.incident_id)
    assert fresh is not None and incident is not None
    completed = fresh.status is AgentRunStatus.COMPLETED and fresh.output
    result = RemediationResult.model_validate(fresh.output) if completed else None
    approval = None
    if result is not None and result.approval_id is not None:
        row = await reload(db, Approval, result.approval_id)
        approval = ApprovalRead.model_validate(row) if row else None
    return RemediationRunRead(
        run_id=fresh.id,
        incident_id=incident.id,
        incident_reference=incident.reference,
        incident_status=incident.status,
        agent=fresh.agent_name,
        status=fresh.status,
        started_at=fresh.started_at,
        completed_at=fresh.completed_at,
        summary=fresh.summary,
        result=result,
        approval=approval,
    )
