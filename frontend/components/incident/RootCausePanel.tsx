import { Badge, Card, Empty, EvidenceRefs, evidenceIndex, Meter } from "@/components/ui";
import { label, percent } from "@/lib/format";
import type { EvidenceItem, RootCauseRun } from "@/types/api";

export function RootCausePanel({ run, evidence }: { run: RootCauseRun | null; evidence: EvidenceItem[] }) {
  const result = run?.result;
  if (!run || !result) {
    return (
      <Card eyebrow="Root cause agent" title="Likely root cause">
        <Empty>{run?.status === "FAILED" ? run.summary : "Root cause analysis has not run yet."}</Empty>
      </Card>
    );
  }
  const index = evidenceIndex(evidence, result.supporting_evidence);

  return (
    <Card eyebrow="Root cause agent" title="Likely root cause" actions={<Badge tone="violet">{label(result.category)}</Badge>}>
      <p className="mb-3 text-lg leading-snug font-medium text-slate-50">{result.root_cause}</p>
      <div className="mb-4 flex items-center gap-3 text-xs text-slate-400">
        <span className="w-28 shrink-0">Confidence {percent(result.confidence)}</span>
        <Meter value={result.confidence} tone="emerald" />
      </div>
      <p className="mb-4 text-sm text-slate-400">{result.reasoning_summary}</p>

      <h3 className="mb-2 text-[11px] font-semibold tracking-widest text-slate-500 uppercase">Causal chain</h3>
      <ol className="mb-4 space-y-1.5 border-l border-slate-700 pl-4">
        {result.causal_chain.map((step, i) => (
          <li key={i} className="relative text-sm text-slate-200">
            <span className="absolute top-1.5 -left-[1.3rem] h-2 w-2 rounded-full bg-cyan-400" />
            {step.statement} <EvidenceRefs ids={step.evidence_ids} evidence={index} />
          </li>
        ))}
      </ol>

      <h3 className="mb-2 text-[11px] font-semibold tracking-widest text-slate-500 uppercase">Supporting evidence</h3>
      <ul className="mb-4 space-y-1">
        {result.supporting_evidence.map((item) => (
          <li key={item.id} className="flex gap-2 font-mono text-[11px] text-slate-400">
            <span className="shrink-0 text-cyan-300">{item.id}</span>
            <span className="break-words">{item.fact}</span>
          </li>
        ))}
      </ul>

      {result.contributing_factors.length > 0 && (
        <>
          <h3 className="mb-2 text-[11px] font-semibold tracking-widest text-slate-500 uppercase">Contributing factors</h3>
          <ul className="mb-4 list-disc space-y-1 pl-5 text-sm text-slate-300">
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
          <h3 className="mb-2 text-[11px] font-semibold tracking-widest text-slate-500 uppercase">Alternatives considered</h3>
          <ul className="mb-4 space-y-1.5">
            {result.alternative_explanations.map((alt, i) => (
              <li key={i} className="text-sm text-slate-300">
                <Badge tone={alt.assessment === "ruled_out" ? "slate" : "amber"}>{label(alt.assessment)}</Badge>{" "}
                <span className="text-slate-200">{alt.explanation}</span> — {alt.reason}{" "}
                <EvidenceRefs ids={alt.evidence_ids} evidence={index} />
              </li>
            ))}
          </ul>
        </>
      )}

      {result.missing_evidence.length > 0 && (
        <p className="text-xs text-slate-500">Would raise confidence: {result.missing_evidence.join("; ")}</p>
      )}
    </Card>
  );
}
