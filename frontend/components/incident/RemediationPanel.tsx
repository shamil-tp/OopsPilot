"use client";

import { motion } from "framer-motion";

import { SnapshotCompare } from "@/components/incident/SnapshotCompare";
import { Badge, Card, Empty, ErrorNote, EvidenceRefs, evidenceIndex, RiskBadge } from "@/components/ui";
import { dateTime, describeAction, label, percent, str } from "@/lib/format";
import type { EvidenceItem, ExecutionRun, IncidentStatus, RemediationRun } from "@/types/api";

type Decision = "approve" | "reject";

export function RemediationPanel({
  run,
  execution,
  incidentStatus,
  evidence,
  busy,
  error,
  onDecide,
}: {
  run: RemediationRun | null;
  execution: ExecutionRun | null;
  incidentStatus: IncidentStatus;
  evidence: EvidenceItem[];
  busy: Decision | null;
  error: string | null;
  onDecide: (decision: Decision) => void;
}) {
  const result = run?.result;
  if (!run || !result) {
    return (
      <Card eyebrow="Remediation agent" title="Recommended action">
        <Empty>{run?.status === "FAILED" ? run.summary : "No remediation proposed yet."}</Empty>
      </Card>
    );
  }
  const approval = run.approval;
  const sentence = describeAction(result.action, result.parameters, result.target);
  const index = evidenceIndex(evidence, result.supporting_evidence);
  const pending = approval?.status === "PENDING" && incidentStatus === "AWAITING_APPROVAL";

  return (
    <Card
      eyebrow="Remediation agent"
      title="Recommended action"
      actions={
        <div className="flex gap-1.5">
          <RiskBadge risk={result.risk} />
          {result.requires_approval && <Badge tone="amber">Human approval required</Badge>}
        </div>
      }
    >
      <p className="mb-1 font-mono text-lg text-slate-50">{sentence}</p>
      <p className="mb-3 text-xs text-slate-500">
        {label(result.action)} · target {result.target} · confidence {percent(result.confidence)} · risk and
        approval set by backend policy
      </p>
      <p className="mb-3 text-sm text-slate-300">
        {result.reason} <EvidenceRefs ids={result.supporting_evidence.map((e) => e.id)} evidence={index} />
      </p>

      {approval && (
        <motion.div
          layout
          className={`mb-4 rounded-lg border p-4 ${
            pending ? "border-amber-400/40 bg-amber-400/5" : "border-slate-800 bg-slate-950/50"
          }`}
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-[11px] font-semibold tracking-widest text-slate-400 uppercase">Human approval</p>
            <Badge
              tone={approval.status === "APPROVED" ? "emerald" : approval.status === "REJECTED" ? "rose" : "amber"}
            >
              {approval.status}
            </Badge>
          </div>
          <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs text-slate-300">
            {Object.entries(approval.parameters)
              .filter(([key]) => key !== "remediation_run_id")
              .map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="text-slate-500">{key}</dt>
                  <dd>{str(value)}</dd>
                </div>
              ))}
          </dl>
          {pending ? (
            <>
              <p className="mb-3 text-sm text-amber-200">
                Approve <span className="font-semibold">{sentence}</span>? The backend executes exactly these
                stored parameters.
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => onDecide("approve")}
                  className="rounded-md bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {busy === "approve" ? "Approving & executing…" : "Approve"}
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => onDecide("reject")}
                  className="rounded-md border border-rose-500/50 px-4 py-2 text-sm font-semibold text-rose-300 hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {busy === "reject" ? "Rejecting…" : "Reject"}
                </button>
              </div>
            </>
          ) : (
            <p className="text-xs text-slate-500">
              Requested {dateTime(approval.requested_at)}
              {approval.decided_at && ` · decided ${dateTime(approval.decided_at)}`}
            </p>
          )}
          {error && (
            <div className="mt-3">
              <ErrorNote>{error}</ErrorNote>
            </div>
          )}
        </motion.div>
      )}

      {execution && (
        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-[11px] font-semibold tracking-widest text-slate-400 uppercase">Execution</p>
            <Badge tone={execution.status === "COMPLETED" ? "emerald" : execution.status === "FAILED" ? "rose" : "cyan"}>
              {execution.status}
            </Badge>
          </div>
          {execution.result ? (
            <>
              <p className="mb-2 text-sm text-slate-300">
                {execution.summary} <span className="text-slate-500">(simulated)</span>
              </p>
              <SnapshotCompare before={execution.result.before} after={execution.result.after} />
            </>
          ) : (
            <p className="text-sm text-slate-400">{execution.summary ?? "Executing…"}</p>
          )}
        </div>
      )}
    </Card>
  );
}
