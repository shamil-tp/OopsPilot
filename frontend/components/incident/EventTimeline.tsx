"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

import { Button, Dot, Empty, type Tone } from "@/components/ui";
import { label, time } from "@/lib/format";
import type { AgentEvent } from "@/types/api";

// Fine-grained events, hidden unless "All events" is selected.
const DETAIL = new Set(["tool_started", "tool_completed", "recovery_check"]);

const TONE: Record<string, Tone> = {
  incident_created: "critical",
  error: "critical",
  approval_required: "warn",
  operator_action_required: "warn",
  incident_escalated: "warn",
  incident_resolved: "ok",
};

function actor(event: AgentEvent): string {
  if (event.metadata.source === "github") return "GitHub";
  return event.agent ? label(event.agent) : "System";
}

export function EventTimeline({ events }: { events: AgentEvent[] }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? events : events.filter((e) => !DETAIL.has(e.event_type));

  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between gap-2 border-b border-line pb-2">
        <h2 className="text-[15px] font-semibold text-ink">Timeline</h2>
        <Button variant="quiet" className="px-0 py-0 text-xs" aria-pressed={showAll} onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Milestones only" : `All events (${events.length})`}
        </Button>
      </div>
      {visible.length === 0 ? (
        <Empty>No events yet.</Empty>
      ) : (
        <ol aria-live="polite" className="max-h-80 overflow-y-auto pr-1 lg:max-h-[calc(100vh-9rem)]">
          <AnimatePresence initial={false}>
            {visible.map((event, i) => (
              <motion.li
                key={event.id}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.2 }}
                className="grid grid-cols-[3.75rem_minmax(0,1fr)] gap-x-3"
              >
                <time dateTime={event.timestamp} className="pt-px font-mono text-xs text-muted">
                  {time(event.timestamp)}
                </time>
                <div className={`relative border-l pl-4 ${i === visible.length - 1 ? "border-transparent pb-0" : "border-line pb-4"}`}>
                  <Dot tone={TONE[event.event_type] ?? "neutral"} className="absolute top-1.5 -left-[4.5px] ring-2 ring-canvas" />
                  <p className="text-sm leading-snug break-words text-ink">{event.message}</p>
                  <p className="mt-0.5 text-xs text-muted">{actor(event)}</p>
                </div>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      )}
    </div>
  );
}
