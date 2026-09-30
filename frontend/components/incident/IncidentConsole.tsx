"use client";

import Link from "next/link";
import { useState } from "react";

import { CicdEvidencePanel } from "@/components/incident/CicdEvidencePanel";
import { EventTimeline } from "@/components/incident/EventTimeline";
import { InvestigationPanel } from "@/components/incident/InvestigationPanel";
import { LifecycleStepper } from "@/components/incident/LifecycleStepper";
import { RemediationPanel } from "@/components/incident/RemediationPanel";
import { ReportPanel } from "@/components/incident/ReportPanel";
import { RootCausePanel } from "@/components/incident/RootCausePanel";
import { VerificationPanel } from "@/components/incident/VerificationPanel";
import { Button, Empty, ErrorNote, Indicator, SeverityLabel, StatusIndicator } from "@/components/ui";
import { type IncidentData, type LiveMode, useIncidentConsole } from "@/hooks/useIncidentConsole";
import { analyze, ApiError, approve, investigate, reject, remediate, verify } from "@/lib/api";
import { dateTime } from "@/lib/format";

type Step = "investigate" | "analyze" | "remediate" | "verify";

const STEPS: Record<Step, { label: string; working: string; about: string; run: (id: number) => Promise<unknown> }> = {
  investigate: {
    label: "Start investigation",
    working: "Investigating…",
    about: "Collect evidence from logs, health, deployments, previous incidents and GitHub, then analyse it.",
    run: investigate,
  },
  analyze: {
    label: "Run root cause analysis",
    working: "Analysing…",
    about: "Correlate the stored evidence into the most likely root cause.",
    run: analyze,
  },
  remediate: {
    label: "Propose remediation",
    working: "Proposing…",
    about: "Propose a safe remediation. Risky actions wait for human approval.",
    run: remediate,
  },
  verify: {
    label: "Verify recovery",
    working: "Verifying…",
    about: "Check whether the service recovered after the approved remediation.",
    run: verify,
  },
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
      return "Waiting for a human decision on the proposed remediation.";
    case "REMEDIATING":
      return "Executing the approved remediation…";
    case "RESOLVED":
      return "Resolved. Recovery was verified by the backend checks.";
    case "FAILED":
      return "Failed. Recovery could not be verified; see the timeline and the verification checks.";
    case "ESCALATED":
      return "Escalated to a human. Nothing was executed.";
    case "ANALYZING":
      return d.remediation?.result?.status === "no_action" ? "The remediation agent recommends no action." : null;
    default:
      return null;
  }
}

const LIVE: Record<LiveMode, { tone: "ok" | "warn" | "neutral"; text: string; title: string }> = {
  live: { tone: "ok", text: "Live", title: "Receiving live updates over WebSocket" },
  polling: { tone: "warn", text: "Polling", title: "Live connection lost; refreshing every 3 s and reconnecting" },
  connecting: { tone: "neutral", text: "Connecting", title: "Connecting to live updates" },
};

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
  if (loading && !incident) return <Empty>Loading incident…</Empty>;
  if (!incident) return <ErrorNote>{error ?? "Incident not found"}</ErrorNote>;

  const step = nextStep(data);
  const hint = stateHint(data);
  const evidence = data.investigation?.result?.evidence ?? [];
  const live = LIVE[mode];

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
      <nav aria-label="Breadcrumb" className="text-xs text-muted">
        <Link href="/incidents" className="hover:text-ink">
          Incidents
        </Link>
        <span aria-hidden className="mx-1.5">/</span>
        <span className="font-mono text-ink">{incident.reference}</span>
      </nav>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted">{incident.reference}</p>
          <h1 className="mt-0.5 text-xl font-semibold tracking-tight text-ink">{incident.title}</h1>
          <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <SeverityLabel severity={incident.severity} />
            <span aria-hidden className="text-line-strong">|</span>
            <StatusIndicator status={incident.status} />
            <span aria-hidden className="text-line-strong">|</span>
            <span className="font-mono text-[13px]">{incident.service_name}</span>
            <span aria-hidden className="text-line-strong">|</span>
            <span className="text-muted">Started {dateTime(incident.created_at)}</span>
          </p>
          <p className="mt-2 max-w-3xl text-sm text-muted">{incident.description}</p>
        </div>
        <span title={live.title} className="text-xs">
          <Indicator tone={live.tone}>{live.text}</Indicator>
        </span>
      </header>

      <LifecycleStepper status={incident.status} />

      <div
        className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-panel px-4 py-3"
        role="status"
        aria-live="polite"
      >
        <p className="min-w-0 text-sm text-ink">
          {step ? (
            <>
              <span className="font-medium">Next: </span>
              {running ? `${STEPS[running].working} One AI call, usually 5–15 s.` : STEPS[step].about}
            </>
          ) : (
            (hint ?? "No action available in this state.")
          )}
        </p>
        {step ? (
          <Button variant="primary" disabled={running !== null} onClick={() => void runStep(step)}>
            {running ? STEPS[running].working : STEPS[step].label}
          </Button>
        ) : (
          data.report && (
            <a href="#report" className="text-sm font-medium text-ink underline underline-offset-2">
              Read the incident report
            </a>
          )
        )}
      </div>
      {stepError && <ErrorNote>{stepError}</ErrorNote>}
      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_19rem]">
        <aside className="lg:sticky lg:top-16 lg:col-start-2 lg:row-start-1 lg:self-start">
          <EventTimeline events={data.events} />
        </aside>
        <div className="flex min-w-0 flex-col gap-10 lg:col-start-1 lg:row-start-1">
          <CicdEvidencePanel cicd={data.cicd} investigation={data.investigation} detectedAt={incident.created_at} />
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
          {data.report && <ReportPanel report={data.report} />}
        </div>
      </div>
    </div>
  );
}
