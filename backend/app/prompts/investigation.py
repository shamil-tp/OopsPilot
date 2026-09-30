"""Investigation Agent prompt: short rules + the compact evidence package, nothing else."""

from app.agents.evidence import EvidencePackage, IncidentContext

SYSTEM_INSTRUCTION = """You are the OpsPilot Investigation Agent reviewing incident evidence.
Rules:
- Use ONLY the evidence lines given. Never invent logs, metrics, deployments, incidents,
  timestamps or causes.
- Every finding cites the ids of the evidence lines that support it (e.g. L3, H1, D1).
- kind "observation": directly shown by the evidence.
  kind "hypothesis": a possible explanation to test next.
- Do not claim a root cause is proven; root cause analysis is the next step.
- Do not recommend, claim or describe any remediation, rollback, restart or resolution.
- summary: at most 60 words. findings: at most 6, each one sentence.
- confidence: 0-1, how strongly the evidence supports your findings.
- next_step: "root_cause_analysis" if the evidence is sufficient, else "collect_more_evidence"."""

_SECTIONS = (
    ("logs", "Logs"),
    ("health", "Health"),
    ("deployments", "Deployments"),
    ("previous_incidents", "Previous incidents"),
)


def build_prompt(incident: IncidentContext, package: EvidencePackage) -> str:
    lines = [
        f"Incident {package.reference} ({incident.severity}): {incident.title}",
        f"Service: {package.service}. Detected {package.detected_at:%Y-%m-%d %H:%M:%S} UTC (T0).",
        f"Description: {incident.description}",
        "",
        "Evidence (T-<n>s = seconds before T0):",
    ]
    for source, title in _SECTIONS:
        items = package.of(source)
        lines.append(f"[{title}]")
        lines += [f"{item.id} {item.fact}" for item in items] or ["none"]
    return "\n".join(lines)
