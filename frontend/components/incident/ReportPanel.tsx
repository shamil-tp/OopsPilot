"use client";

import { motion } from "framer-motion";

import { SnapshotCompare } from "@/components/incident/SnapshotCompare";
import { Badge, Card, SeverityBadge, StatusBadge } from "@/components/ui";
import { dateTime, describeAction, label, percent, time } from "@/lib/format";
import { download, reportMarkdown } from "@/lib/report-markdown";
import type { IncidentReport } from "@/types/api";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-slate-800 pt-4">
      <h3 className="mb-2 text-[11px] font-semibold tracking-widest text-cyan-400 uppercase">{title}</h3>
      {children}
    </div>
  );
}

export function ReportPanel({ report }: { report: IncidentReport }) {
  const r = report.report;
  const { summary: s, root_cause: rca, remediation, approval, execution, verification: v, outcome } = r;
  const file = `${s.reference}-incident-report`;

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
      <Card
        eyebrow="Final incident report"
        title={`${s.reference} · ${s.title}`}
        className={outcome.recovered ? "border-emerald-500/30" : "border-rose-500/30"}
        actions={
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => download(`${file}.md`, reportMarkdown(r), "text/markdown")}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:border-cyan-400 hover:text-cyan-200"
            >
              Download report (.md)
            </button>
            <button
              type="button"
              onClick={() => download(`${file}.json`, JSON.stringify(report, null, 2), "application/json")}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-400 hover:border-slate-500 hover:text-slate-200"
            >
              JSON
            </button>
          </div>
        }
      >
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <StatusBadge status={outcome.final_status} />
          <SeverityBadge severity={s.severity} />
          <Badge>{s.service}</Badge>
          <Badge tone={outcome.recovered ? "emerald" : "rose"}>{label(outcome.recovery_status)}</Badge>
          <span className="text-xs text-slate-500">
            Detected {dateTime(s.created_at)} · Resolved {dateTime(s.resolved_at)}
          </span>
        </div>

        <div className="space-y-4">
          <Section title="Root cause">
            <p className="text-sm text-slate-100">{rca.root_cause}</p>
            <p className="mt-1 text-xs text-slate-500">
              {label(rca.category)} · confidence {percent(rca.confidence)} · evidence{" "}
              {rca.supporting_evidence.map((e) => e.id).join(", ")}
            </p>
          </Section>

          <Section title="Remediation & human decision">
            <p className="font-mono text-sm text-slate-100">
              {describeAction(remediation.action, remediation.parameters, remediation.target)}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Risk {remediation.risk} · approval {approval.status}
              {approval.decided_at && ` at ${dateTime(approval.decided_at)}`} · executed:{" "}
              {outcome.remediation_performed} (simulated)
            </p>
          </Section>

          <Section title="Recovery">
            <SnapshotCompare before={execution.before} after={v.after} />
            <p className="mt-2 text-sm text-slate-300">{v.reasoning_summary}</p>
            <p className="mt-1 text-xs text-slate-500">
              {outcome.verification} · {v.checks.filter((c) => c.passed).length}/{v.checks.length} checks passed
            </p>
          </Section>

          <Section title="Timeline">
            <ol className="space-y-1">
              {r.timeline.map((entry, i) => (
                <li key={i} className="flex gap-3 text-xs">
                  <span className="w-16 shrink-0 font-mono text-slate-500">{time(entry.timestamp)}</span>
                  <span className="text-slate-300">{entry.message}</span>
                </li>
              ))}
            </ol>
          </Section>

          <Section title="Final outcome">
            <p className={`text-sm font-semibold ${outcome.recovered ? "text-emerald-300" : "text-rose-300"}`}>
              {outcome.recovered
                ? `Recovered and ${outcome.final_status}: the approved remediation restored ${s.service}.`
                : `Not recovered (${outcome.final_status}): human investigation required.`}
            </p>
          </Section>
        </div>
      </Card>
    </motion.div>
  );
}
