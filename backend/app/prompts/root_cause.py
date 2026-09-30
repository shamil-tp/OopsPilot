"""Root Cause Analysis prompt: rules + the stored investigation, as one time-ordered timeline.

Evidence from all sources is merged into a single chronological list (deterministically, by the
backend) so the model correlates the order of events instead of reading separate tables.
"""

from app.schemas.investigation import EvidenceItem, InvestigationResult
from app.services.service_catalog import get_service

SYSTEM_INSTRUCTION = """You are the OpsPilot Root Cause Analysis Agent.
Input: evidence already collected by the investigation, in time order (T0 = detection).
Rules:
- Use ONLY the given evidence. Cite evidence ids (L/H/D/P) for every claim; never invent ids,
  facts, timestamps, services or dependencies.
- Correlate: what changed just before the first error, the order of events, whether the
  service's dependencies are healthy, and whether CPU/memory changed.
- Consider competing explanations (e.g. deployment/configuration regression, database or
  infrastructure failure, network issue, resource exhaustion, transient errors) and assess each.
  Use "ruled_out" only when evidence contradicts it; otherwise "less_likely".
- root_cause: one sentence naming the most likely technical cause (most likely, not certain).
- causal_chain: the key events in time order, each citing its evidence.
- confidence 0-1 reflects evidence strength and gaps; missing_evidence lists what would help.
- Do not recommend or describe specific fixes (rollback, restart, ...); recommended_next_step
  only chooses the next phase.
- reasoning_summary: at most 80 words."""


def _timeline(items: list[EvidenceItem]) -> list[EvidenceItem]:
    dated = sorted((i for i in items if i.timestamp is not None), key=lambda i: i.timestamp)
    return dated + [i for i in items if i.timestamp is None]


def build_prompt(investigation: InvestigationResult) -> str:
    service = get_service(investigation.service)
    dependencies = ", ".join(service.dependencies) if service and service.dependencies else "none"
    findings = [
        f"- [{f.kind}] {f.statement} ({', '.join(f.evidence_ids)})" for f in investigation.findings
    ]
    evidence = [f"{item.id} {item.fact}" for item in _timeline(investigation.evidence)]
    previous = [i for i in investigation.evidence if i.source == "previous_incidents"]
    return "\n".join(
        [
            f"Incident {investigation.incident_reference}. Affected service: "
            f"{investigation.service} (depends on: {dependencies}).",
            f"Investigation findings (confidence {investigation.confidence:g}):",
            *findings,
            "",
            "Evidence timeline:",
            *evidence,
            *([] if previous else ["Previous incidents: none"]),
        ]
    )
