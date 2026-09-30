"use client";

import { SnapshotCompare } from "@/components/incident/SnapshotCompare";
import {
  Button,
  Empty,
  ErrorNote,
  EvidenceRefs,
  evidenceIndex,
  Indicator,
  RiskLabel,
  Section,
  Subheading,
  type Tone,
} from "@/components/ui";
import { dateTime, describeAction, label, percent, str } from "@/lib/format";
import type { ActionType, ApprovalStatus, EvidenceItem, ExecutionRun, IncidentStatus, RemediationRun } from "@/types/api";

type Decision = "approve" | "reject";

const APPROVAL_TONE: Record<ApprovalStatus, Tone> = { PENDING: "warn", APPROVED: "ok", REJECTED: "critical", EXPIRED: "neutral" };
const APPROVE_LABEL: Partial<Record<ActionType, string>> = {
  ROLLBACK_DEPLOYMENT: "Approve rollback",
  RESTART_SERVICE: "Approve restart",
};

function Field({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted">{term}</dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

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
      <Section id="remediation" title="Remediation">
        <Empty>
          {run?.status === "FAILED"
            ? run.summary
            : run?.status === "RUNNING"
              ? "Choosing a safe action…"
              : "Proposed after root cause analysis. Risky actions wait for human approval."}
        </Empty>
      </Section>
    );
  }
  const approval = run.approval;
  const sentence = describeAction(result.action, result.parameters, result.target);
  const index = evidenceIndex(evidence, result.supporting_evidence);
  const pending = approval?.status === "PENDING" && incidentStatus === "AWAITING_APPROVAL";
  const params = approval?.parameters ?? result.parameters;
  const service = str(params.service ?? result.target);

  return (
    <Section id="remediation" title="Remediation" aside="Risk and approval are set by backend policy">
      <p className="text-base font-medium text-ink">{sentence}</p>
      <p className="mt-1 text-xs text-muted">
        {label(result.action)} · risk <RiskLabel risk={result.risk} /> ·{" "}
        {result.requires_approval ? "human approval required" : "no approval needed"} · confidence{" "}
        {percent(result.confidence)}
      </p>
      <p className="mt-2 max-w-3xl text-sm text-ink">
        {result.reason} <EvidenceRefs ids={result.supporting_evidence.map((e) => e.id)} evidence={index} />
      </p>

      {approval && (
        <div
          className={`mt-5 rounded-md border bg-panel p-4 sm:p-5 ${pending ? "border-amber-400 border-l-4" : "border-line"}`}
          aria-labelledby="approval-title"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 id="approval-title" className="text-sm font-semibold text-ink">
              Remediation approval
            </h3>
            <Indicator tone={APPROVAL_TONE[approval.status]}>
              {approval.status === "PENDING" ? "Waiting for approval" : label(approval.status)}
            </Indicator>
          </div>

          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-5">
            <Field term="Action">{label(approval.action_type)}</Field>
            <Field term="Service">
              <span className="font-mono">{service}</span>
            </Field>
            {"from_version" in params && (
              <Field term="Current version">
                <span className="font-mono">{str(params.from_version)}</span>
              </Field>
            )}
            {"to_version" in params && (
              <Field term="Target version">
                <span className="font-mono">{str(params.to_version)}</span>
              </Field>
            )}
            <Field term="Risk">
              <RiskLabel risk={approval.risk} />
            </Field>
          </dl>

          {pending ? (
            <>
              <p className="mt-4 text-sm text-ink">
                {result.action === "ROLLBACK_DEPLOYMENT"
                  ? `This action will modify the active deployment of ${service}.`
                  : `This action will change ${service}.`}{" "}
                <span className="text-muted">
                  The backend executes exactly these stored parameters; they cannot be edited here.
                </span>
              </p>
              <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-between">
                <Button disabled={busy !== null} onClick={() => onDecide("reject")}>
                  {busy === "reject" ? "Rejecting…" : "Reject"}
                </Button>
                <Button variant="primary" disabled={busy !== null} onClick={() => onDecide("approve")}>
                  {busy === "approve" ? "Approving and executing…" : (APPROVE_LABEL[result.action] ?? "Approve")}
                </Button>
              </div>
            </>
          ) : (
            <p className="mt-4 text-xs text-muted">
              Requested {dateTime(approval.requested_at)}
              {approval.decided_at && ` · decided ${dateTime(approval.decided_at)}`}
            </p>
          )}
          {error && (
            <div className="mt-3">
              <ErrorNote>{error}</ErrorNote>
            </div>
          )}
        </div>
      )}

      {execution && (
        <>
          <Subheading>Execution</Subheading>
          <p className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink">
            <Indicator tone={execution.status === "COMPLETED" ? "ok" : execution.status === "FAILED" ? "critical" : "info"}>
              {label(execution.status)}
            </Indicator>
            <span>
              {execution.summary ?? "Executing…"} <span className="text-muted">(simulated)</span>
            </span>
          </p>
          {execution.result && <SnapshotCompare before={execution.result.before} after={execution.result.after} />}
        </>
      )}
    </Section>
  );
}
