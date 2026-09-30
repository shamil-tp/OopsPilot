import { describeAction, label, num, percent } from "@/lib/format";
import type { IncidentReportContent, ServiceSnapshot } from "@/types/api";

function snapshot(s: ServiceSnapshot): string {
  return `${s.status ?? "—"}, ${num(s.error_rate, "%")} errors, ${num(s.latency_ms, " ms")}, ${s.active_version ?? "—"}`;
}

/** The stored report rendered as Markdown for download (CLAUDE.md §27 "Download Report"). */
export function reportMarkdown(r: IncidentReportContent): string {
  const { summary: s, root_cause: rca, remediation, approval, execution, verification: v, outcome } = r;
  const lines = [
    `# Incident report — ${s.reference}: ${s.title}`,
    "",
    `- **Service:** ${s.service}`,
    `- **Severity:** ${s.severity}`,
    `- **Final status:** ${outcome.final_status}`,
    `- **Detected:** ${s.created_at}`,
    `- **Resolved:** ${s.resolved_at ?? "—"}`,
    "",
    "## Timeline",
    "",
    ...r.timeline.map((e) => `- \`${e.timestamp}\` **${e.event_type}** — ${e.message}`),
    "",
    "## Root cause",
    "",
    `${rca.root_cause}`,
    "",
    `Category: ${label(rca.category)} · confidence ${percent(rca.confidence)}`,
    "",
    "Causal chain:",
    ...rca.causal_chain.map((c) => `1. ${c.statement} (${c.evidence_ids.join(", ")})`),
    "",
    "Evidence:",
    ...rca.supporting_evidence.map((e) => `- ${e.id}: ${e.fact}`),
    "",
    "## Recommended action",
    "",
    `${describeAction(remediation.action, remediation.parameters, remediation.target)} — risk ${remediation.risk}`,
    "",
    remediation.reason,
    "",
    "## Human decision",
    "",
    `${approval.status}${approval.decided_at ? ` at ${approval.decided_at}` : ""}`,
    "",
    "## Action taken",
    "",
    `${outcome.remediation_performed ?? "—"} (simulated)`,
    "",
    `- Before: ${snapshot(execution.before)}`,
    `- After: ${snapshot(execution.after)}`,
    "",
    "## Verification",
    "",
    `${outcome.verification} — ${v.recovered ? "recovered" : "not recovered"}`,
    "",
    ...v.checks.map((c) => `- [${c.passed ? "x" : " "}] ${label(c.name)}: expected ${c.expected}, actual ${c.actual}`),
    "",
    v.reasoning_summary,
    "",
    "## Final status",
    "",
    `**${outcome.final_status}** (${outcome.recovery_status})`,
    "",
  ];
  return lines.join("\n");
}

export function download(filename: string, content: string, type: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
