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

export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
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
