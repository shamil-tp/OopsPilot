import { SnapshotCompare } from "@/components/incident/SnapshotCompare";
import { Dot, Empty, EvidenceRefs, evidenceIndex, Section, Subheading, table } from "@/components/ui";
import { label } from "@/lib/format";
import type { VerificationRun } from "@/types/api";

export function VerificationPanel({ run, operator = false }: { run: VerificationRun | null; operator?: boolean }) {
  const result = run?.result;
  if (!run || !result) {
    return (
      <Section id="verification" title="Verification">
        <Empty>
          {run?.status === "FAILED"
            ? run.summary
            : run?.status === "RUNNING"
              ? "Checking the service…"
              : "Runs after an approved remediation has been executed."}
        </Empty>
      </Section>
    );
  }
  const index = evidenceIndex(result.supporting_evidence);
  const passed = result.checks.filter((c) => c.passed).length;

  return (
    <Section id="verification" title="Verification" aside="Outcome decided by backend checks">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1" role="status">
        <Dot tone={result.recovered ? "ok" : "critical"} className="h-2.5 w-2.5" />
        <span className={`text-base font-semibold ${result.recovered ? "text-emerald-700" : "text-red-700"}`}>
          {result.recovered ? "Recovered" : "Not recovered"}
        </span>
        <span className="text-sm text-muted">
          {passed} / {result.checks.length} checks passed
        </span>
      </p>

      <div className="mt-4">
        <SnapshotCompare before={result.before} after={result.after} operator={operator} />
      </div>

      <Subheading>Checks</Subheading>
      <div className={table.wrap}>
        <table className={table.table}>
          <thead>
            <tr>
              <th scope="col" className={table.th}>Check</th>
              <th scope="col" className={`${table.th} hidden sm:table-cell`}>Expected</th>
              <th scope="col" className={table.th}>Actual</th>
              <th scope="col" className={`${table.th} text-right`}>Result</th>
            </tr>
          </thead>
          <tbody>
            {result.checks.map((check) => (
              <tr key={check.name}>
                <td className={table.td}>{label(check.name)}</td>
                <td className={`${table.td} hidden font-mono text-xs text-muted sm:table-cell`}>{check.expected}</td>
                <td className={`${table.td} font-mono text-xs`}>{check.actual}</td>
                <td className={`${table.td} text-right text-xs font-semibold ${check.passed ? "text-emerald-700" : "text-red-700"}`}>
                  {check.passed ? "Pass" : "Fail"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-4 max-w-3xl text-sm text-ink">
        {result.reasoning_summary} <EvidenceRefs ids={result.supporting_evidence.map((e) => e.id)} evidence={index} />
      </p>
    </Section>
  );
}
