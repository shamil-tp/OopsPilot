import type { ActionType, IncidentStatus } from "@/types/api";

/** Backend lifecycle (CLAUDE.md §46), in order. FAILED / ESCALATED are terminal alternatives. */
export const LIFECYCLE: IncidentStatus[] = [
  "DETECTED",
  "INVESTIGATING",
  "ANALYZING",
  "AWAITING_APPROVAL",
  "REMEDIATING",
  "VERIFYING",
  "RESOLVED",
];

export const TERMINAL: IncidentStatus[] = ["RESOLVED", "FAILED", "ESCALATED"];

export function label(value: string): string {
  return value.replaceAll("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

// All times are shown in UTC, like the backend's evidence lines, event messages and reports, so the
// same moment never appears with two different clock times.
const TIME: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
};

export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-GB", TIME);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return `${new Date(iso).toLocaleString("en-GB", { ...TIME, month: "short", day: "numeric" })} UTC`;
}

/** Compact table date, e.g. "30 Sept 19:30". */
export function shortDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", { ...TIME, second: undefined, month: "short", day: "numeric" });
}

/** Elapsed time between two timestamps, e.g. "1 min 03 s" or "42 s". */
export function duration(fromIso: string | null | undefined, toIso: string | null | undefined): string {
  if (!fromIso || !toIso) return "—";
  const seconds = Math.max(0, Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${String(seconds % 60).padStart(2, "0")} s`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, unit = ""): string {
  return value === null || value === undefined ? "—" : `${Number(value.toFixed(2))}${unit}`;
}

export function str(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "—" : JSON.stringify(value);
}

/** Human sentence for an action and its stored parameters, e.g. "Roll back payment-api from v1.8.2 to v1.8.1". */
export function describeAction(action: ActionType, params: Record<string, unknown>, target: string): string {
  switch (action) {
    case "ROLLBACK_DEPLOYMENT":
      return `Roll back ${str(params.service)} from ${str(params.from_version)} to ${str(params.to_version)}`;
    case "RESTART_SERVICE":
      return `Restart ${str(params.service ?? target)}`;
    case "ESCALATE_TO_HUMAN":
      return "Escalate to a human";
    default:
      return "No action";
  }
}
