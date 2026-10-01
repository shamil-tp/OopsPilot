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
  getProjects,
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
  Projects,
  ServiceHealth,
  ServiceSummary,
  WebhookStatus,
} from "@/types/api";

const REFRESH_MS = 5000;
// The simulated service thresholds (backend app/services/scenario.py).
const ERROR_RATE_LIMIT = 5;
const LATENCY_LIMIT_MS = 500;

interface Snapshot {
  /** Null when the backend has no project support: treated as demo only. */
  projects: Projects | null;
  incidents: Incident[];
  services: ServiceSummary[];
  health: Record<string, ServiceHealth | null>;
  /** Newest first, per service. */
  deployments: Record<string, Deployment[]>;
  cicd: CicdEvent[];
  webhook: WebhookStatus;
}

async function loadSnapshot(): Promise<Snapshot> {
  const [incidents, services, projects] = await Promise.all([listIncidents(), listServices(), getProjects()]);
  const real = services.filter((s) => s.kind === "real");
  // Deployment history: every real project, or the demo's payment-api when there is none.
  const tracked = real.length > 0 ? real.map((s) => s.name) : services.slice(0, 1).map((s) => s.name);
  const [healthList, deploymentLists, cicd, webhook] = await Promise.all([
    Promise.all(services.map((s) => getServiceHealth(s.name))),
    Promise.all(tracked.map((name) => listDeployments(name))),
    listCicdEvents({ limit: 8 }),
    getWebhookStatus(),
  ]);
  return {
    projects,
    incidents,
    services,
    health: Object.fromEntries(services.map((s, i) => [s.name, healthList[i]])),
    deployments: Object.fromEntries(tracked.map((name, i) => [name, deploymentLists[i]])),
    cicd,
    webhook,
  };
}

const DEPLOYMENT_TONE: Record<DeploymentStatus, Tone> = {
  SUCCEEDED: "neutral",
  IN_PROGRESS: "info",
  FAILED: "critical",
  ROLLED_BACK: "warn",
};

const currentVersion = (deployments: Deployment[] | undefined) =>
  deployments?.find((d) => d.status === "SUCCEEDED")?.version ?? "—";

function ProductionStatus({ snapshot }: { snapshot: Snapshot }) {
  const active = snapshot.incidents.filter((i) => !TERMINAL.includes(i.status));
  const degraded = snapshot.services.filter((s) => snapshot.health[s.name]?.status !== "HEALTHY");
  const projectCount = snapshot.services.filter((s) => s.kind === "real").length;
  const allDeployments = Object.values(snapshot.deployments).flat();
  const latest = allDeployments.sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))[0];
  const [onlyService] = Object.keys(snapshot.deployments);

  let tone: Tone = "ok";
  let headline = "All services operational";
  if (active.length > 0) {
    tone = "critical";
    headline = `${active.length} active incident${active.length === 1 ? "" : "s"}`;
  } else if (degraded.length > 0) {
    tone = "warn";
    headline = `${degraded.map((s) => (s.kind === "real" ? s.display_name : s.name)).join(", ")} degraded`;
  }

  const facts = [
    { term: "Active incidents", value: String(active.length) },
    { term: "Healthy services", value: `${snapshot.services.length - degraded.length} / ${snapshot.services.length}` },
    projectCount > 1
      ? { term: "Projects", value: String(projectCount) }
      : { term: `${onlyService ?? "Service"} version`, value: currentVersion(snapshot.deployments[onlyService]), mono: true },
    { term: "Last deployment", value: latest ? `${shortDateTime(latest.timestamp)} · ${latest.service_name}` : "—" },
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
          <div key={fact.term} className="min-w-0">
            <dt className="text-xs text-muted">{fact.term}</dt>
            <dd className={`mt-0.5 truncate text-[15px] font-medium text-ink ${fact.mono ? "font-mono" : ""}`}>
              {fact.value}
            </dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

const host = (url: string) => url.replace(/^https?:\/\//, "").replace(/\/$/, "");

function ProjectsTable({ snapshot }: { snapshot: Snapshot }) {
  const real = snapshot.services.filter((s) => s.kind === "real");
  const repos = new Map((snapshot.projects?.projects ?? []).map((p) => [p.service, p.repository]));
  return (
    <div className={table.wrap}>
      <table className={table.table}>
        <thead>
          <tr>
            <th scope="col" className={table.th}>Project</th>
            <th scope="col" className={table.th}>Status</th>
            <th scope="col" className={`${table.th} text-right`}>Latency</th>
            <th scope="col" className={`${table.th} hidden text-right sm:table-cell`}>Failed checks</th>
            <th scope="col" className={`${table.th} hidden md:table-cell`}>Version</th>
            <th scope="col" className={`${table.th} hidden text-right lg:table-cell`}>Last deployed (UTC)</th>
          </tr>
        </thead>
        <tbody>
          {real.map((service) => {
            const h = snapshot.health[service.name];
            const deployments = snapshot.deployments[service.name];
            const repo = repos.get(service.name);
            return (
              <tr key={service.name}>
                <td className={table.td}>
                  <span className="font-medium text-ink">{service.display_name}</span>
                  <span className="block text-xs text-muted">
                    {service.url ? (
                      <a href={service.url} target="_blank" rel="noreferrer" className="hover:text-ink hover:underline">
                        {host(service.url)}
                      </a>
                    ) : (
                      <span className="font-mono">{service.name}</span>
                    )}
                    {repo && (
                      <>
                        {" · "}
                        <a
                          href={`https://github.com/${repo}`}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono hover:text-ink hover:underline"
                        >
                          {repo}
                        </a>
                      </>
                    )}
                  </span>
                </td>
                <td className={table.td}>
                  {h ? <ServiceStatusIndicator status={h.status} /> : <Indicator tone="neutral">Not checked yet</Indicator>}
                </td>
                <td className={`${table.td} ${table.mono} text-right whitespace-nowrap`}>{num(h?.latency_ms, " ms")}</td>
                <td className={`${table.td} ${table.mono} hidden text-right sm:table-cell ${h && h.error_rate > 0 ? "text-red-700" : ""}`}>
                  {num(h?.error_rate, "%")}
                </td>
                <td className={`${table.td} ${table.mono} hidden md:table-cell`}>{currentVersion(deployments)}</td>
                <td className={`${table.td} hidden text-right whitespace-nowrap text-muted lg:table-cell`}>
                  {shortDateTime(deployments?.[0]?.timestamp)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
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

  const projects = snapshot?.projects?.projects ?? [];
  // Until the first load, keep demo controls hidden; a backend without project support is demo-only.
  const demoMode = snapshot ? (snapshot.projects?.demo_mode ?? true) : false;
  const demoServices = snapshot?.services.filter((s) => s.kind === "demo") ?? [];
  const deploymentRows = snapshot
    ? Object.values(snapshot.deployments)
        .flat()
        .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
        .slice(0, 8)
    : [];
  const severalServices = snapshot ? Object.keys(snapshot.deployments).length > 1 : false;
  const repositories = new Set(snapshot?.cicd.map((e) => e.repository));
  const incidents = snapshot
    ? [...snapshot.incidents].sort((a, b) => Number(TERMINAL.includes(a.status)) - Number(TERMINAL.includes(b.status)))
    : [];
  const single = projects.length === 1 ? projects[0] : null;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <PageHeader
          title={single?.name ?? "Overview"}
          description={
            single ? (
              <>
                {label(single.environment)}
                {single.url && (
                  <>
                    {" · "}
                    <a href={single.url} target="_blank" rel="noreferrer" className="text-ink underline underline-offset-2">
                      {host(single.url)}
                    </a>
                  </>
                )}
                {single.repository && (
                  <>
                    {" · "}
                    <a
                      href={`https://github.com/${single.repository}`}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-ink underline underline-offset-2"
                    >
                      {single.repository}
                    </a>
                  </>
                )}
              </>
            ) : projects.length > 1 ? (
              `${projects.length} projects monitored`
            ) : (
              "Simulated production environment: payment-api, auth-api and database."
            )
          }
          actions={
            demoMode && (
              <>
                <Button variant="quiet" disabled={busy !== null} onClick={() => void reset()}>
                  {busy === "reset" ? "Resetting…" : "Reset demo"}
                </Button>
                <Button variant="primary" disabled={busy !== null} onClick={() => void simulate()}>
                  {busy === "simulate" ? "Simulating…" : "Simulate incident"}
                </Button>
              </>
            )
          }
        />
        {demoMode && (
          <p className="max-w-3xl text-xs text-muted">
            Demo mode: Simulate incident replays the GitHub push and deploy-production run that ship payment-api v1.8.2,
            then the failure that follows. Each agent step is started from the incident page, and the rollback waits for
            human approval.
          </p>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>

      {!snapshot ? (
        <Empty>Loading…</Empty>
      ) : (
        <>
          <ProductionStatus snapshot={snapshot} />

          {projects.length > 0 && (
            <Section title="Projects" aside={`Health checked every ${snapshot.projects?.health_check_interval_seconds ?? 60} s`}>
              <ProjectsTable snapshot={snapshot} />
              <p className="mt-2 text-xs text-muted">
                Each project&apos;s URL is checked over HTTP. Latency is the check&apos;s response time; failed checks
                is the share of the last 10 checks that did not succeed.
              </p>
            </Section>
          )}

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
            <IncidentTable
              incidents={incidents.slice(0, 5)}
              empty={demoMode ? "No incidents. Simulate incident to start the demo." : "No incidents."}
            />
          </Section>

          <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
            {demoServices.length > 0 && (
              <Section title={projects.length > 0 ? "Demo services" : "Services"}>
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
                      {demoServices.map((service) => {
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
            )}

            <Section
              title="Deployments"
              aside={severalServices ? undefined : Object.keys(snapshot.deployments)[0]}
              className={demoServices.length > 0 ? "" : "lg:col-span-2"}
            >
              <div className={table.wrap}>
                <table className={table.table}>
                  <thead>
                    <tr>
                      {severalServices && <th scope="col" className={`${table.th} hidden sm:table-cell`}>Service</th>}
                      <th scope="col" className={table.th}>Version</th>
                      <th scope="col" className={`${table.th} hidden sm:table-cell`}>Commit</th>
                      <th scope="col" className={table.th}>Status</th>
                      <th scope="col" className={`${table.th} text-right`}>Deployed (UTC)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deploymentRows.length === 0 && (
                      <tr>
                        <td colSpan={5} className={`${table.td} text-muted`}>
                          No deployments recorded yet. Production deployments arrive through each repository&apos;s
                          GitHub webhook (Deployment statuses).
                        </td>
                      </tr>
                    )}
                    {deploymentRows.map((d) => {
                      // The newest successful deployment of its service is the one running.
                      const active = snapshot.deployments[d.service_name]?.find((x) => x.status === "SUCCEEDED") === d;
                      return (
                        <tr key={`${d.service_name}-${d.version}-${d.timestamp}`}>
                          {severalServices && (
                            <td className={`${table.td} ${table.mono} hidden sm:table-cell`}>{d.service_name}</td>
                          )}
                          <td className={`${table.td} ${table.mono} font-medium`}>
                            {d.version}
                            {severalServices && (
                              <span className="block text-xs font-normal text-muted sm:hidden">{d.service_name}</span>
                            )}
                          </td>
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
              <Empty>No CI/CD events yet. Each repository&apos;s GitHub webhook delivers to /api/webhooks/github.</Empty>
            ) : (
              <CicdTable events={snapshot.cicd} showRepository={repositories.size > 1} />
            )}
          </Section>
        </>
      )}
    </div>
  );
}
