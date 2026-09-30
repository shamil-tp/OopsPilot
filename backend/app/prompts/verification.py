"""Verification prompt: the backend's recovery checks and the evidence behind them. The model
only explains; the outcome is already decided."""

from app.schemas.investigation import EvidenceItem
from app.schemas.verification import RecoveryCheck

SYSTEM_INSTRUCTION = """You are the OpsPilot Verification Agent. The backend has already checked
whether the service recovered after an approved remediation; its checks are final.
Rules:
- Explain the outcome stated below in at most 60 words. Do not contradict or re-decide it.
- Use ONLY the given evidence and cite its ids (V1, V2, ...). Never invent ids, metrics,
  versions or events.
- Compare before and after values. Do not propose or claim any further remediation."""


def build_prompt(
    *,
    reference: str,
    service: str,
    recovered: bool,
    checks: list[RecoveryCheck],
    evidence: list[EvidenceItem],
) -> str:
    outcome = "RECOVERED" if recovered else "NOT RECOVERED"
    return "\n".join(
        [
            f"Incident {reference}, service {service}. Backend outcome: {outcome}.",
            "Recovery checks:",
            *[
                f"- {c.name}: {'PASS' if c.passed else 'FAIL'} (expected {c.expected}; "
                f"actual {c.actual}; evidence {', '.join(c.evidence_ids) or 'none'})"
                for c in checks
            ],
            "",
            "Evidence:",
            *[f"{item.id} {item.fact}" for item in evidence],
        ]
    )
