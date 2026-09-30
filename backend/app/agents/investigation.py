"""Investigation Agent: read-only evidence collection + one structured AI analysis.

Flow for one incident:

    start_investigation   DETECTED -> INVESTIGATING (atomic), agent_run RUNNING, agent_started
    run_investigation     read-only tools -> evidence package -> 1 AIProvider call ->
                          guardrails -> agent_run COMPLETED, investigation_completed
                          (the incident stays INVESTIGATING; RCA is the next phase)

On failure the run is FAILED, an `error` event is stored, and the incident goes back to DETECTED
so the investigation can be retried. The agent never runs actions: its ToolExecutor only accepts
the allowlisted READ_ONLY tools.
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
    now,
    record_failure,
    reload,
)
from app.agents.evidence import EvidenceCollector, EvidencePackage, IncidentContext
from app.ai.base import AIProvider, GenerationOptions
from app.ai.errors import AIProviderError
from app.ai.factory import get_ai_provider
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import AgentRun, Incident
from app.models.enums import AgentName, AgentRunStatus, IncidentStatus
from app.prompts.investigation import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.investigation import (
    InvestigationAnalysis,
    InvestigationFinding,
    InvestigationResult,
    InvestigationRunRead,
)
from app.services.agent_events import EventRecorder
from app.tools import ToolExecutor

logger = get_logger(__name__)

AGENT = AgentName.INVESTIGATION
MAX_FINDINGS = 8
MAX_SUMMARY_CHARS = 800
# Small, deterministic output: the evidence package is already filtered by the backend.
GENERATION = GenerationOptions(
    system_instruction=SYSTEM_INSTRUCTION, temperature=0.0, max_output_tokens=1024
)


async def start_investigation(db: AsyncSession, incident_id: int) -> tuple[AgentRun, bool]:
    """Claim the incident and create a RUNNING run. Returns `(run, created)`.

    A completed investigation is returned as is (`created=False`, no AI call): investigating the
    same evidence twice would only spend quota.
    """
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"Incident {incident_id} not found")

    previous = await latest_run(db, incident_id, AGENT)
    if previous is not None and previous.status is AgentRunStatus.COMPLETED:
        return previous, False
    if previous is not None and previous.status is AgentRunStatus.RUNNING:
        raise AgentConflictError(f"An investigation of {incident.reference} is already in progress")
    if incident.status is not IncidentStatus.DETECTED:
        raise AgentConflictError(
            f"{incident.reference} is {incident.status}; "
            "only DETECTED incidents can be investigated"
        )

    # Atomic claim: of two concurrent requests, only one moves the incident out of DETECTED.
    ref = incident.reference  # read before a rollback expires the ORM object
    if not await claim_incident(
        db, incident_id, expected=IncidentStatus.DETECTED, new=IncidentStatus.INVESTIGATING
    ):
        await db.rollback()
        raise AgentConflictError(f"An investigation of {ref} has already started")

    run = AgentRun(
        incident_id=incident_id, agent_name=AGENT, status=AgentRunStatus.RUNNING, started_at=now()
    )
    db.add(run)
    EventRecorder(db, incident_id, AGENT).emit(
        "agent_started",
        f"Investigation agent started for {incident.reference}",
        service=incident.service_name,
        incident_status={"from": IncidentStatus.DETECTED, "to": IncidentStatus.INVESTIGATING},
    )
    await db.commit()
    logger.info(
        "agent_started", extra={"agent": AGENT, "incident_id": incident_id, "run_id": run.id}
    )
    return run, True


async def run_investigation(
    run_id: int,
    *,
    provider: AIProvider | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> None:
    """Execute a RUNNING investigation. Uses its own session so it can outlive the request."""
    settings = get_settings()
    async with session_factory() as db:
        run = await db.get(AgentRun, run_id)
        assert run is not None and run.status is AgentRunStatus.RUNNING
        loaded = await db.get(Incident, run.incident_id)
        assert loaded is not None
        incident = IncidentContext.of(loaded)
        events = EventRecorder(db, incident.id, AGENT)
        try:
            executor = ToolExecutor(
                AGENT,
                db,
                max_calls=settings.max_agent_steps,
                max_retries=settings.tool_max_retries,
            )
            package = await EvidenceCollector(executor, events, db.commit).collect(incident)
            events.emit(
                "investigation_analysis_started",
                f"Analyzing {len(package.items)} evidence items with AI",
                evidence_items=len(package.items),
                tool_calls=executor.calls,
            )
            await db.commit()

            ai = provider or get_ai_provider()
            analysis = await ai.generate_structured(
                build_prompt(incident, package), InvestigationAnalysis, options=GENERATION
            )
            result, dropped = _apply_guardrails(incident, package, analysis, ai)

            await complete_run(
                db, run_id, summary=result.summary, output=result.model_dump(mode="json")
            )
            events.emit(
                "investigation_completed",
                f"Investigation complete: {len(result.findings)} finding(s); next step "
                f"{result.next_step}",
                findings=len(result.findings),
                observations=sum(f.kind == "observation" for f in result.findings),
                hypotheses=sum(f.kind == "hypothesis" for f in result.findings),
                unsupported_findings_dropped=dropped,
                confidence=result.confidence,
                next_step=result.next_step,
            )
            await db.commit()
            logger.info(
                "investigation_completed",
                extra={
                    "agent": AGENT,
                    "incident_id": incident.id,
                    "run_id": run_id,
                    "findings": len(result.findings),
                    "tool_calls": executor.calls,
                },
            )
        except Exception as exc:  # recorded and re-raised as a safe AgentFailedError
            failure = failure_for(exc, step="investigation", restored=IncidentStatus.DETECTED)
            logger.error(
                "investigation_failed",
                extra={
                    "agent": AGENT,
                    "incident_id": incident.id,
                    "run_id": run_id,
                    "error": type(exc).__name__,
                },
            )
            await record_failure(
                db,
                agent=AGENT,
                run_id=run_id,
                incident_id=incident.id,
                message=failure.message,
                claimed=IncidentStatus.INVESTIGATING,
                restore=IncidentStatus.DETECTED,
            )
            raise failure from None


def _apply_guardrails(
    incident: IncidentContext,
    package: EvidencePackage,
    analysis: InvestigationAnalysis,
    provider: AIProvider,
) -> tuple[InvestigationResult, int]:
    """Keep only findings backed by supplied evidence ids. Returns (result, findings dropped)."""
    known = package.ids
    findings: list[InvestigationFinding] = []
    for finding in analysis.findings:
        cited = keep_known(finding.evidence_ids, known)
        if cited and finding.statement.strip():
            findings.append(
                InvestigationFinding(
                    kind=finding.kind, statement=finding.statement.strip(), evidence_ids=cited
                )
            )
    dropped = len(analysis.findings) - len(findings)
    if not findings:
        raise AIProviderError("AI findings did not cite any of the supplied evidence")

    result = InvestigationResult(
        incident_id=incident.id,
        incident_reference=package.reference,
        service=package.service,
        summary=analysis.summary.strip()[:MAX_SUMMARY_CHARS],
        findings=findings[:MAX_FINDINGS],
        evidence=package.items,
        related_deployments=package.deployments,
        related_previous_incidents=package.previous_incidents,
        confidence=analysis.confidence,
        next_step=analysis.next_step,
        model=f"{provider.name}:{getattr(provider, 'model', 'unknown')}",
    )
    return result, dropped


async def run_view(db: AsyncSession, run: AgentRun) -> InvestigationRunRead:
    fresh = await reload(db, AgentRun, run.id)
    incident = await reload(db, Incident, run.incident_id)
    assert fresh is not None and incident is not None
    completed = fresh.status is AgentRunStatus.COMPLETED and fresh.output
    return InvestigationRunRead(
        run_id=fresh.id,
        incident_id=incident.id,
        incident_reference=incident.reference,
        incident_status=incident.status,
        agent=fresh.agent_name,
        status=fresh.status,
        started_at=fresh.started_at,
        completed_at=fresh.completed_at,
        summary=fresh.summary,
        result=InvestigationResult.model_validate(fresh.output) if completed else None,
    )
