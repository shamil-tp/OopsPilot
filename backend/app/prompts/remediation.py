"""Remediation prompt: the stored RCA, the evidence it cites, and the real deployment options."""

from app.models import Deployment
from app.schemas.investigation import EvidenceItem
from app.schemas.root_cause import RootCauseResult

SYSTEM_INSTRUCTION = """You are the OpsPilot Remediation Agent. You PROPOSE one corrective action;
you never execute it. The backend validates your proposal and a human approves risky actions.
Supported actions (no others exist):
- ROLLBACK_DEPLOYMENT: roll the affected service back to one of the listed rollback candidates.
- RESTART_SERVICE: restart the affected service or one of its listed dependencies.
- NO_ACTION: the evidence shows nothing needs to change.
- ESCALATE_TO_HUMAN: no supported action fits, or the evidence is insufficient.
Rules:
- Use ONLY the supplied root cause analysis and evidence; cite evidence ids. Never invent
  versions, services, evidence ids or facts.
- rollback_to_version: exactly one listed rollback candidate for ROLLBACK_DEPLOYMENT, else null.
- Prefer the least disruptive supported action that addresses the root cause.
- Do not claim the action is approved or executed, that the service recovered, or that the
  incident is resolved.
- reason: at most 50 words."""


def build_prompt(
    rca: RootCauseResult,
    evidence: list[EvidenceItem],
    dependencies: list[str],
    active: Deployment | None,
    candidates: list[Deployment],
) -> str:
    def deployment(d: Deployment) -> str:
        return f"{d.version} (deployed {d.timestamp:%Y-%m-%d %H:%M:%S} UTC, commit {d.commit_sha})"

    lines = [
        f"Incident {rca.incident_reference}. Affected service: {rca.service} "
        f"(dependencies: {', '.join(dependencies) or 'none'}).",
        f"Active deployment: {deployment(active) if active else 'unknown'}",
        "Rollback candidates: "
        + ("; ".join(deployment(d) for d in candidates) if candidates else "none"),
        "",
        f"Root cause ({rca.category}, confidence {rca.confidence:g}): {rca.root_cause}",
        "Causal chain:",
        *[f"- {c.statement} ({', '.join(c.evidence_ids)})" for c in rca.causal_chain],
        "Alternatives considered:",
        *[
            f"- {a.explanation}: {a.assessment} ({', '.join(a.evidence_ids) or 'no evidence'})"
            for a in rca.alternative_explanations
        ],
        "",
        "Evidence:",
        *[f"{item.id} {item.fact}" for item in evidence],
    ]
    return "\n".join(lines)
