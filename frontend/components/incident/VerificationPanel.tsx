import { SnapshotCompare } from "@/components/incident/SnapshotCompare";
import { Badge, Card, Empty, EvidenceRefs, evidenceIndex } from "@/components/ui";
import { label } from "@/lib/format";
import type { VerificationRun } from "@/types/api";

export function VerificationPanel({ run }: { run: VerificationRun | null }) {
  const result = run?.result;
  if (!run || !result) {
    return (
      <Card eyebrow="Verification agent" title="Recovery verification">
        <Empty>{run?.status === "FAILED" ? run.summary : "Not verified yet."}</Empty>
      </Card>
    );
  }
  const index = evidenceIndex(result.supporting_evidence);
  const passed = result.checks.filter((c) => c.passed).length;

  return (
    <Card
      eyebrow="Verification agent"
      title="Recovery verification"
      actions={
        <Badge tone={result.recovered ? "emerald" : "rose"}>
          {result.recovered ? "Recovered" : "Not recovered"} · {passed}/{result.checks.length}
        </Badge>
      }
    >
      <SnapshotCompare before={result.before} after={result.after} />

      <table className="mt-4 w-full text-left text-xs">
        <thead className="text-slate-500">
          <tr>
            <th className="py-1 font-medium">Check</th>
            <th className="py-1 font-medium">Expected</th>
            <th className="py-1 font-medium">Actual</th>
            <th className="py-1 text-right font-medium">Result</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {result.checks.map((check) => (
            <tr key={check.name}>
              <td className="py-1.5 pr-2 text-slate-200">{label(check.name)}</td>
              <td className="py-1.5 pr-2 font-mono text-slate-400">{check.expected}</td>
              <td className="py-1.5 pr-2 font-mono text-slate-300">{check.actual}</td>
              <td className={`py-1.5 text-right font-semibold ${check.passed ? "text-emerald-300" : "text-rose-300"}`}>
                {check.passed ? "PASS" : "FAIL"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="mt-4 text-sm text-slate-300">
        {result.reasoning_summary}{" "}
        <EvidenceRefs ids={result.supporting_evidence.map((e) => e.id)} evidence={index} />
      </p>
      <p className="mt-2 text-xs text-slate-500">
        The outcome is decided by the backend checks above; the AI only writes the summary.
      </p>
    </Card>
  );
}
