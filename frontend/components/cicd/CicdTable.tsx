import type { ReactNode } from "react";

import { Indicator, table, type Tone } from "@/components/ui";
import { label, time } from "@/lib/format";
import type { CicdEvent } from "@/types/api";

export function cicdOutcome(event: CicdEvent): { text: string; tone: Tone } {
  if (event.event_type === "push") return { text: event.metadata.tag ? "Tagged" : "Pushed", tone: "neutral" };
  switch (event.conclusion) {
    case "SUCCESS":
      return { text: "Success", tone: "ok" };
    case "FAILURE":
    case "TIMED_OUT":
      return { text: label(event.conclusion), tone: "critical" };
    case "CANCELLED":
      return { text: "Cancelled", tone: "warn" };
    case null:
      return { text: label(event.status), tone: "info" };
    default:
      return { text: label(event.conclusion), tone: "neutral" };
  }
}

/** Newest finished deployment per service (events arrive newest first). */
export function latestDeployments(events: CicdEvent[]): Record<string, CicdEvent> {
  const latest: Record<string, CicdEvent> = {};
  for (const event of events) {
    if (event.category !== "DEPLOYMENT" || !event.service_name || event.conclusion === null) continue;
    latest[event.service_name] ??= event;
  }
  return latest;
}

/**
 * "Last deploy failed": the host kept serving the previous build, so the site can be healthy while
 * the newest commits are not live. Nothing when the latest deployment succeeded.
 */
export function DeployFailedBadge({ event }: { event: CicdEvent | undefined }) {
  if (!event || (event.conclusion !== "FAILURE" && event.conclusion !== "TIMED_OUT")) return null;
  const text = <Indicator tone="warn">Last deploy failed · older version live</Indicator>;
  return event.html_url ? (
    <a href={event.html_url} target="_blank" rel="noreferrer" className="hover:underline" title={`Commit ${event.commit_sha?.slice(0, 7) ?? "unknown"}`}>
      {text}
    </a>
  ) : (
    text
  );
}

/** One-line title of an event, e.g. "deploy-production #57" or "push to main". */
export function cicdTitle(event: CicdEvent): string {
  if (event.event_type === "push") {
    const tag = event.metadata.tag;
    return typeof tag === "string" ? `tag ${tag}` : `push to ${event.branch ?? "unknown ref"}`;
  }
  if (event.event_type === "workflow_run") {
    return `${event.workflow_name ?? "workflow"}${event.run_number ? ` #${event.run_number}` : ""}`;
  }
  return `deployment to ${event.environment ?? "unknown"}`;
}

/** Extra, non-CI/CD rows (e.g. "first error") interleaved by time. */
export interface ContextRow {
  key: string;
  at: string;
  tone: Tone;
  text: ReactNode;
  cite?: ReactNode;
}

type Row = { at: string; event: CicdEvent; cite?: ReactNode } | ContextRow;

export function CicdTable({
  events,
  context = [],
  cite,
  showRepository = false,
}: {
  events: CicdEvent[];
  context?: ContextRow[];
  cite?: (event: CicdEvent) => ReactNode;
  /** Show which repository each event came from (several projects). */
  showRepository?: boolean;
}) {
  const rows: Row[] = [
    ...events.map((event) => ({ at: event.occurred_at, event, cite: cite?.(event) })),
    ...context,
  ].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));

  return (
    <div className={table.wrap}>
      <table className={table.table}>
        <thead>
          <tr>
            <th scope="col" className={table.th}>
              Time<span className="hidden sm:inline"> (UTC)</span>
            </th>
            <th scope="col" className={table.th}>Event</th>
            <th scope="col" className={table.th}>Result</th>
            <th scope="col" className={`${table.th} hidden sm:table-cell`}>Version</th>
            <th scope="col" className={`${table.th} hidden md:table-cell`}>Commit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) =>
            "event" in row ? (
              <tr key={`cicd-${row.event.id}`}>
                <td className={`${table.td} ${table.mono} whitespace-nowrap text-muted`}>{time(row.event.occurred_at)}</td>
                <td className={table.td}>
                  <span className="font-medium text-ink">{cicdTitle(row.event)}</span>{" "}
                  {row.cite}
                  {showRepository && (
                    <span className="block font-mono text-xs break-all text-muted">{row.event.repository}</span>
                  )}
                  {row.event.commit_message && (
                    <span className="block max-w-md text-xs break-words text-muted sm:truncate">{row.event.commit_message}</span>
                  )}
                </td>
                <td className={table.td}>
                  <Indicator tone={cicdOutcome(row.event).tone}>{cicdOutcome(row.event).text}</Indicator>
                  {row.event.deployment_id !== null && (
                    <span className="hidden text-xs text-muted sm:block">recorded as deployment</span>
                  )}
                </td>
                <td className={`${table.td} ${table.mono} hidden sm:table-cell`}>{row.event.version ?? "—"}</td>
                <td className={`${table.td} ${table.mono} hidden text-muted md:table-cell`}>
                  {row.event.commit_sha?.slice(0, 7) ?? "—"}
                </td>
              </tr>
            ) : (
              <tr key={row.key}>
                <td className={`${table.td} ${table.mono} whitespace-nowrap text-muted`}>{time(row.at)}</td>
                <td className={table.td} colSpan={4}>
                  <Indicator tone={row.tone}>{row.text}</Indicator> {row.cite}
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}
