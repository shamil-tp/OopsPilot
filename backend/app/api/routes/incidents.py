import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import approval as approval_gate
from app.agents import investigation, remediation, root_cause, verification
from app.agents import report as reports
from app.agents.common import (
    AgentConflictError,
    AgentFailedError,
    IncidentNotFoundError,
    latest_run,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.common import ErrorResponse
from app.schemas.events import AgentEventRead
from app.schemas.execution import DecisionRead, ExecutionRead
from app.schemas.incidents import IncidentRead
from app.schemas.investigation import InvestigationRunRead
from app.schemas.remediation import RemediationRunRead
from app.schemas.report import IncidentReportRead
from app.schemas.root_cause import RootCauseRunRead
from app.schemas.verification import VerificationRunRead
from app.services import agent_events, incidents, simulator

router = APIRouter(prefix="/incidents", tags=["incidents"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "/simulate",
    response_model=IncidentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Simulate the payment-api incident",
    description=(
        "Deploys v1.8.2 of payment-api in the simulated environment, which causes database "
        "connection failures and HTTP 500s, persists the resulting logs, deployment and degraded "
        "health metrics, and creates a DETECTED incident. If a simulated incident is still "
        "active, it is returned unchanged with status 200 instead of creating a duplicate."
    ),
    responses={200: {"model": IncidentRead, "description": "Existing active incident returned"}},
)
async def simulate_incident(response: Response, db: DbSession) -> IncidentRead:
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_409_CONFLICT, "Demo mode is disabled (DEMO_MODE=false)")
    incident, created = await simulator.simulate_incident(db)
    if not created:
        response.status_code = status.HTTP_200_OK
    return IncidentRead.model_validate(incident)


@router.get("", response_model=list[IncidentRead], summary="List incidents (newest first)")
async def list_incidents(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncidentRead]:
    rows = await incidents.list_incidents(db, limit=limit, offset=offset)
    return [IncidentRead.model_validate(row) for row in rows]


@router.get(
    "/{incident_id}",
    response_model=IncidentRead,
    summary="Get an incident",
    responses={404: {"model": ErrorResponse, "description": "Incident not found"}},
)
async def get_incident(incident_id: int, db: DbSession) -> IncidentRead:
    incident = await incidents.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Incident {incident_id} not found")
    return IncidentRead.model_validate(incident)


@router.post(
    "/{incident_id}/investigate",
    response_model=InvestigationRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Investigation Agent on an incident",
    description=(
        "Moves a DETECTED incident to INVESTIGATING, collects bounded evidence with read-only "
        "tools (logs, health, deployments, previous incidents), and asks the AI provider for one "
        "structured analysis. Returns the stored run with its findings. The incident stays "
        "INVESTIGATING: no root cause is concluded and nothing is remediated. If the incident "
        "was already investigated, that result is returned with status 200 and no AI call."
    ),
    responses={
        200: {"model": InvestigationRunRead, "description": "Existing investigation returned"},
        404: {"model": ErrorResponse, "description": "Incident not found"},
        409: {"model": ErrorResponse, "description": "Already running, or wrong incident state"},
        502: {"model": ErrorResponse, "description": "AI analysis failed (retryable)"},
        503: {"model": ErrorResponse, "description": "AI not configured or database unavailable"},
    },
)
async def investigate_incident(
    incident_id: int, response: Response, db: DbSession
) -> InvestigationRunRead:
    try:
        run, created = await investigation.start_investigation(db, incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AgentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    if created:
        try:
            # Shielded: a client disconnect must not leave the run stuck in RUNNING.
            await asyncio.shield(investigation.run_investigation(run.id))
        except AgentFailedError as exc:
            raise HTTPException(exc.http_status, exc.message) from None
    else:
        response.status_code = status.HTTP_200_OK
    return await investigation.run_view(db, run)


@router.get(
    "/{incident_id}/events",
    response_model=list[AgentEventRead],
    summary="Agent timeline of an incident (oldest first)",
    responses={404: {"model": ErrorResponse, "description": "Incident not found"}},
)
async def incident_events(incident_id: int, db: DbSession) -> list[AgentEventRead]:
    if await incidents.get_incident(db, incident_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Incident {incident_id} not found")
    rows = await agent_events.list_events(db, incident_id)
    return [AgentEventRead.model_validate(row) for row in rows]


@router.post(
    "/{incident_id}/analyze",
    response_model=RootCauseRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Root Cause Analysis Agent on an investigated incident",
    description=(
        "Requires a completed investigation. Moves the incident from INVESTIGATING to ANALYZING, "
        "correlates the stored investigation evidence (no telemetry is re-collected) with one "
        "structured AI call, and returns the most likely root cause with validated evidence "
        "citations, alternatives and confidence. The incident stays ANALYZING: nothing is "
        "remediated. If the analysis already completed, it is returned with status 200 and no AI "
        "call."
    ),
    responses={
        200: {"model": RootCauseRunRead, "description": "Existing analysis returned"},
        404: {"model": ErrorResponse, "description": "Incident not found"},
        409: {
            "model": ErrorResponse,
            "description": "No completed investigation, analysis running, or wrong state",
        },
        502: {"model": ErrorResponse, "description": "AI analysis failed (retryable)"},
        503: {"model": ErrorResponse, "description": "AI not configured or database unavailable"},
    },
)
async def analyze_incident(incident_id: int, response: Response, db: DbSession) -> RootCauseRunRead:
    try:
        run, created = await root_cause.start_analysis(db, incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AgentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    if created:
        try:
            # Shielded: a client disconnect must not leave the run stuck in RUNNING.
            await asyncio.shield(root_cause.run_analysis(run.id))
        except AgentFailedError as exc:
            raise HTTPException(exc.http_status, exc.message) from None
    else:
        response.status_code = status.HTTP_200_OK
    return await root_cause.run_view(db, run)


@router.get(
    "/{incident_id}/analysis",
    response_model=RootCauseRunRead,
    summary="Latest root cause analysis of an incident",
    description="The most recent RCA run (RUNNING, FAILED or COMPLETED with its result).",
    responses={404: {"model": ErrorResponse, "description": "Incident or analysis not found"}},
)
async def get_analysis(incident_id: int, db: DbSession) -> RootCauseRunRead:
    incident = await incidents.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Incident {incident_id} not found")
    run = await latest_run(db, incident_id, root_cause.AGENT)
    if run is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{incident.reference} has no root cause analysis yet"
        )
    return await root_cause.run_view(db, run)


@router.post(
    "/{incident_id}/remediate",
    response_model=RemediationRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Remediation Agent: propose an action and request human approval",
    description=(
        "Requires a completed root cause analysis and an ANALYZING incident. The agent proposes "
        "one supported action (ROLLBACK_DEPLOYMENT, RESTART_SERVICE, NO_ACTION, "
        "ESCALATE_TO_HUMAN); the backend validates it against policy and real deployment data "
        "and decides risk and approval. Risky actions create a PENDING approval and move the "
        "incident to AWAITING_APPROVAL. Nothing is executed. The request takes no body: the "
        "action always comes from the server-side proposal. If a proposal already exists it is "
        "returned with status 200 and no AI call."
    ),
    responses={
        200: {"model": RemediationRunRead, "description": "Existing proposal returned"},
        404: {"model": ErrorResponse, "description": "Incident not found"},
        409: {
            "model": ErrorResponse,
            "description": "No completed RCA, proposal running, or wrong state",
        },
        502: {
            "model": ErrorResponse,
            "description": "AI failure or proposal rejected by backend policy (retryable)",
        },
        503: {"model": ErrorResponse, "description": "AI not configured or database unavailable"},
    },
)
async def remediate_incident(
    incident_id: int, response: Response, db: DbSession
) -> RemediationRunRead:
    try:
        run, created = await remediation.start_remediation(db, incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AgentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    if created:
        try:
            # Shielded: a client disconnect must not leave the run stuck in RUNNING.
            await asyncio.shield(remediation.run_remediation(run.id))
        except AgentFailedError as exc:
            raise HTTPException(exc.http_status, exc.message) from None
    else:
        response.status_code = status.HTTP_200_OK
    return await remediation.run_view(db, run)


@router.get(
    "/{incident_id}/remediation",
    response_model=RemediationRunRead,
    summary="Latest remediation proposal and its approval",
    responses={404: {"model": ErrorResponse, "description": "Incident or proposal not found"}},
)
async def get_remediation(incident_id: int, db: DbSession) -> RemediationRunRead:
    incident = await incidents.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Incident {incident_id} not found")
    run = await latest_run(db, incident_id, remediation.AGENT)
    if run is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{incident.reference} has no remediation proposal yet"
        )
    return await remediation.run_view(db, run)


_DECISION_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "Incident or approval request not found"},
    409: {
        "model": ErrorResponse,
        "description": "Already decided the other way, decided concurrently, wrong state, or "
        "execution refused (stale approval)",
    },
}


@router.post(
    "/{incident_id}/approve",
    response_model=DecisionRead,
    summary="Approve the pending remediation and execute it (simulated)",
    description=(
        "Approves the incident's PENDING approval, i.e. the exact action and parameters the "
        "Remediation Agent proposed and the backend validated. The request takes no body: the "
        "client cannot choose the action, target or version. The backend then re-validates the "
        "stored parameters against live deployment data and executes the simulated action "
        "(AWAITING_APPROVAL -> REMEDIATING -> VERIFYING). No AI is involved. Approving again is "
        "idempotent and never executes twice. If execution is refused or fails, nothing is "
        "changed and the incident becomes FAILED."
    ),
    responses={
        **_DECISION_ERRORS,
        503: {"model": ErrorResponse, "description": "Database unavailable during execution"},
    },
)
async def approve_remediation(incident_id: int, db: DbSession) -> DecisionRead:
    try:
        approval, run, changed = await approval_gate.decide(db, incident_id, approve=True)
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AgentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    if changed and run is not None:
        try:
            # Shielded: a client disconnect must not interrupt a half-applied execution.
            await asyncio.shield(approval_gate.execute(run.id))
        except AgentConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        except AgentFailedError as exc:
            raise HTTPException(exc.http_status, exc.message) from None
    return await approval_gate.decision_view(db, incident_id, approval.id)


@router.post(
    "/{incident_id}/reject",
    response_model=DecisionRead,
    summary="Reject the pending remediation",
    description=(
        "Rejects the incident's PENDING approval. Nothing is executed and the incident becomes "
        "ESCALATED (CLAUDE.md §18). Takes no body. Rejecting again is idempotent."
    ),
    responses=_DECISION_ERRORS,
)
async def reject_remediation(incident_id: int, db: DbSession) -> DecisionRead:
    try:
        approval, _, _ = await approval_gate.decide(db, incident_id, approve=False)
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AgentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return await approval_gate.decision_view(db, incident_id, approval.id)


@router.get(
    "/{incident_id}/execution",
    response_model=ExecutionRead,
    summary="Execution of the approved remediation",
    responses={404: {"model": ErrorResponse, "description": "Incident or execution not found"}},
)
async def get_execution(incident_id: int, db: DbSession) -> ExecutionRead:
    incident = await incidents.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Incident {incident_id} not found")
    view = await approval_gate.execution_view(
        db, await latest_run(db, incident_id, approval_gate.AGENT)
    )
    if view is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{incident.reference} has no remediation execution"
        )
    return view


@router.post(
    "/{incident_id}/verify",
    response_model=VerificationRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Verification Agent: confirm recovery after the approved remediation",
    description=(
        "Requires a VERIFYING incident whose approved remediation was executed. The backend "
        "checks current telemetry (target deployment active, service HEALTHY, error rate and "
        "latency below the service thresholds, telemetry recorded after the remediation) and "
        "decides `recovered` itself; one AI call only explains the outcome. Recovered: "
        "incident RESOLVED. Not recovered: incident FAILED (nothing is re-executed). Takes no "
        "body. If verification already completed, it is returned with status 200 and no AI call."
    ),
    responses={
        200: {"model": VerificationRunRead, "description": "Existing verification returned"},
        404: {"model": ErrorResponse, "description": "Incident not found"},
        409: {
            "model": ErrorResponse,
            "description": "No completed approved remediation, verification running, or wrong "
            "state",
        },
        502: {"model": ErrorResponse, "description": "AI explanation failed (retryable)"},
        503: {"model": ErrorResponse, "description": "AI not configured or database unavailable"},
    },
)
async def verify_incident(
    incident_id: int, response: Response, db: DbSession
) -> VerificationRunRead:
    try:
        run, created = await verification.start_verification(db, incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AgentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    if created:
        try:
            # Shielded: a client disconnect must not leave the run stuck in RUNNING.
            await asyncio.shield(verification.run_verification(run.id))
        except AgentFailedError as exc:
            raise HTTPException(exc.http_status, exc.message) from None
        # CLAUDE.md §9: the final report follows verification (deterministic, no AI call).
        await reports.ensure_report(db, incident_id)
    else:
        response.status_code = status.HTTP_200_OK
    return await verification.run_view(db, run)


@router.get(
    "/{incident_id}/verification",
    response_model=VerificationRunRead,
    summary="Latest verification of an incident",
    responses={404: {"model": ErrorResponse, "description": "Incident or verification not found"}},
)
async def get_verification(incident_id: int, db: DbSession) -> VerificationRunRead:
    incident = await incidents.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Incident {incident_id} not found")
    run = await latest_run(db, incident_id, verification.AGENT)
    if run is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{incident.reference} has no verification yet"
        )
    return await verification.run_view(db, run)


@router.get(
    "/{incident_id}/investigation",
    response_model=InvestigationRunRead,
    summary="Latest investigation of an incident",
    responses={404: {"model": ErrorResponse, "description": "Incident or investigation not found"}},
)
async def get_investigation(incident_id: int, db: DbSession) -> InvestigationRunRead:
    incident = await incidents.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Incident {incident_id} not found")
    run = await latest_run(db, incident_id, investigation.AGENT)
    if run is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{incident.reference} has no investigation yet"
        )
    return await investigation.run_view(db, run)


@router.get(
    "/{incident_id}/report",
    response_model=IncidentReportRead,
    summary="Final incident report",
    description=(
        "The report assembled from the stored results of every phase (investigation, root "
        "cause, remediation, approval, execution, verification) and the incident timeline. It is "
        "created once, after verification, without any AI call; this endpoint returns the stored "
        "report (creating it if a verified incident does not have one yet)."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Incident not found"},
        409: {"model": ErrorResponse, "description": "The incident has not been verified yet"},
    },
)
async def get_report(incident_id: int, db: DbSession) -> IncidentReportRead:
    try:
        row, _ = await reports.ensure_report(db, incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AgentConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return reports.report_view(row)
