"""Root Cause Analysis Agent: correlates the stored investigation into the most likely cause.

Flow for one incident (after a completed investigation):

    start_analysis   INVESTIGATING -> ANALYZING (atomic), agent_run RUNNING, agent_started
    run_analysis     load the investigation output (no telemetry is re-collected) ->
                     evidence_evaluated -> 1 AIProvider call -> citation guardrails ->
                     agent_run COMPLETED, root_cause_identified
                     (the incident stays ANALYZING; remediation is the next phase)

On failure the run is FAILED, an `error` event is stored, and the incident goes back to
INVESTIGATING so the analysis can be retried. This agent has no tools at all: it only reads
stored evidence and writes its own run and events.
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
from app.ai.base import AIProvider, GenerationOptions
from app.ai.errors import AIProviderError
from app.ai.factory import get_ai_provider
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import AgentRun, Incident
from app.models.enums import AgentName, AgentRunStatus, IncidentStatus
from app.prompts.root_cause import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.investigation import InvestigationResult
from app.schemas.root_cause import (
    AlternativeExplanation,
    CitedStatement,
    RootCauseAnalysis,
    RootCauseResult,
    RootCauseRunRead,
)
from app.services.agent_events import EventRecorder

logger = get_logger(__name__)

AGENT = AgentName.ROOT_CAUSE
MAX_ITEMS = 8
MAX_TEXT_CHARS = 800
GENERATION = GenerationOptions(
    system_instruction=SYSTEM_INSTRUCTION, temperature=0.0, max_output_tokens=1536
)


async def _completed_investigation(db: AsyncSession, incident_id: int) -> AgentRun | None:
    run = await latest_run(db, incident_id, AgentName.INVESTIGATION)
    if run is None or run.status is not AgentRunStatus.COMPLETED or not run.output:
        return None
    return run


async def start_analysis(db: AsyncSession, incident_id: int) -> tuple[AgentRun, bool]:
    """Claim the incident and create a RUNNING RCA run. Returns `(run, created)`.

    A completed analysis is returned as is (`created=False`, no AI call).
    """
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"Incident {incident_id} not found")
    ref = incident.reference

    previous = await latest_run(db, incident_id, AGENT)
    if previous is not None and previous.status is AgentRunStatus.COMPLETED:
        return previous, False
    if previous is not None and previous.status is AgentRunStatus.RUNNING:
        raise AgentConflictError(f"A root cause analysis of {ref} is already in progress")
    investigation = await _completed_investigation(db, incident_id)
    if investigation is None:
        raise AgentConflictError(
            f"{ref} has no completed investigation; run POST /api/incidents/{incident_id}"
            "/investigate first"
        )
    if incident.status is not IncidentStatus.INVESTIGATING:
        raise AgentConflictError(
            f"{ref} is {incident.status}; root cause analysis requires INVESTIGATING"
        )

    if not await claim_incident(
        db, incident_id, expected=IncidentStatus.INVESTIGATING, new=IncidentStatus.ANALYZING
    ):
        await db.rollback()
        raise AgentConflictError(f"A root cause analysis of {ref} has already started")

    run = AgentRun(
        incident_id=incident_id, agent_name=AGENT, status=AgentRunStatus.RUNNING, started_at=now()
    )
    db.add(run)
    EventRecorder(db, incident_id, AGENT).emit(
        "agent_started",
        f"Root cause analysis agent started for {ref}",
        investigation_run_id=investigation.id,
        incident_status={"from": IncidentStatus.INVESTIGATING, "to": IncidentStatus.ANALYZING},
    )
    await db.commit()
    logger.info(
        "agent_started", extra={"agent": AGENT, "incident_id": incident_id, "run_id": run.id}
    )
    return run, True


async def run_analysis(
    run_id: int,
    *,
    provider: AIProvider | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> None:
    """Execute a RUNNING analysis. Uses its own session so it can outlive the request."""
    async with session_factory() as db:
        run = await db.get(AgentRun, run_id)
        assert run is not None and run.status is AgentRunStatus.RUNNING
        incident_id = run.incident_id
        events = EventRecorder(db, incident_id, AGENT)
        try:
            source = await _completed_investigation(db, incident_id)
            if source is None or source.output is None:
                raise AIProviderError("the completed investigation is no longer available")
            investigation = InvestigationResult.model_validate(source.output)
            source_id = source.id
            by_source: dict[str, int] = {}
            for item in investigation.evidence:
                by_source[item.source] = by_source.get(item.source, 0) + 1
            events.emit(
                "evidence_evaluated",
                f"Loaded investigation run {source_id}: {len(investigation.evidence)} evidence "
                f"items, {len(investigation.findings)} findings",
                investigation_run_id=source_id,
                evidence_by_source=by_source,
                findings=len(investigation.findings),
            )
            events.emit(
                "root_cause_analysis_started",
                f"Correlating {len(investigation.evidence)} evidence items with AI",
                evidence_items=len(investigation.evidence),
            )
            await db.commit()

            ai = provider or get_ai_provider()
            analysis = await ai.generate_structured(
                build_prompt(investigation), RootCauseAnalysis, options=GENERATION
            )
            result, removed = _apply_guardrails(investigation, source_id, analysis, ai)

            await complete_run(
                db, run_id, summary=result.root_cause, output=result.model_dump(mode="json")
            )
            events.emit(
                "root_cause_identified",
                f"Most likely root cause ({result.confidence:.0%} confidence): {result.root_cause}",
                category=result.category,
                confidence=result.confidence,
                supporting_evidence=[e.id for e in result.supporting_evidence],
                alternatives=len(result.alternative_explanations),
                unsupported_citations_removed=removed,
                recommended_next_step=result.recommended_next_step,
            )
            await db.commit()
            logger.info(
                "root_cause_identified",
                extra={
                    "agent": AGENT,
                    "incident_id": incident_id,
                    "run_id": run_id,
                    "category": result.category,
                    "confidence": result.confidence,
                },
            )
        except Exception as exc:  # recorded and re-raised as a safe AgentFailedError
            failure = failure_for(
                exc, step="root cause analysis", restored=IncidentStatus.INVESTIGATING
            )
            logger.error(
                "root_cause_analysis_failed",
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
                restore=IncidentStatus.INVESTIGATING,
            )
            raise failure from None


def _cited(items: list[CitedStatement], known: set[str]) -> tuple[list[CitedStatement], int]:
    kept = []
    for item in items:
        refs = keep_known(item.evidence_ids, known)
        if refs and item.statement.strip():
            kept.append(CitedStatement(statement=item.statement.strip(), evidence_ids=refs))
    return kept[:MAX_ITEMS], len(items) - len(kept)


def _alternatives(
    items: list[AlternativeExplanation], known: set[str]
) -> tuple[list[AlternativeExplanation], int]:
    kept, removed = [], 0
    for alt in items:
        refs = keep_known(alt.evidence_ids, known)
        if alt.evidence_ids and not refs:  # cites only evidence we never supplied: fabricated
            removed += 1
            continue
        # An alternative may only be "ruled out" on the strength of real evidence.
        assessment = alt.assessment if refs or alt.assessment != "ruled_out" else "less_likely"
        kept.append(
            AlternativeExplanation(
                explanation=alt.explanation.strip(),
                assessment=assessment,
                reason=alt.reason.strip(),
                evidence_ids=refs,
            )
        )
    return kept[:MAX_ITEMS], removed


def _apply_guardrails(
    investigation: InvestigationResult,
    investigation_run_id: int,
    analysis: RootCauseAnalysis,
    provider: AIProvider,
) -> tuple[RootCauseResult, int]:
    """Validate every citation against the supplied evidence. Returns (result, items removed)."""
    evidence = {item.id: item for item in investigation.evidence}
    known = set(evidence)
    supporting = keep_known(analysis.supporting_evidence, known)
    if not supporting or not analysis.root_cause.strip():
        raise AIProviderError("the root cause did not cite any of the supplied evidence")
    chain, chain_removed = _cited(analysis.causal_chain, known)
    factors, factors_removed = _cited(analysis.contributing_factors, known)
    alternatives, alternatives_removed = _alternatives(analysis.alternative_explanations, known)
    removed = (
        len(analysis.supporting_evidence)
        - len(supporting)
        + chain_removed
        + factors_removed
        + alternatives_removed
    )

    result = RootCauseResult(
        incident_id=investigation.incident_id,
        incident_reference=investigation.incident_reference,
        service=investigation.service,
        root_cause=analysis.root_cause.strip()[:MAX_TEXT_CHARS],
        category=analysis.category,
        confidence=analysis.confidence,
        supporting_evidence=[evidence[ref] for ref in supporting],
        causal_chain=chain,
        contributing_factors=factors,
        alternative_explanations=alternatives,
        missing_evidence=[m.strip() for m in analysis.missing_evidence if m.strip()][:MAX_ITEMS],
        reasoning_summary=analysis.reasoning_summary.strip()[:MAX_TEXT_CHARS],
        recommended_next_step=analysis.recommended_next_step,
        investigation_run_id=investigation_run_id,
        model=f"{provider.name}:{getattr(provider, 'model', 'unknown')}",
    )
    return result, removed


async def run_view(db: AsyncSession, run: AgentRun) -> RootCauseRunRead:
    fresh = await reload(db, AgentRun, run.id)
    incident = await reload(db, Incident, run.incident_id)
    assert fresh is not None and incident is not None
    completed = fresh.status is AgentRunStatus.COMPLETED and fresh.output
    return RootCauseRunRead(
        run_id=fresh.id,
        incident_id=incident.id,
        incident_reference=incident.reference,
        incident_status=incident.status,
        agent=fresh.agent_name,
        status=fresh.status,
        started_at=fresh.started_at,
        completed_at=fresh.completed_at,
        summary=fresh.summary,
        result=RootCauseResult.model_validate(fresh.output) if completed else None,
    )
