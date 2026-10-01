"""Verification Agent: objective recovery checks by the backend + a short AI explanation.

Flow for one incident (after an approved remediation was executed):

    start_verification  incident must be VERIFYING with a COMPLETED execution of an APPROVED
                        approval; row lock + duplicate check; run RUNNING, verification_started
    run_verification    read current telemetry (active deployment, latest health) ->
                        6 deterministic checks (recovery_check events) -> recovered = all passed ->
                        1 AIProvider call to explain the outcome (it cannot change it) ->
                        citation guardrails -> incident RESOLVED (recovered) or FAILED (not),
                        run COMPLETED, verification_completed (+ incident_resolved)

Read-only with respect to remediation: nothing is re-executed, rolled back or restarted. If the
check or the explanation fails (AI, database), the run is FAILED, an `error` event is stored and
the incident stays VERIFYING so verification can be retried.
"""

from datetime import datetime

from sqlalchemy import select
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
from app.agents.evidence import relative
from app.ai.base import AIProvider, GenerationOptions
from app.ai.errors import AIProviderError
from app.ai.factory import get_ai_provider
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
    ServiceStatus,
)
from app.prompts.verification import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.common import as_utc
from app.schemas.execution import ExecutionResult, ServiceSnapshot
from app.schemas.investigation import EvidenceItem
from app.schemas.verification import (
    RecoveryCheck,
    VerificationExplanation,
    VerificationResult,
    VerificationRunRead,
)
from app.services import telemetry
from app.services.agent_events import EventRecorder
from app.services.remediation_policy import deployment_state
from app.services.scenario import ERROR_RATE_THRESHOLD, LATENCY_SLO_MS
from app.services.service_catalog import is_demo_service

logger = get_logger(__name__)

AGENT = AgentName.VERIFICATION
MAX_SUMMARY_CHARS = 600
GENERATION = GenerationOptions(
    system_instruction=SYSTEM_INSTRUCTION, temperature=0.0, max_output_tokens=512
)


async def _completed_execution(db: AsyncSession, incident_id: int) -> AgentRun | None:
    run = await latest_run(db, incident_id, AgentName.ORCHESTRATOR)
    if run is None or run.status is not AgentRunStatus.COMPLETED or not run.output:
        return None
    return run


async def start_verification(db: AsyncSession, incident_id: int) -> tuple[AgentRun, bool]:
    """Create a RUNNING verification run. Returns `(run, created)`.

    A completed verification is returned as is (`created=False`, no AI call).
    """
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"Incident {incident_id} not found")
    ref = incident.reference

    previous = await latest_run(db, incident_id, AGENT)
    if previous is not None and previous.status is AgentRunStatus.COMPLETED:
        return previous, False
    if previous is not None and previous.status is AgentRunStatus.RUNNING:
        raise AgentConflictError(f"A verification of {ref} is already in progress")
    execution = await _completed_execution(db, incident_id)
    if execution is None:
        raise AgentConflictError(f"{ref} has no completed remediation execution to verify")
    approval = await reload(db, Approval, int(execution.output["approval_id"]))
    if approval is None or approval.status is not ApprovalStatus.APPROVED:
        raise AgentConflictError(f"{ref} has no approved remediation to verify")
    if incident.status is not IncidentStatus.VERIFYING:
        raise AgentConflictError(f"{ref} is {incident.status}; verification requires VERIFYING")
    if not is_demo_service(incident.service_name):
        # A real service is judged only on health checks recorded after the operator's action.
        needed = get_settings().verify_min_checks
        executed_at = ExecutionResult.model_validate(execution.output).executed_at
        have = len(await checks_since(db, incident.service_name, executed_at))
        if have < needed:
            raise AgentConflictError(
                f"Waiting for health checks of {incident.service_name} after the remediation "
                f"({have} of {needed}); verification starts automatically when they are in"
            )

    # Serialize concurrent requests on the incident row, then re-check for a run another
    # request may have committed in the meantime.
    if not await lock_incident(db, incident_id, status=IncidentStatus.VERIFYING):
        await db.rollback()
        raise AgentConflictError(f"{ref} is no longer VERIFYING")
    current = await latest_run(db, incident_id, AGENT)
    if current is not None and current.status in (
        AgentRunStatus.RUNNING,
        AgentRunStatus.COMPLETED,
    ):
        await db.rollback()
        raise AgentConflictError(f"A verification of {ref} has already started")

    run = AgentRun(
        incident_id=incident_id, agent_name=AGENT, status=AgentRunStatus.RUNNING, started_at=now()
    )
    db.add(run)
    EventRecorder(db, incident_id, AGENT).emit(
        "verification_started",
        f"Verifying recovery of {incident.service_name} after the approved remediation",
        execution_run_id=execution.id,
        approval_id=approval.id,
    )
    await db.commit()
    logger.info(
        "agent_started", extra={"agent": AGENT, "incident_id": incident_id, "run_id": run.id}
    )
    return run, True


async def run_verification(
    run_id: int,
    *,
    provider: AIProvider | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> None:
    """Execute a RUNNING verification. Uses its own session so it can outlive the request."""
    async with session_factory() as db:
        run = await db.get(AgentRun, run_id)
        assert run is not None and run.status is AgentRunStatus.RUNNING
        incident_id = run.incident_id
        loaded = await db.get(Incident, incident_id)
        assert loaded is not None
        service, ref = loaded.service_name, loaded.reference
        events = EventRecorder(db, incident_id, AGENT)
        try:
            execution_run = await _completed_execution(db, incident_id)
            if execution_run is None:
                raise AgentConflictError("the remediation execution is no longer available")
            execution = ExecutionResult.model_validate(execution_run.output)
            execution_run_id = execution_run.id
            approval = await reload(db, Approval, execution.approval_id)

            checks, evidence, after = await _assess(db, service, execution, approval)
            recovered = all(c.passed for c in checks)
            for check in checks:
                events.emit(
                    "recovery_check",
                    f"{check.name}: {'PASS' if check.passed else 'FAIL'} "
                    f"(expected {check.expected}; actual {check.actual})",
                    check=check.name,
                    passed=check.passed,
                    expected=check.expected,
                    actual=check.actual,
                    evidence_ids=check.evidence_ids,
                    execution_run_id=execution_run_id,
                )
            await db.commit()

            ai = provider or get_ai_provider()
            explanation = await ai.generate_structured(
                build_prompt(
                    reference=ref,
                    service=service,
                    recovered=recovered,
                    checks=checks,
                    evidence=evidence,
                ),
                VerificationExplanation,
                options=GENERATION,
            )
            by_id = {e.id: e for e in evidence}
            cited = keep_known(explanation.supporting_evidence, set(by_id))
            if not cited or not explanation.reasoning_summary.strip():
                raise AIProviderError("the verification summary did not cite any supplied evidence")

            passed = sum(c.passed for c in checks)
            result = VerificationResult(
                incident_id=incident_id,
                incident_reference=ref,
                service=service,
                recovered=recovered,
                confidence=round(passed / len(checks), 2),
                before=execution.before,
                after=after,
                checks=checks,
                failed_checks=[c.name for c in checks if not c.passed],
                supporting_evidence=[by_id[i] for i in cited],
                reasoning_summary=explanation.reasoning_summary.strip()[:MAX_SUMMARY_CHARS],
                next_step="incident_report" if recovered else "human_investigation",
                execution_run_id=execution_run_id,
                approval_id=execution.approval_id,
                model=f"{ai.name}:{getattr(ai, 'model', 'unknown')}",
            )
            new_state = IncidentStatus.RESOLVED if recovered else IncidentStatus.FAILED
            if not await claim_incident(
                db, incident_id, expected=IncidentStatus.VERIFYING, new=new_state
            ):
                raise AgentConflictError("the incident left VERIFYING during verification")
            summary = (
                f"{service} recovered: {passed}/{len(checks)} checks passed"
                if recovered
                else f"{service} NOT recovered: failed {', '.join(result.failed_checks)}"
            )
            await complete_run(db, run_id, summary=summary, output=result.model_dump(mode="json"))
            events.emit(
                "verification_completed",
                f"Verification complete: {summary}",
                recovered=recovered,
                confidence=result.confidence,
                failed_checks=result.failed_checks,
                before=result.before.model_dump(mode="json"),
                after=result.after.model_dump(mode="json"),
                incident_status={"from": IncidentStatus.VERIFYING, "to": new_state},
                execution_run_id=execution_run_id,
                unsupported_citations_removed=len(explanation.supporting_evidence) - len(cited),
            )
            if recovered:
                events.emit(
                    "incident_resolved",
                    f"{ref} resolved: {service} is healthy after the approved remediation",
                    execution_run_id=execution_run_id,
                )
            await db.commit()
            logger.info(
                "verification_completed",
                extra={
                    "agent": AGENT,
                    "incident_id": incident_id,
                    "run_id": run_id,
                    "recovered": recovered,
                },
            )
        except Exception as exc:  # recorded and re-raised as a safe AgentFailedError
            failure = failure_for(exc, step="verification", restored=IncidentStatus.VERIFYING)
            logger.error(
                "verification_failed",
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
                claimed=IncidentStatus.VERIFYING,
                restore=IncidentStatus.VERIFYING,
            )
            raise failure from None


async def checks_since(db: AsyncSession, service: str, since: datetime) -> list[ServiceHealth]:
    """Health checks of `service` recorded at or after `since`, oldest first (max 50)."""
    rows = await db.scalars(
        select(ServiceHealth)
        .where(ServiceHealth.service_name == service, ServiceHealth.timestamp >= since)
        .order_by(ServiceHealth.timestamp, ServiceHealth.id)
        .limit(50)
    )
    return list(rows)


async def _assess(
    db: AsyncSession,
    service: str,
    execution: ExecutionResult,
    approval: Approval | None,
) -> tuple[list[RecoveryCheck], list[EvidenceItem], ServiceSnapshot]:
    """Deterministic recovery checks on current telemetry. Returns (checks, evidence, after)."""
    executed_at = as_utc(execution.executed_at)
    params = execution.parameters
    state = await deployment_state(db, service)
    health = await telemetry.latest_health(db, service)
    active = state.active

    def t(ts: datetime) -> str:
        return f"{ts:%H:%M:%S} ({relative(as_utc(ts), executed_at)} vs execution)"

    b = execution.before
    # A real service's "error rate" is the share of failed health checks among the last 10.
    rate = "failed health checks (last 10)" if not is_demo_service(service) else "error_rate"

    def num(value: float | None, unit: str) -> str:
        return f"{value:g}{unit}" if value is not None else "unknown"

    evidence = [
        EvidenceItem(
            id="V1",
            source="execution",
            service=service,
            timestamp=executed_at,
            fact=f"Approved {execution.action} executed at {executed_at:%H:%M:%S}: "
            + (
                f"{service} {params.get('from_version')} -> {params.get('to_version')}"
                if execution.action is ActionType.ROLLBACK_DEPLOYMENT
                else f"restart of {service}"
            )
            + f" (approval {execution.approval_id})",
            data=execution.model_dump(mode="json"),
        ),
        EvidenceItem(
            id="V2",
            source="health",
            service=service,
            timestamp=executed_at,
            fact=f"{service} before remediation: {b.status}, {rate} {num(b.error_rate, '%')}, "
            f"latency {num(b.latency_ms, ' ms')}, active {b.active_version}",
            data=b.model_dump(mode="json"),
        ),
    ]
    if health is not None:
        evidence.append(
            EvidenceItem(
                id="V3",
                source="health",
                service=service,
                timestamp=health.timestamp,
                fact=f"{service} now: {health.status} at {t(health.timestamp)}, {rate} "
                f"{health.error_rate:g}%, latency {health.latency_ms:g} ms",
                data={
                    "status": health.status.value,
                    "error_rate": health.error_rate,
                    "latency_ms": health.latency_ms,
                },
            )
        )
    if active is not None:
        evidence.append(
            EvidenceItem(
                id="V4",
                source="deployments",
                service=service,
                timestamp=active.timestamp,
                fact=f"{service} active deployment: {active.version} ({active.status}), "
                f"deployed {t(active.timestamp)}",
                data={"version": active.version, "status": active.status.value},
            )
        )
    health_ids = ["V3"] if health is not None else []
    missing = "no health data"

    real = not is_demo_service(service)
    remediation_ok = approval is not None and approval.status is ApprovalStatus.APPROVED
    performed = (
        "performed by an operator"
        if execution.performed_by == "operator"
        else "execution COMPLETED"
    )
    checks = [
        RecoveryCheck(
            name="remediation_executed",
            passed=remediation_ok,
            expected="approved remediation executed",
            actual=f"approval {approval.status if approval else 'missing'}, {performed}",
            evidence_ids=["V1"],
        )
    ]
    # A real application's deployment switch (e.g. a Vercel rollback) is not observable through
    # GitHub events, so it is not checked; the operator's confirmation is check 1.
    if execution.action is ActionType.ROLLBACK_DEPLOYMENT and not real:
        target = params.get("to_version")
        checks.append(
            RecoveryCheck(
                name="target_deployment_active",
                passed=active is not None and active.version == target,
                expected=f"{service} {target} active",
                actual=f"{active.version} active" if active else "no active deployment",
                evidence_ids=["V4"] if active else [],
            )
        )
    if real:
        # Health checks measure availability, not request errors: every check recorded since
        # the operator's action must have succeeded (and enough of them must exist).
        settings = get_settings()
        latency_slo = settings.monitored_latency_slo_ms
        since = await checks_since(db, service, executed_at)
        succeeded = sum(row.status is ServiceStatus.HEALTHY for row in since)
        needed = settings.verify_min_checks
        error_check = RecoveryCheck(
            name="error_rate_recovered",
            passed=len(since) >= needed and succeeded == len(since),
            expected=f"every health check since the remediation succeeds (at least {needed})",
            actual=f"{succeeded} of {len(since)} succeeded",
            evidence_ids=["V2", *health_ids],
        )
    else:
        latency_slo = LATENCY_SLO_MS
        error_check = RecoveryCheck(
            name="error_rate_recovered",
            passed=health is not None and health.error_rate < ERROR_RATE_THRESHOLD,
            expected=f"< {ERROR_RATE_THRESHOLD:g}% (was {num(b.error_rate, '%')})",
            actual=f"{health.error_rate:g}%" if health else missing,
            evidence_ids=["V2", *health_ids],
        )
    checks += [
        RecoveryCheck(
            name="service_healthy",
            passed=health is not None and health.status is ServiceStatus.HEALTHY,
            expected="HEALTHY",
            actual=str(health.status) if health else missing,
            evidence_ids=health_ids,
        ),
        error_check,
        RecoveryCheck(
            name="latency_recovered",
            passed=health is not None and health.latency_ms < latency_slo,
            expected=f"< {latency_slo:g} ms (was {num(b.latency_ms, ' ms')})",
            actual=f"{health.latency_ms:g} ms" if health else missing,
            evidence_ids=["V2", *health_ids],
        ),
        RecoveryCheck(
            name="telemetry_fresh",
            passed=health is not None and as_utc(health.timestamp) >= executed_at,
            expected="health recorded after the remediation",
            actual=t(health.timestamp) if health else missing,
            evidence_ids=["V1", *health_ids],
        ),
    ]
    after = ServiceSnapshot(
        active_version=active.version if active else None,
        status=health.status if health else None,
        error_rate=health.error_rate if health else None,
        latency_ms=health.latency_ms if health else None,
    )
    return checks, evidence, after


async def run_view(db: AsyncSession, run: AgentRun) -> VerificationRunRead:
    fresh = await reload(db, AgentRun, run.id)
    incident = await reload(db, Incident, run.incident_id)
    assert fresh is not None and incident is not None
    completed = fresh.status is AgentRunStatus.COMPLETED and fresh.output
    return VerificationRunRead(
        run_id=fresh.id,
        incident_id=incident.id,
        incident_reference=incident.reference,
        incident_status=incident.status,
        agent=fresh.agent_name,
        status=fresh.status,
        started_at=fresh.started_at,
        completed_at=fresh.completed_at,
        summary=fresh.summary,
        result=VerificationResult.model_validate(fresh.output) if completed else None,
    )
