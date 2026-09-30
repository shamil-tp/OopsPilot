"use client";

import Link from "next/link";
import { useState } from "react";

import { EventTimeline } from "@/components/incident/EventTimeline";
import { InvestigationPanel } from "@/components/incident/InvestigationPanel";
import { LifecycleStepper } from "@/components/incident/LifecycleStepper";
import { RemediationPanel } from "@/components/incident/RemediationPanel";
import { ReportPanel } from "@/components/incident/ReportPanel";
import { RootCausePanel } from "@/components/incident/RootCausePanel";
import { VerificationPanel } from "@/components/incident/VerificationPanel";
import { Badge, ErrorNote, SeverityBadge, StatusBadge } from "@/components/ui";
import { useIncidentConsole } from "@/hooks/useIncidentConsole";
import { analyze, ApiError, approve, investigate, reject, remediate, verify } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { IncidentData } from "@/hooks/useIncidentConsole";

type Step = "investigate" | "analyze" | "remediate" | "verify";

const STEPS: Record<Step, { label: string; working: string; run: (id: number) => Promise<unknown> }> = {
  investigate: { label: "Start investigation", working: "Investigation agent collecting evidence…", run: investigate },
  analyze: { label: "Run root cause analysis", working: "Root cause agent correlating evidence…", run: analyze },
  remediate: { label: "Propose remediation", working: "Remediation agent choosing a safe action…", run: remediate },
  verify: { label: "Verify recovery", working: "Verification agent checking the service…", run: verify },
};

/** The next backend step for the current state. The backend still validates every request. */
function nextStep(d: IncidentData): Step | null {
  const status = d.incident?.status;
  if (status === "DETECTED") return "investigate";
  if (status === "INVESTIGATING" && d.investigation?.status === "COMPLETED") return "analyze";
  if (status === "ANALYZING" && d.analysis?.status === "COMPLETED" && d.remediation?.status !== "COMPLETED") {
    return "remediate";
  }
  if (status === "VERIFYING") return "verify";
  return null;
}

function stateHint(d: IncidentData): string | null {
  switch (d.incident?.status) {
    case "AWAITING_APPROVAL":
      return "Waiting for a human decision on the recommended action below.";
    case "REMEDIATING":
      return "Executing the approved remediation…";
    case "RESOLVED":
      return "Resolved — recovery verified by the backend checks.";
    case "FAILED":
      return "The incident is FAILED and needs a human (see the timeline and panels).";
    case "ESCALATED":
      return "Escalated to a human. Nothing was executed.";
    case "ANALYZING":
      return d.remediation?.result?.status === "no_action" ? "The remediation agent recommends no action." : null;
    default:
      return null;
  }
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.status ? `${err.message} (HTTP ${err.status})` : err.message;
  return "Unexpected error";
}

export function IncidentConsole({ incidentId }: { incidentId: number }) {
  const { data, error, loading, mode, refresh } = useIncidentConsole(incidentId);
  const [running, setRunning] = useState<Step | null>(null);
  const [stepError, setStepError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<"approve" | "reject" | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const incident = data.incident;
  if (loading && !incident) {
    return <p className="text-sm text-slate-400">Loading incident…</p>;
  }
  if (!incident) {
    return <ErrorNote>{error ?? "Incident not found"}</ErrorNote>;
  }

  const step = nextStep(data);
  const hint = stateHint(data);
  const evidence = data.investigation?.result?.evidence ?? [];

  async function runStep(s: Step) {
    setRunning(s);
    setStepError(null);
    try {
      await STEPS[s].run(incidentId);
    } catch (err) {
      setStepError(errorText(err));
    } finally {
      setRunning(null);
      await refresh();
    }
  }

  async function decide(decision: "approve" | "reject") {
    setDeciding(decision);
    setDecisionError(null);
    try {
      await (decision === "approve" ? approve : reject)(incidentId);
    } catch (err) {
      setDecisionError(errorText(err));
    } finally {
      setDeciding(null);
      await refresh();
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-3">
        <Link href="/" className="text-xs text-slate-500 hover:text-cyan-300">
          ← Dashboard
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-sm text-cyan-300">{incident.reference}</span>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-50">{incident.title}</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
          <StatusBadge status={incident.status} />
          <SeverityBadge severity={incident.severity} />
          <Badge>{incident.service_name}</Badge>
          <span>Detected {dateTime(incident.created_at)}</span>
        </div>
        <p className="text-sm text-slate-400">{incident.description}</p>
      </header>

      <LifecycleStepper status={incident.status} />

      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        {step && (
          <button
            type="button"
            disabled={running !== null}
            onClick={() => void runStep(step)}
            className="rounded-md bg-cyan-400 px-4 py-2 text-sm font-semibold text-slate-950 shadow-[0_0_20px] shadow-cyan-400/20 hover:bg-cyan-300 disabled:cursor-wait disabled:opacity-60"
          >
            {running ? STEPS[running].working : STEPS[step].label}
          </button>
        )}
        {running && <span className="text-xs text-slate-500">One AI call; this can take up to ~30 s. Watch the timeline.</span>}
        {!step && hint && <p className="text-sm text-slate-300">{hint}</p>}
        {stepError && <ErrorNote>{stepError}</ErrorNote>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>

      {data.report && <ReportPanel report={data.report} />}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex min-w-0 flex-col gap-6">
          <InvestigationPanel run={data.investigation} />
          <RootCausePanel run={data.analysis} evidence={evidence} />
          <RemediationPanel
            run={data.remediation}
            execution={data.execution}
            incidentStatus={incident.status}
            evidence={evidence}
            busy={deciding}
            error={decisionError}
            onDecide={(d) => void decide(d)}
          />
          <VerificationPanel run={data.verification} />
        </div>
        <div className="lg:sticky lg:top-6 lg:self-start">
          <EventTimeline events={data.events} mode={mode} />
        </div>
      </div>
    </div>
  );
}
