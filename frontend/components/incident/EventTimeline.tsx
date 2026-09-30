"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

import { Badge, Card, Empty } from "@/components/ui";
import { label, time } from "@/lib/format";
import type { AgentEvent } from "@/types/api";
import type { LiveMode } from "@/hooks/useIncidentConsole";

// Fine-grained events, hidden unless "show all" is on.
const DETAIL = new Set(["tool_started", "tool_completed", "recovery_check"]);

const ICON: Record<string, string> = {
  incident_created: "🚨",
  cicd_event_recorded: "⎇",
  deployment_detected: "🚀",
  agent_started: "▶",
  evidence_found: "🔎",
  investigation_completed: "✓",
  root_cause_identified: "🎯",
  remediation_recommended: "🛠",
  approval_required: "⚠",
  approval_received: "👤",
  remediation_started: "⏳",
  remediation_completed: "✓",
  verification_started: "🩺",
  verification_completed: "✓",
  incident_resolved: "✅",
  incident_escalated: "⤴",
  report_generated: "📄",
  error: "✖",
};

const MODE: Record<LiveMode, { text: string; tone: "emerald" | "amber" | "slate" }> = {
  live: { text: "Live", tone: "emerald" },
  polling: { text: "Polling", tone: "amber" },
  connecting: { text: "Connecting", tone: "slate" },
};

export function EventTimeline({ events, mode }: { events: AgentEvent[]; mode: LiveMode }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? events : events.filter((e) => !DETAIL.has(e.event_type));

  return (
    <Card
      eyebrow="Agent activity"
      title="Investigation timeline"
      actions={
        <div className="flex items-center gap-2">
          <Badge tone={MODE[mode].tone}>● {MODE[mode].text}</Badge>
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="rounded-md border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400 hover:border-slate-500 hover:text-slate-200"
          >
            {showAll ? "Milestones" : "Show all"}
          </button>
        </div>
      }
    >
      {visible.length === 0 ? (
        <Empty>No events yet.</Empty>
      ) : (
        <ol className="relative max-h-[34rem] space-y-3 overflow-y-auto pr-1">
          <AnimatePresence initial={false}>
            {visible.map((event) => (
              <motion.li
                key={event.id}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.25 }}
                className="flex gap-3"
              >
                <span
                  className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs ${
                    event.event_type === "error"
                      ? "border-rose-500/50 bg-rose-500/10"
                      : event.event_type === "approval_required"
                        ? "border-amber-400/50 bg-amber-400/10"
                        : "border-slate-700 bg-slate-800"
                  }`}
                >
                  {ICON[event.event_type] ?? "·"}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 text-[11px] text-slate-500">
                    <span className="font-mono">{time(event.timestamp)}</span>
                    <span>{event.agent ? label(event.agent) : "System"}</span>
                    <span className="font-mono text-slate-600">{event.event_type}</span>
                  </div>
                  <p className="text-sm break-words text-slate-200">{event.message}</p>
                </div>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      )}
    </Card>
  );
}
