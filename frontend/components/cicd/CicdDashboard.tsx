"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Button,
  Dot,
  Empty,
  ErrorNote,
  Indicator,
  PageHeader,
  Panel,
  Section,
  table,
  type Tone,
} from "@/components/ui";
import { ApiError, listCicdEvents, listServices } from "@/lib/api";
import { dateTime, label, time } from "@/lib/format";
import type { CicdCategory, CicdConclusion, CicdEvent, CicdStatus, ServiceSummary } from "@/types/api";
import { cicdOutcome, cicdTitle } from "@/components/cicd/CicdTable";
import { CicdEventModal } from "@/components/cicd/CicdEventModal";
import { WebhookModal } from "@/components/projects/WebhookModal";

const REFRESH_MS = 10_000;

const CATEGORY_TABS: { label: string; value: CicdCategory | "ALL" }[] = [
  { label: "All events", value: "ALL" },
  { label: "Deployments", value: "DEPLOYMENT" },
  { label: "Builds", value: "BUILD" },
  { label: "Tests", value: "TEST" },
  { label: "Commits", value: "COMMIT" },
];

export function CicdDashboard() {
  const searchParams = useSearchParams();
  const initialService = searchParams.get("service") ?? "";

  const [events, setEvents] = useState<CicdEvent[] | null>(null);
  const [services, setServices] = useState<ServiceSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Filters
  const [categoryFilter, setCategoryFilter] = useState<CicdCategory | "ALL">("ALL");
  const [serviceFilter, setServiceFilter] = useState<string>(initialService);
  const [conclusionFilter, setConclusionFilter] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");

  // Modals
  const [selectedEvent, setSelectedEvent] = useState<CicdEvent | null>(null);
  const [showWebhookModal, setShowWebhookModal] = useState(false);

  // Load services for dropdown
  useEffect(() => {
    listServices()
      .then(setServices)
      .catch(() => []);
  }, []);

  // Load events
  const load = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    try {
      const data = await listCicdEvents({ limit: 50 });
      setEvents(data);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Could not load CI/CD telemetry events");
    } finally {
      if (manual) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchEvents = () => {
      listCicdEvents({ limit: 50 })
        .then((data) => {
          if (!cancelled) {
            setEvents(data);
            setError(null);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setError(err instanceof ApiError ? err.message : "Could not load CI/CD telemetry events");
          }
        });
    };

    void fetchEvents();
    const timer = setInterval(() => void fetchEvents(), REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // Filtered events
  const filteredEvents = useMemo(() => {
    if (!events) return [];
    return events.filter((ev) => {
      // Category filter
      if (categoryFilter !== "ALL" && ev.category !== categoryFilter) {
        return false;
      }
      // Service filter
      if (serviceFilter && ev.service_name !== serviceFilter) {
        return false;
      }
      // Conclusion / Status filter
      if (conclusionFilter === "SUCCESS" && ev.conclusion !== "SUCCESS") {
        return false;
      }
      if (
        conclusionFilter === "FAILURE" &&
        ev.conclusion !== "FAILURE" &&
        ev.conclusion !== "TIMED_OUT"
      ) {
        return false;
      }
      if (conclusionFilter === "IN_PROGRESS" && ev.status !== "IN_PROGRESS") {
        return false;
      }
      // Search query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const msg = (ev.commit_message ?? "").toLowerCase();
        const branch = (ev.branch ?? "").toLowerCase();
        const sha = (ev.commit_sha ?? "").toLowerCase();
        const repo = ev.repository.toLowerCase();
        const author = (ev.actor ?? "").toLowerCase();
        const workflow = (ev.workflow_name ?? "").toLowerCase();
        if (
          !msg.includes(q) &&
          !branch.includes(q) &&
          !sha.includes(q) &&
          !repo.includes(q) &&
          !author.includes(q) &&
          !workflow.includes(q)
        ) {
          return false;
        }
      }
      return true;
    });
  }, [events, categoryFilter, serviceFilter, conclusionFilter, searchQuery]);

  // Telemetry metrics
  const metrics = useMemo(() => {
    if (!events || events.length === 0) {
      return { total: 0, deployments: 0, buildSuccessRate: 100, repositories: 0 };
    }
    const deployments = events.filter((e) => e.category === "DEPLOYMENT").length;
    const builds = events.filter((e) => e.category === "BUILD");
    const successfulBuilds = builds.filter((e) => e.conclusion === "SUCCESS").length;
    const buildSuccessRate =
      builds.length > 0 ? Math.round((successfulBuilds / builds.length) * 100) : 100;
    const repos = new Set(events.map((e) => e.repository));

    return {
      total: events.length,
      deployments,
      buildSuccessRate,
      repositories: repos.size,
    };
  }, [events]);

  return (
    <div className="flex flex-col gap-6">
      {/* Page Header */}
      <PageHeader
        title="CI/CD Pipelines & Telemetry"
        description="Live stream of GitHub commits, workflow runs, and production deployments captured via webhooks."
        actions={
          <div className="flex items-center gap-2">
            <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-line bg-canvas text-xs text-muted">
              <Dot tone="ok" className="h-2 w-2 animate-pulse" />
              <span>Live 10s</span>
            </div>
            <Button
              variant="secondary"
              onClick={() => setShowWebhookModal(true)}
            >
              Webhook setup
            </Button>
            <Button
              disabled={refreshing}
              onClick={() => void load(true)}
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </Button>
          </div>
        }
      />

      {error && <ErrorNote>{error}</ErrorNote>}

      {/* KPI Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Panel className="p-4">
          <span className="text-xs text-muted block">Total Events</span>
          <span className="mt-1 font-mono text-2xl font-semibold text-ink">
            {events === null ? "—" : metrics.total}
          </span>
          <span className="mt-0.5 text-[11px] text-muted block">In telemetry window</span>
        </Panel>

        <Panel className="p-4">
          <span className="text-xs text-muted block">Deployments</span>
          <span className="mt-1 font-mono text-2xl font-semibold text-ink">
            {events === null ? "—" : metrics.deployments}
          </span>
          <span className="mt-0.5 text-[11px] text-muted block">Recorded releases</span>
        </Panel>

        <Panel className="p-4">
          <span className="text-xs text-muted block">Build Success Rate</span>
          <span className="mt-1 font-mono text-2xl font-semibold text-ink">
            {events === null ? "—" : `${metrics.buildSuccessRate}%`}
          </span>
          <span className="mt-0.5 text-[11px] text-muted block">Automated workflows</span>
        </Panel>

        <Panel className="p-4">
          <span className="text-xs text-muted block">Monitored Repos</span>
          <span className="mt-1 font-mono text-2xl font-semibold text-ink">
            {events === null ? "—" : metrics.repositories}
          </span>
          <span className="mt-0.5 text-[11px] text-muted block">Active GitHub repos</span>
        </Panel>
      </div>

      {/* Controls & Filter Bar */}
      <Panel className="p-4 flex flex-col gap-4">
        {/* Category Tabs */}
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line pb-3">
          {CATEGORY_TABS.map((tab) => {
            const active = categoryFilter === tab.value;
            return (
              <button
                key={tab.value}
                type="button"
                onClick={() => setCategoryFilter(tab.value)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  active
                    ? "bg-ink text-white"
                    : "text-muted hover:bg-hover hover:text-ink"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Dropdowns & Search */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Project dropdown */}
          <div className="flex items-center gap-2">
            <label htmlFor="service-filter" className="text-xs text-muted whitespace-nowrap">
              Project:
            </label>
            <select
              id="service-filter"
              value={serviceFilter}
              onChange={(e) => setServiceFilter(e.target.value)}
              className="rounded-md border border-line bg-canvas px-2.5 py-1 text-xs text-ink focus:outline-none focus:ring-1 focus:ring-ink"
            >
              <option value="">All projects</option>
              {services.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.display_name}
                </option>
              ))}
            </select>
          </div>

          {/* Outcome dropdown */}
          <div className="flex items-center gap-2">
            <label htmlFor="conclusion-filter" className="text-xs text-muted whitespace-nowrap">
              Outcome:
            </label>
            <select
              id="conclusion-filter"
              value={conclusionFilter}
              onChange={(e) => setConclusionFilter(e.target.value)}
              className="rounded-md border border-line bg-canvas px-2.5 py-1 text-xs text-ink focus:outline-none focus:ring-1 focus:ring-ink"
            >
              <option value="ALL">All outcomes</option>
              <option value="SUCCESS">Success</option>
              <option value="FAILURE">Failure / Timed out</option>
              <option value="IN_PROGRESS">In Progress</option>
            </select>
          </div>

          {/* Search bar */}
          <div className="flex-1 min-w-[200px]">
            <input
              type="text"
              placeholder="Filter by commit, branch, author, or SHA…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full rounded-md border border-line bg-canvas px-3 py-1 text-xs text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-ink"
            />
          </div>

          {/* Reset button if filtered */}
          {(categoryFilter !== "ALL" || serviceFilter || conclusionFilter !== "ALL" || searchQuery) && (
            <Button
              variant="quiet"
              className="text-xs py-1 px-2.5"
              onClick={() => {
                setCategoryFilter("ALL");
                setServiceFilter("");
                setConclusionFilter("ALL");
                setSearchQuery("");
              }}
            >
              Reset filters
            </Button>
          )}
        </div>
      </Panel>

      {/* Events Table */}
      {events === null ? (
        !error && <Empty>Loading CI/CD events…</Empty>
      ) : filteredEvents.length === 0 ? (
        events.length === 0 ? (
          <Panel className="p-8 text-center flex flex-col items-center gap-3">
            <span className="text-2xl">⚡</span>
            <h3 className="text-sm font-semibold text-ink">No CI/CD activity received yet</h3>
            <p className="text-xs text-muted max-w-md">
              Configure your GitHub repository webhook with OpsPilot to automatically stream pushes,
              workflow runs, automated tests, and deployment statuses into this console.
            </p>
            <Button
              variant="primary"
              className="mt-2"
              onClick={() => setShowWebhookModal(true)}
            >
              Setup GitHub Webhook
            </Button>
          </Panel>
        ) : (
          <Empty>No events match the selected filter criteria.</Empty>
        )
      ) : (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead>
              <tr>
                <th scope="col" className={table.th}>Time</th>
                <th scope="col" className={table.th}>Event & Details</th>
                <th scope="col" className={table.th}>Category</th>
                <th scope="col" className={table.th}>Outcome</th>
                <th scope="col" className={`${table.th} hidden sm:table-cell`}>Branch / Commit</th>
                <th scope="col" className={`${table.th} text-right`}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredEvents.map((event) => {
                const outcome = cicdOutcome(event);
                const commitLink =
                  event.commit_sha && event.repository
                    ? `https://github.com/${event.repository}/commit/${event.commit_sha}`
                    : null;

                return (
                  <tr
                    key={`cicd-event-${event.id}`}
                    className="hover:bg-canvas/50 transition-colors"
                  >
                    {/* Time */}
                    <td className={`${table.td} ${table.mono} whitespace-nowrap text-muted text-xs`}>
                      <span title={dateTime(event.occurred_at)}>
                        {time(event.occurred_at)}
                      </span>
                    </td>

                    {/* Event & Details */}
                    <td className={table.td}>
                      <div className="flex flex-col gap-0.5">
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setSelectedEvent(event)}
                            className="text-left font-medium text-ink hover:underline text-sm"
                          >
                            {cicdTitle(event)}
                          </button>
                          {event.environment && (
                            <span className="rounded bg-hover px-1.5 py-0.2 font-mono text-[10px] text-muted border border-line">
                              {event.environment}
                            </span>
                          )}
                        </div>
                        <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
                          <span className="font-mono text-muted">{event.repository}</span>
                          {event.actor && <span>· by {event.actor}</span>}
                          {event.service_name && (
                            <Link
                              href={`/projects/${event.service_name}`}
                              className="text-muted hover:text-ink underline underline-offset-2"
                            >
                              {event.service_name}
                            </Link>
                          )}
                        </div>
                        {event.commit_message && (
                          <p className="mt-0.5 max-w-lg text-xs text-muted truncate">
                            {event.commit_message}
                          </p>
                        )}
                      </div>
                    </td>

                    {/* Category */}
                    <td className={table.td}>
                      <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink border border-line">
                        {event.category}
                      </span>
                    </td>

                    {/* Outcome */}
                    <td className={table.td}>
                      <Indicator tone={outcome.tone}>{outcome.text}</Indicator>
                      {event.status && event.status !== "COMPLETED" && (
                        <span className="block text-[11px] text-muted">
                          {label(event.status)}
                        </span>
                      )}
                    </td>

                    {/* Branch / Commit */}
                    <td className={`${table.td} hidden sm:table-cell text-xs`}>
                      <div className="flex flex-col gap-0.5">
                        {event.branch ? (
                          <span className="font-mono font-medium text-ink">
                            {event.branch}
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                        {event.commit_sha && (
                          commitLink ? (
                            <a
                              href={commitLink}
                              target="_blank"
                              rel="noreferrer"
                              className="font-mono text-[11px] text-muted hover:text-ink underline underline-offset-2"
                            >
                              {event.commit_sha.slice(0, 7)}
                            </a>
                          ) : (
                            <span className="font-mono text-[11px] text-muted">
                              {event.commit_sha.slice(0, 7)}
                            </span>
                          )
                        )}
                      </div>
                    </td>

                    {/* Actions */}
                    <td className={`${table.td} text-right whitespace-nowrap`}>
                      <div className="flex items-center justify-end gap-1.5">
                        <Button
                          variant="secondary"
                          className="px-2 py-0.5 text-xs h-7"
                          onClick={() => setSelectedEvent(event)}
                        >
                          Details
                        </Button>
                        {event.html_url && (
                          <a
                            href={event.html_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center justify-center rounded-md border border-line p-1 text-muted hover:text-ink hover:bg-hover h-7 w-7"
                            title="Open on GitHub"
                          >
                            ↗
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Event Details Modal */}
      <CicdEventModal
        event={selectedEvent}
        isOpen={selectedEvent !== null}
        onClose={() => setSelectedEvent(null)}
      />

      {/* Webhook Setup Modal */}
      <WebhookModal
        isOpen={showWebhookModal}
        onClose={() => setShowWebhookModal(false)}
        repository={null}
        serviceName="All Services"
      />
    </div>
  );
}
