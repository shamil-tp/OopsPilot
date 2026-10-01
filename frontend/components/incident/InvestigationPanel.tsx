import { Empty, EvidenceRefs, evidenceIndex, Section, table } from "@/components/ui";
import { label, percent } from "@/lib/format";
import type { EvidenceSource, InvestigationRun } from "@/types/api";

const SOURCE_LABEL: Record<EvidenceSource, string> = {
  logs: "Logs",
  health: "Health",
  deployments: "Deployments",
  previous_incidents: "Previous incidents",
  cicd: "GitHub CI/CD",
  code_review: "Code reviews",
  execution: "Execution",
};
const SOURCE_ORDER = Object.keys(SOURCE_LABEL) as EvidenceSource[];

export function InvestigationPanel({ run }: { run: InvestigationRun | null }) {
  const result = run?.result;
  if (!run || !result) {
    return (
      <Section id="investigation" title="Investigation">
        <Empty>
          {run?.status === "FAILED"
            ? run.summary
            : run?.status === "RUNNING"
              ? "Collecting evidence…"
              : "Not started. The investigation collects logs, health, deployments, previous incidents and GitHub events, then analyses them."}
        </Empty>
      </Section>
    );
  }
  const index = evidenceIndex(result.evidence);
  const observations = result.findings.filter((f) => f.kind === "observation").length;
  const hypotheses = result.findings.length - observations;
  const evidence = [...result.evidence].sort((a, b) => SOURCE_ORDER.indexOf(a.source) - SOURCE_ORDER.indexOf(b.source));

  return (
    <Section
      id="investigation"
      title="Investigation"
      aside={`${result.evidence.length} evidence items · ${observations} observation${observations === 1 ? "" : "s"} · ${hypotheses} hypothes${hypotheses === 1 ? "is" : "es"} · confidence ${percent(result.confidence)}`}
    >
      <p className="max-w-3xl text-sm text-ink">{result.summary}</p>

      <ul className="mt-4 divide-y divide-line border-y border-line">
        {result.findings.map((finding, i) => (
          <li key={i} className="grid grid-cols-1 gap-x-4 gap-y-0.5 py-2 sm:grid-cols-[6.5rem_minmax(0,1fr)]">
            <span className={`text-xs font-medium uppercase ${finding.kind === "hypothesis" ? "text-amber-700" : "text-muted"}`}>
              {finding.kind}
            </span>
            <span className="text-sm text-ink">
              {finding.statement} <EvidenceRefs ids={finding.evidence_ids} evidence={index} />
            </span>
          </li>
        ))}
      </ul>

      <details className="group mt-4">
        <summary className="cursor-pointer text-sm font-medium text-ink select-none">
          Evidence collected by read-only tools ({result.evidence.length})
        </summary>
        <div className={`${table.wrap} mt-2`}>
          <table className={table.table}>
            <thead>
              <tr>
                <th scope="col" className={table.th}>ID</th>
                <th scope="col" className={`${table.th} hidden sm:table-cell`}>Source</th>
                <th scope="col" className={table.th}>Fact</th>
              </tr>
            </thead>
            <tbody>
              {evidence.map((item) => (
                <tr key={item.id}>
                  <td className={`${table.td} ${table.mono} font-medium`}>{item.id}</td>
                  <td className={`${table.td} hidden whitespace-nowrap text-muted sm:table-cell`}>{SOURCE_LABEL[item.source]}</td>
                  <td className={`${table.td} font-mono text-xs leading-relaxed break-words text-ink`}>{item.fact}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {result.related_previous_incidents.length === 0 && (
          <p className="mt-2 text-xs text-muted">No previous incidents for {result.service}.</p>
        )}
      </details>
      <p className="mt-3 text-xs text-muted">
        Suggested next step: {label(result.next_step)} · model {result.model}
      </p>
    </Section>
  );
}
