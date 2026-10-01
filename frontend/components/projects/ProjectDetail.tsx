"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { CicdTable } from "@/components/cicd/CicdTable";
import { FindingList, ReviewTable } from "@/components/code-review/Findings";
import { IncidentTable } from "@/components/incidents/IncidentTable";
import {
  Dot,
  Empty,
  ErrorNote,
  Indicator,
  PageHeader,
  Panel,
  PrintButton,
  Section,
  ServiceStatusIndicator,
  table,
  type Tone,
} from "@/components/ui";
import {
  ApiError,
  getHealthHistory,
  getProjects,
  listCicdEvents,
  listCodeReviews,
  listDeployments,
  listIncidents,
  listServices,
} from "@/lib/api";
import { dateTime, label, num, shortDateTime, time, TERMINAL } from "@/lib/format";
import type { CicdEvent, CodeReview, Deployment, Incident, Project, ServiceHealth, ServiceSummary } from "@/types/api";

const REFRESH_MS = 10_000;

interface ProjectData {
  service: ServiceSummary;
  project: Project | null;
  checks: ServiceHealth[];
  reviews: CodeReview[];
  incidents: Incident[];
  deployments: Deployment[];
  cicd: CicdEvent[];
}

async function loadProject(name: string): Promise<ProjectData | null> {
  const services = await listServices();
  const service = services.find((s) => s.name === name);
  if (!service) return null;
  const [projects, checks, reviews, incidents, deployments, cicd] = await Promise.all([
    getProjects(),
    getHealthHistory(name, 20),
    listCodeReviews({ service: name, limit: 10 }),
    listIncidents(),
    listDeployments(name),
    listCicdEvents({ service: name, limit: 10 }),
  ]);
  return {
    service,
    project: projects?.projects.find((p) => p.service === name) ?? null,
    checks,
    reviews,
    incidents: incidents.filter((i) => i.service_name === name),
    deployments,
    cicd,
  };
}

const STATUS_TONE: Record<string, Tone> = { HEALTHY: "ok", DEGRADED: "warn", DOWN: "critical" };

export function ProjectDetail({ service }: { service: string }) {
  const [data, setData] = useState<ProjectData | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      loadProject(service)
        .then((result) => {
          if (!cancelled) {
            setData(result);
            setError(null);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load the project");
        });
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [service]);

  if (data === undefined) return error ? <ErrorNote>{error}</ErrorNote> : <Empty>Loading project…</Empty>;
  if (data === null) return <ErrorNote>Unknown project &quot;{service}&quot;.</ErrorNote>;

  const latest = data.checks[0];
  const reviewed = data.reviews.find((r) => r.status === "COMPLETED");
  const openIncidents = data.incidents.filter((i) => !TERMINAL.includes(i.status));
  const version = data.deployments.find((d) => d.status === "SUCCEEDED")?.version;
  const repo = data.project?.repository ?? null;
  const tone: Tone = latest ? (STATUS_TONE[latest.status] ?? "neutral") : "neutral";

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <nav aria-label="Breadcrumb" className="text-xs text-muted print:hidden">
          <Link href="/" className="hover:text-ink">
            Overview
          </Link>
          <span aria-hidden className="mx-1.5">/</span>
          <span className="font-mono text-ink">{data.service.name}</span>
        </nav>
        <PageHeader
          title={data.service.display_name}
          description={
            <>
              {label(data.project?.environment ?? "production")}
              {data.service.url && (
                <>
                  {" · "}
                  <a href={data.service.url} target="_blank" rel="noreferrer" className="text-ink underline underline-offset-2">
                    {data.service.url.replace(/^https?:\/\//, "").replace(/\/$/, "")}
                  </a>
                </>
              )}
              {repo && (
                <>
                  {" · "}
                  <a
                    href={`https://github.com/${repo}`}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-ink underline underline-offset-2"
                  >
                    {repo}
                  </a>
                </>
              )}
            </>
          }
          actions={<PrintButton label="Download PDF" />}
        />
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>

      <Panel className="px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1" role="status" aria-live="polite">
          <Dot tone={tone} className="h-2.5 w-2.5" />
          <p className="text-base font-semibold text-ink">
            {latest ? label(latest.status) : "Not checked yet"}
            {openIncidents.length > 0 && ` · ${openIncidents.length} open incident${openIncidents.length === 1 ? "" : "s"}`}
          </p>
          {latest && <p className="text-sm text-muted">last check {dateTime(latest.timestamp)}</p>}
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-line pt-4 sm:grid-cols-4">
          {[
            ["Response time", num(latest?.latency_ms, " ms")],
            ["Failed checks (last 10)", num(latest?.error_rate, "%")],
            ["Version", version ?? "—"],
            ["Open code issues", reviewed ? String(reviewed.findings.length) : "—"],
          ].map(([term, value]) => (
            <div key={term} className="min-w-0">
              <dt className="text-xs text-muted">{term}</dt>
              <dd className="mt-0.5 truncate font-mono text-[15px] font-medium text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      </Panel>

      <Section
        title="Code issues"
        aside={
          reviewed ? (
            <Link href={`/code-reviews/${reviewed.id}`} className="hover:text-ink">
              From the review of {reviewed.commit_sha.slice(0, 7)} · {shortDateTime(reviewed.created_at)} →
            </Link>
          ) : undefined
        }
      >
        {reviewed ? (
          <>
            {reviewed.summary && <p className="mb-3 max-w-3xl text-sm text-ink">{reviewed.summary}</p>}
            <FindingList review={reviewed} />
          </>
        ) : (
          <Empty>No reviewed push yet. The next push to the main branch is reviewed automatically.</Empty>
        )}
      </Section>

      <Section title="Incidents">
        <IncidentTable incidents={data.incidents.slice(0, 10)} empty="No incidents for this project." />
      </Section>

      <Section title="Code reviews" aside="Every push to the main branch">
        <ReviewTable reviews={data.reviews} showService={false} />
      </Section>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        <Section title="Health checks" aside="Latest 20">
          {data.checks.length === 0 ? (
            <Empty>No health checks yet.</Empty>
          ) : (
            <div className={table.wrap}>
              <table className={table.table}>
                <thead>
                  <tr>
                    <th scope="col" className={table.th}>Time (UTC)</th>
                    <th scope="col" className={table.th}>Status</th>
                    <th scope="col" className={`${table.th} text-right`}>Response</th>
                  </tr>
                </thead>
                <tbody>
                  {data.checks.map((check, i) => (
                    <tr key={`${check.timestamp}-${i}`}>
                      <td className={`${table.td} ${table.mono} text-muted`}>{time(check.timestamp)}</td>
                      <td className={table.td}>
                        <ServiceStatusIndicator status={check.status} />
                      </td>
                      <td className={`${table.td} ${table.mono} text-right whitespace-nowrap`}>
                        {num(check.latency_ms, " ms")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>

        <Section title="Deployments">
          {data.deployments.length === 0 ? (
            <Empty>No deployments recorded yet (they arrive through the webhook&apos;s Deployment statuses).</Empty>
          ) : (
            <div className={table.wrap}>
              <table className={table.table}>
                <thead>
                  <tr>
                    <th scope="col" className={table.th}>Version</th>
                    <th scope="col" className={table.th}>Status</th>
                    <th scope="col" className={`${table.th} text-right`}>Deployed (UTC)</th>
                  </tr>
                </thead>
                <tbody>
                  {data.deployments.map((d, i) => (
                    <tr key={`${d.version}-${d.timestamp}`}>
                      <td className={`${table.td} ${table.mono} font-medium`}>{d.version}</td>
                      <td className={table.td}>
                        {i === data.deployments.findIndex((x) => x.status === "SUCCEEDED") ? (
                          <Indicator tone="ok">Active</Indicator>
                        ) : (
                          <Indicator tone="neutral">{label(d.status)}</Indicator>
                        )}
                      </td>
                      <td className={`${table.td} text-right whitespace-nowrap text-muted`}>{shortDateTime(d.timestamp)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </div>

      <Section title="GitHub activity">
        {data.cicd.length === 0 ? <Empty>No GitHub events for this project yet.</Empty> : <CicdTable events={data.cicd} />}
      </Section>
    </div>
  );
}
