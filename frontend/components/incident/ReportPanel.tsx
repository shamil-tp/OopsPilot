"use client";

import type { ReactNode } from "react";

import { SnapshotCompare } from "@/components/incident/SnapshotCompare";
import { Button, Dot, Panel, RiskLabel, Section, StatusIndicator, table } from "@/components/ui";
import { dateTime, describeAction, duration, label, num, percent, time } from "@/lib/format";
import { download, reportMarkdown } from "@/lib/report-markdown";
import type { EvidenceItem, IncidentReport } from "@/types/api";

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-line pt-5">
      <h3 className="mb-2 text-sm font-semibold text-ink">{title}</h3>
      {children}
    </section>
  );
}

function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
      {items.map(([term, value]) => (
        <div key={term}>
          <dt className="text-xs text-muted">{term}</dt>
          <dd className="mt-0.5 text-sm text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The stored final report, laid out as an engineering incident report. */
export function ReportPanel({ report }: { report: IncidentReport }) {
  const r = report.report;
  const { summary: s, root_cause: rca, remediation, approval, execution, verification: v, outcome } = r;
  const file = `${s.reference}-incident-report`;
  const cicd = r.investigation.evidence.filter((e) => e.source === "cicd");
  const evidence: EvidenceItem[] = [...rca.supporting_evidence, ...cicd.filter((e) => !rca.supporting_evidence.some((x) => x.id === e.id))];
  const passed = v.checks.filter((c) => c.passed).length;

  return (
    <Section
      id="report"
      title="Incident report"
      aside={
        <span className="flex gap-2">
          <Button className="px-2 py-1 text-xs" onClick={() => download(`${file}.md`, reportMarkdown(r), "text/markdown")}>
            Download .md
          </Button>
          <Button
            className="px-2 py-1 text-xs"
            onClick={() => download(`${file}.json`, JSON.stringify(report, null, 2), "application/json")}
          >
            JSON
          </Button>
        </span>
      }
    >
      <Panel className="space-y-5 p-5 sm:p-8">
        <header>
          <p className="font-mono text-xs text-muted">{s.reference}</p>
          <h3 className="mt-0.5 text-lg font-semibold text-ink">{s.title}</h3>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <StatusIndicator status={outcome.final_status} />
            <span>Report generated {dateTime(report.created_at)} from the stored results of every phase</span>
          </p>
        </header>

        <Part title="Summary">
          <p className="text-sm text-ink">
            {s.description} {s.service} was affected from detection at {dateTime(s.created_at)}
            {s.resolved_at ? ` until it was resolved at ${dateTime(s.resolved_at)} (${duration(s.created_at, s.resolved_at)}).` : "."}
          </p>
        </Part>

        <Part title="Impact">
          <Facts
            items={[
              ["Severity", s.severity],
              ["Service", <span key="s" className="font-mono">{s.service}</span>],
              ["Time to resolve", duration(s.created_at, s.resolved_at)],
              ["Error rate at detection", <span key="e" className="font-mono">{num(execution.before.error_rate, "%")}</span>],
              ["Latency (p95) at detection", <span key="l" className="font-mono">{num(execution.before.latency_ms, " ms")}</span>],
              ["Status at detection", label(execution.before.status ?? "—")],
            ]}
          />
        </Part>

        <Part title="Timeline">
          <div className={table.wrap}>
            <table className={table.table}>
              <tbody>
                {r.timeline.map((entry, i) => (
                  <tr key={i}>
                    <td className={`${table.td} ${table.mono} w-20 whitespace-nowrap text-muted`}>{time(entry.timestamp)}</td>
                    <td className={table.td}>{entry.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Part>

        <Part title="Root cause">
          <p className="text-sm font-medium text-ink">{rca.root_cause}</p>
          <p className="mt-1 text-xs text-muted">
            {label(rca.category)} · confidence {percent(rca.confidence)}
          </p>
          <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-ink">
            {rca.causal_chain.map((step, i) => (
              <li key={i}>
                {step.statement} <span className="font-mono text-xs text-muted">{step.evidence_ids.join(", ")}</span>
              </li>
            ))}
          </ol>
        </Part>

        <Part title="Evidence">
          <div className={table.wrap}>
            <table className={table.table}>
              <tbody>
                {evidence.map((item) => (
                  <tr key={item.id}>
                    <td className={`${table.td} ${table.mono} w-12 font-medium`}>{item.id}</td>
                    <td className={`${table.td} font-mono text-xs leading-relaxed break-words`}>{item.fact}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Part>

        <Part title="Remediation">
          <p className="text-sm font-medium text-ink">
            {describeAction(remediation.action, remediation.parameters, remediation.target)}
          </p>
          <p className="mt-1 text-xs text-muted">
            Risk <RiskLabel risk={remediation.risk} /> · proposed by the remediation agent, validated by backend policy
          </p>
          <p className="mt-2 text-sm text-ink">{remediation.reason}</p>
        </Part>

        <Part title="Approval">
          <Facts
            items={[
              ["Human decision", label(outcome.human_decision)],
              ["Requested", dateTime(approval.requested_at)],
              ["Decided", dateTime(approval.decided_at)],
            ]}
          />
          <p className="mt-3 text-sm text-ink">
            Executed: {outcome.remediation_performed ?? "nothing"} <span className="text-muted">(simulated)</span>
          </p>
        </Part>

        <Part title="Verification">
          <p className="mb-3 text-sm text-ink">
            {passed} / {v.checks.length} backend checks passed. {v.reasoning_summary}
          </p>
          <SnapshotCompare before={execution.before} after={v.after} />
        </Part>

        <Part title="Final outcome">
          <p className="flex items-center gap-2 text-sm font-semibold">
            <Dot tone={outcome.recovered ? "ok" : "critical"} />
            <span className={outcome.recovered ? "text-emerald-700" : "text-red-700"}>
              {outcome.recovered
                ? `${label(outcome.final_status)}: the approved remediation restored ${s.service}.`
                : `${label(outcome.final_status)}: not recovered; human investigation required.`}
            </span>
          </p>
        </Part>
      </Panel>
    </Section>
  );
}
