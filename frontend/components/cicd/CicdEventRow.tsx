import type { ReactNode } from "react";

import { Badge } from "@/components/ui";
import { label, time } from "@/lib/format";
import type { CicdEvent } from "@/types/api";

const ICON: Record<CicdEvent["category"], string> = {
  COMMIT: "⎇",
  BUILD: "⚙",
  TEST: "🧪",
  DEPLOYMENT: "🚀",
};

type Tone = "emerald" | "rose" | "amber" | "sky" | "slate";

export function cicdOutcome(event: CicdEvent): { text: string; tone: Tone } {
  if (event.event_type === "push") return { text: event.metadata.tag ? "tag" : "push", tone: "slate" };
  switch (event.conclusion) {
    case "SUCCESS":
      return { text: "success", tone: "emerald" };
    case "FAILURE":
    case "TIMED_OUT":
      return { text: label(event.conclusion), tone: "rose" };
    case "CANCELLED":
      return { text: "cancelled", tone: "amber" };
    case null:
      return { text: label(event.status), tone: "sky" };
    default:
      return { text: label(event.conclusion), tone: "slate" };
  }
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

export function CicdEventRow({ event, extra }: { event: CicdEvent; extra?: ReactNode }) {
  const outcome = cicdOutcome(event);
  return (
    <div className="flex min-w-0 gap-3">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-slate-700 bg-slate-800 text-xs">
        {ICON[event.category]}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="font-mono text-[11px] text-slate-500">{time(event.occurred_at)}</span>
          <span className="font-medium text-slate-100">{cicdTitle(event)}</span>
          <Badge tone={outcome.tone}>{outcome.text}</Badge>
          {event.version && <Badge tone="cyan">{event.version}</Badge>}
          {event.deployment_id !== null && <Badge tone="violet">deployment</Badge>}
          {extra}
        </div>
        <p className="truncate font-mono text-[11px] text-slate-400">
          {event.commit_sha ? `commit ${event.commit_sha.slice(0, 7)}` : "no commit"}
          {event.commit_message ? ` · ${event.commit_message}` : ""}
          {event.actor ? ` · ${event.actor}` : ""}
        </p>
      </div>
    </div>
  );
}
