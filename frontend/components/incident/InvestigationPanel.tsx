import { Badge, Card, Empty, EvidenceRefs, evidenceIndex, Meter } from "@/components/ui";
import { label, percent } from "@/lib/format";
import type { EvidenceSource, InvestigationRun } from "@/types/api";

const SOURCE_LABEL: Record<EvidenceSource, string> = {
  logs: "Logs",
  health: "Health",
  deployments: "Deployments",
  previous_incidents: "Previous incidents",
  execution: "Execution",
};

export function InvestigationPanel({ run }: { run: InvestigationRun | null }) {
  const result = run?.result;
  if (!run || !result) {
    return (
      <Card eyebrow="Investigation agent" title="Evidence">
        <Empty>{run?.status === "FAILED" ? run.summary : "Not investigated yet."}</Empty>
      </Card>
    );
  }
  const evidence = evidenceIndex(result.evidence);
  const sources = (Object.keys(SOURCE_LABEL) as EvidenceSource[]).filter((s) =>
    result.evidence.some((e) => e.source === s),
  );

  return (
    <Card
      eyebrow="Investigation agent"
      title="Evidence & findings"
      actions={<Badge tone="cyan">{result.evidence.length} evidence items</Badge>}
    >
      <p className="mb-4 text-sm text-slate-300">{result.summary}</p>
      <div className="mb-4 flex items-center gap-3 text-xs text-slate-400">
        <span className="w-28 shrink-0">Confidence {percent(result.confidence)}</span>
        <Meter value={result.confidence} />
      </div>

      <ul className="mb-5 space-y-2">
        {result.findings.map((finding, i) => (
          <li key={i} className="flex gap-2 text-sm">
            <Badge tone={finding.kind === "observation" ? "sky" : "violet"}>{finding.kind}</Badge>
            <span className="text-slate-200">
              {finding.statement} <EvidenceRefs ids={finding.evidence_ids} evidence={evidence} />
            </span>
          </li>
        ))}
      </ul>

      <details className="group">
        <summary className="cursor-pointer text-xs font-semibold tracking-widest text-slate-400 uppercase hover:text-slate-200">
          Evidence collected by read-only tools
        </summary>
        <div className="mt-3 space-y-4">
          {sources.map((source) => (
            <div key={source}>
              <p className="mb-1 text-[11px] font-semibold text-slate-500 uppercase">{SOURCE_LABEL[source]}</p>
              <ul className="space-y-1">
                {result.evidence
                  .filter((e) => e.source === source)
                  .map((item) => (
                    <li key={item.id} className="flex gap-2 font-mono text-[11px] leading-relaxed text-slate-400">
                      <span className="shrink-0 text-cyan-300">{item.id}</span>
                      <span className="break-words">{item.fact}</span>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
          {result.related_previous_incidents.length === 0 && (
            <p className="text-[11px] text-slate-500">No previous incidents for {result.service}.</p>
          )}
          <p className="text-[11px] text-slate-600">
            Next step suggested: {label(result.next_step)} · {result.model}
          </p>
        </div>
      </details>
    </Card>
  );
}
