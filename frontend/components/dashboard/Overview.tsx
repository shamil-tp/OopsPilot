"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { CicdTable } from "@/components/cicd/CicdTable";
import { IncidentTable } from "@/components/incidents/IncidentTable";
import {
  Button,
  Dot,
  Empty,
  ErrorNote,
  Indicator,
  PageHeader,
  Panel,
  Section,
  ServiceStatusIndicator,
  StatusIndicator,
  table,
  type Tone,
} from "@/components/ui";
import {
  ApiError,
  getServiceHealth,
  getWebhookStatus,
  listCicdEvents,
  listDeployments,
  listIncidents,
  listServices,
  resetDemo,
  simulateIncident,
} from "@/lib/api";
import { label, num, shortDateTime, TERMINAL } from "@/lib/format";
import type {
  CicdEvent,
  Deployment,
  DeploymentStatus,
  Incident,
  ServiceHealth,
  ServiceSummary,
  WebhookStatus,
} from "@/types/api";

const REFRESH_MS = 5000;
// The simulated service thresholds (backend app/services/scenario.py).
const ERROR_RATE_LIMIT = 5;
const LATENCY_LIMIT_MS = 500;

interface Snapshot {
  incidents: Incident[];
  services: ServiceSummary[];
  health: Record<string, ServiceHealth | null>;
  deployments: Deployment[];
  cicd: CicdEvent[];
  webhook: WebhookStatus;
}

async function loadSnapshot(): Promise<Snapshot> {
  const [incidents, services] = await Promise.all([listIncidents(), listServices()]);
  const [healthList, deployments, cicd, webhook] = await Promise.all([
    Promise.all(services.map((s) => getServiceHealth(s.name))),
    listDeployments("payment-api"),
    listCicdEvents({ limit: 6 }),
    getWebhookStatus(),
  ]);
  const health = Object.fromEntries(services.map((s, i) => [s.name, healthList[i]]));
  return { incidents, services, health, deployments, cicd, webhook };
}

const DEPLOYMENT_TONE: Record<DeploymentStatus, Tone> = {
  SUCCEEDED: "neutral",
  IN_PROGRESS: "info",
  FAILED: "critical",
  ROLLED_BACK: "warn",
};

function ProductionStatus({ snapshot }: { snapshot: Snapshot }) {
  const active = snapshot.incidents.filter((i) => !TERMINAL.includes(i.status));
  const degraded = snapshot.services.filter((s) => snapshot.health[s.name]?.status !== "HEALTHY");
  const current = snapshot.deployments.find((d) => d.status === "SUCCEEDED");

  let tone: Tone = "ok";
  let headline = "All services operational";
  if (active.length > 0) {
    tone = "critical";
    headline = `${active.length} active incident${active.length === 1 ? "" : "s"}`;
  } else if (degraded.length > 0) {
    tone = "warn";
    headline = `${degraded.map((s) => s.name).join(", ")} degraded`;
  }

  const facts = [
    { term: "Active incidents", value: String(active.length) },
    { term: "Healthy services", value: `${snapshot.services.length - degraded.length} / ${snapshot.services.length}` },
    { term: "payment-api version", value: current?.version ?? "—", mono: true },
    { term: "Last deployment", value: shortDateTime(snapshot.deployments[0]?.timestamp) },
  ];

  return (
    <Panel className="px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1" role="status" aria-live="polite">
        <Dot tone={tone} className="h-2.5 w-2.5" />
        <p className="text-base font-semibold text-ink">{headline}</p>
        {active[0] && (
          <p className="text-sm text-muted">
            <Link href={`/incidents/${active[0].id}`} className="font-mono text-ink underline underline-offset-2">
              {active[0].reference}
            </Link>{" "}
            · {active[0].service_name} · <StatusIndicator status={active[0].status} />
          </p>
        )}
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-line pt-4 sm:grid-cols-4">
        {facts.map((fact) => (
          <div key={fact.term}>
            <dt className="text-xs text-muted">{fact.term}</dt>
            <dd className={`mt-0.5 text-[15px] font-medium text-ink ${fact.mono ? "font-mono" : ""}`}>{fact.value}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

export function Overview() {
  const router = useRouter();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"simulate" | "reset" | null>(null);

  const apply = useCallback((result: Snapshot | Error) => {
    if (result instanceof Error) setError(result instanceof ApiError ? result.message : "Unexpected error");
    else {
      setSnapshot(result);
      setError(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      loadSnapshot()
        .catch((err: unknown) => (err instanceof Error ? err : new Error(String(err))))
        .then((result) => {
          if (!cancelled) apply(result);
        });
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [apply]);

  async function simulate() {
    setBusy("simulate");
    try {
      const incident = await simulateIncident();
      router.push(`/incidents/${incident.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not simulate the incident");
      setBusy(null);
    }
  }

  async function reset() {
    setBusy("reset");
    try {
      await resetDemo();
      apply(await loadSnapshot());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reset the demo");
    } finally {
      setBusy(null);
    }
  }

  const incidents = snapshot
    ? [...snapshot.incidents].sort(
        (a, b) => Number(TERMINAL.includes(a.status)) - Number(TERMINAL.includes(b.status)),
      )
    : [];

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <PageHeader
          title="Overview"
          description="Simulated production environment: payment-api, auth-api and database."
          actions={
            <>
              <Button variant="quiet" disabled={busy !== null} onClick={() => void reset()}>
                {busy === "reset" ? "Resetting…" : "Reset demo"}
              </Button>
              <Button variant="primary" disabled={busy !== null} onClick={() => void simulate()}>
                {busy === "simulate" ? "Simulating…" : "Simulate incident"}
              </Button>
            </>
          }
        />
        <p className="max-w-3xl text-xs text-muted">
          Simulate incident replays the GitHub push and deploy-production run that ship payment-api v1.8.2, then the
          failure that follows. Each agent step is started from the incident page, and the rollback waits for human
          approval.
        </p>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>

      {!snapshot ? (
        <Empty>Loading environment…</Empty>
      ) : (
        <>
          <ProductionStatus snapshot={snapshot} />

          <Section
            title="Incidents"
            aside={
              snapshot.incidents.length > 5 && (
                <Link href="/incidents" className="hover:text-ink">
                  View all {snapshot.incidents.length} →
                </Link>
              )
            }
          >
            <IncidentTable incidents={incidents.slice(0, 5)} empty="No incidents. Simulate incident to start the demo." />
          </Section>

          <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
            <Section title="Services">
              <div className={table.wrap}>
                <table className={table.table}>
                  <thead>
                    <tr>
                      <th scope="col" className={table.th}>Service</th>
                      <th scope="col" className={table.th}>Status</th>
                      <th scope="col" className={`${table.th} text-right`}>Error rate</th>
                      <th scope="col" className={`${table.th} text-right`}>Latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.services.map((service) => {
                      const h = snapshot.health[service.name];
                      return (
                        <tr key={service.name}>
                          <td className={`${table.td} ${table.mono}`}>{service.name}</td>
                          <td className={table.td}>
                            <ServiceStatusIndicator status={h?.status ?? null} />
                          </td>
                          <td
                            className={`${table.td} ${table.mono} text-right ${h && h.error_rate >= ERROR_RATE_LIMIT ? "text-red-700" : ""}`}
                          >
                            {num(h?.error_rate, "%")}
                          </td>
                          <td
                            className={`${table.td} ${table.mono} text-right ${h && h.latency_ms >= LATENCY_LIMIT_MS ? "text-red-700" : ""}`}
                          >
                            {num(h?.latency_ms, " ms")}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Section>

            <Section title="Deployments" aside="payment-api">
              <div className={table.wrap}>
                <table className={table.table}>
                  <thead>
                    <tr>
                      <th scope="col" className={table.th}>Version</th>
                      <th scope="col" className={`${table.th} hidden sm:table-cell`}>Commit</th>
                      <th scope="col" className={table.th}>Status</th>
                      <th scope="col" className={`${table.th} text-right`}>Deployed (UTC)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.deployments.map((d, i) => {
                      const active = i === snapshot.deployments.findIndex((x) => x.status === "SUCCEEDED");
                      return (
                        <tr key={`${d.version}-${d.timestamp}`}>
                          <td className={`${table.td} ${table.mono} font-medium`}>{d.version}</td>
                          <td className={`${table.td} ${table.mono} hidden text-muted sm:table-cell`}>
                            {d.commit_sha?.slice(0, 7) ?? "—"}
                          </td>
                          <td className={table.td}>
                            {active ? (
                              <Indicator tone="ok">Active</Indicator>
                            ) : (
                              <Indicator tone={DEPLOYMENT_TONE[d.status]}>{label(d.status)}</Indicator>
                            )}
                          </td>
                          <td className={`${table.td} text-right whitespace-nowrap text-muted`}>
                            {shortDateTime(d.timestamp)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Section>
          </div>

          <Section
            title="GitHub activity"
            aside={
              <Indicator tone={snapshot.webhook.configured ? "ok" : "neutral"}>
                {snapshot.webhook.configured ? "Webhook configured" : "Webhook not configured"}
              </Indicator>
            }
          >
            {snapshot.cicd.length === 0 ? (
              <Empty>
                No CI/CD events yet. Simulate incident replays the GitHub deliveries of the demo; a configured
                repository delivers to /api/webhooks/github.
              </Empty>
            ) : (
              <CicdTable events={snapshot.cicd} />
            )}
          </Section>
        </>
      )}
    </div>
  );
}
