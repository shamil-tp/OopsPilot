import { Empty, EvidenceRefs, evidenceIndex, Section, Subheading, table } from "@/components/ui";
import { label, percent } from "@/lib/format";
import type { EvidenceItem, RootCauseRun } from "@/types/api";

const ASSESSMENT_CLASS = {
  ruled_out: "text-muted",
  less_likely: "text-amber-700",
  not_assessable: "text-muted",
} as const;

export function RootCausePanel({ run, evidence }: { run: RootCauseRun | null; evidence: EvidenceItem[] }) {
  const result = run?.result;
  if (!run || !result) {
    return (
      <Section id="root-cause" title="Root cause">
        <Empty>
          {run?.status === "FAILED"
            ? run.summary
            : run?.status === "RUNNING"
              ? "Correlating the evidence…"
              : "Runs after the investigation, on the stored evidence."}
        </Empty>
      </Section>
    );
  }
  const index = evidenceIndex(evidence, result.supporting_evidence);

  return (
    <Section id="root-cause" title="Root cause" aside={`${label(result.category)} · confidence ${percent(result.confidence)}`}>
      <p className="max-w-3xl text-base leading-snug font-medium text-ink">{result.root_cause}</p>
      <p className="mt-2 max-w-3xl text-sm text-muted">{result.reasoning_summary}</p>

      <Subheading>Causal chain</Subheading>
      <ol className="max-w-3xl">
        {result.causal_chain.map((step, i) => (
          <li key={i}>
            {i > 0 && (
              <span aria-hidden className="block pl-3 text-muted">
                ↓
              </span>
            )}
            <span className="block rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-ink">
              <span className="sr-only">Step {i + 1}: </span>
              {step.statement} <EvidenceRefs ids={step.evidence_ids} evidence={index} />
            </span>
          </li>
        ))}
      </ol>

      <Subheading>Supporting evidence</Subheading>
      <div className={table.wrap}>
        <table className={table.table}>
          <tbody>
            {result.supporting_evidence.map((item) => (
              <tr key={item.id}>
                <td className={`${table.td} ${table.mono} w-12 font-medium`}>{item.id}</td>
                <td className={`${table.td} font-mono text-xs leading-relaxed break-words`}>{item.fact}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {result.contributing_factors.length > 0 && (
        <>
          <Subheading>Contributing factors</Subheading>
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
            {result.contributing_factors.map((factor, i) => (
              <li key={i}>
                {factor.statement} <EvidenceRefs ids={factor.evidence_ids} evidence={index} />
              </li>
            ))}
          </ul>
        </>
      )}

      {result.alternative_explanations.length > 0 && (
        <>
          <Subheading>Alternatives considered</Subheading>
          <div className={table.wrap}>
            <table className={table.table}>
              <tbody>
                {result.alternative_explanations.map((alt, i) => (
                  <tr key={i}>
                    <td className={`${table.td} w-28 text-xs font-medium whitespace-nowrap uppercase ${ASSESSMENT_CLASS[alt.assessment]}`}>
                      {label(alt.assessment)}
                    </td>
                    <td className={table.td}>
                      <span className="font-medium text-ink">{/[.!?]$/.test(alt.explanation) ? alt.explanation : `${alt.explanation}.`}</span>{" "}
                      <span className="text-muted">{alt.reason}</span>{" "}
                      <EvidenceRefs ids={alt.evidence_ids} evidence={index} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {result.missing_evidence.length > 0 && (
        <p className="mt-3 text-xs text-muted">Would raise confidence: {result.missing_evidence.join("; ")}</p>
      )}
    </Section>
  );
}
