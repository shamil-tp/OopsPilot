"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { CicdEventRow } from "@/components/cicd/CicdEventRow";
import { Badge, Card, Empty, ErrorNote, ServiceStatusBadge, SeverityBadge, StatusBadge } from "@/components/ui";
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
import { dateTime, num, TERMINAL } from "@/lib/format";
import type { CicdEvent, Deployment, Incident, ServiceHealth, ServiceSummary, WebhookStatus } from "@/types/api";

const REFRESH_MS = 5000;

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

export function DashboardConsole() {
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

  const active = snapshot?.incidents.filter((i) => !TERMINAL.includes(i.status)) ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <motion.button
          type="button"
          whileTap={{ scale: 0.97 }}
          disabled={busy !== null}
          onClick={() => void simulate()}
          className="rounded-lg bg-rose-500 px-5 py-3 text-sm font-semibold text-white shadow-[0_0_24px] shadow-rose-500/30 hover:bg-rose-400 disabled:cursor-wait disabled:opacity-60"
        >
          {busy === "simulate" ? "Simulating…" : "🚨 Simulate Incident"}
        </motion.button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void reset()}
          className="rounded-md border border-slate-700 px-3 py-2 text-xs text-slate-400 hover:border-slate-500 hover:text-slate-200 disabled:opacity-50"
        >
          {busy === "reset" ? "Resetting…" : "Reset demo"}
        </button>
        <span className="text-xs text-slate-500">
          Simulate replays the GitHub push and deploy-production run that ship payment-api v1.8.2, then the failure;
          the agents run step by step from the incident page, with a human approval gate before any change.
        </span>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card eyebrow="Incidents" title={`Active incidents (${active.length})`} className="lg:col-span-2">
          {!snapshot ? (
            <Empty>Loading…</Empty>
          ) : snapshot.incidents.length === 0 ? (
            <Empty>No incidents. Click “Simulate Incident” to start the demo.</Empty>
          ) : (
            <ul className="divide-y divide-slate-800">
              {snapshot.incidents.map((incident) => (
                <li key={incident.id}>
                  <Link
                    href={`/incidents/${incident.id}`}
                    className="flex flex-wrap items-center gap-3 py-3 hover:bg-slate-800/30"
                  >
                    <span className="font-mono text-sm text-cyan-300">{incident.reference}</span>
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-200">{incident.title}</span>
                    <Badge>{incident.service_name}</Badge>
                    <SeverityBadge severity={incident.severity} />
                    <StatusBadge status={incident.status} />
                    <span className="ml-auto text-right text-xs whitespace-nowrap text-slate-500">{dateTime(incident.created_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card eyebrow="payment-api" title="Recent deployments">
          {!snapshot ? (
            <Empty>Loading…</Empty>
          ) : (
            <ul className="space-y-2">
              {snapshot.deployments.map((d, i) => (
                <li key={`${d.version}-${i}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
                  <span className="font-mono text-slate-200">
                    {d.version}
                    {d.commit_sha && <span className="ml-2 text-[11px] text-slate-500">{d.commit_sha.slice(0, 7)}</span>}
                  </span>
                  <Badge tone={d.status === "SUCCEEDED" ? (i === 0 ? "emerald" : "slate") : "rose"}>
                    {i === 0 && d.status === "SUCCEEDED" ? "active" : d.status}
                  </Badge>
                  <span className="ml-auto text-xs whitespace-nowrap text-slate-500">{dateTime(d.timestamp)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card eyebrow="Environment" title="Services" className="lg:col-span-2">
        {!snapshot ? (
          <Empty>Loading…</Empty>
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            {snapshot.services.map((service) => {
              const h = snapshot.health[service.name];
              return (
                <div key={service.name} className="rounded-lg border border-slate-800 bg-slate-950/50 p-4">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-1">
                    <span className="font-mono text-sm whitespace-nowrap text-slate-100">{service.name}</span>
                    <ServiceStatusBadge status={h?.status ?? null} />
                  </div>
                  <dl className="grid grid-cols-2 gap-1 text-xs">
                    <dt className="text-slate-500">Error rate</dt>
                    <dd className={`text-right font-mono ${h && h.error_rate >= 5 ? "text-rose-300" : "text-slate-200"}`}>
                      {num(h?.error_rate, "%")}
                    </dd>
                    <dt className="text-slate-500">Latency</dt>
                    <dd className={`text-right font-mono ${h && h.latency_ms >= 500 ? "text-rose-300" : "text-slate-200"}`}>
                      {num(h?.latency_ms, " ms")}
                    </dd>
                  </dl>
                </div>
              );
            })}
          </div>
        )}
        </Card>

        <Card
          eyebrow="GitHub"
          title="CI/CD activity"
          actions={
            snapshot && (
              <Badge tone={snapshot.webhook.configured ? "emerald" : "slate"}>
                {snapshot.webhook.configured ? "webhook on" : "webhook off"}
              </Badge>
            )
          }
        >
          {!snapshot ? (
            <Empty>Loading…</Empty>
          ) : snapshot.cicd.length === 0 ? (
            <Empty>
              No CI/CD events yet. Simulating the incident replays its GitHub push and deploy-production run; a
              configured repository delivers to /api/webhooks/github.
            </Empty>
          ) : (
            <ul className="space-y-3">
              {snapshot.cicd.map((event) => (
                <li key={event.id}>
                  <CicdEventRow event={event} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
