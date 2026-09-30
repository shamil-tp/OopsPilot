"use client";

import { motion } from "framer-motion";

import { useSystemHealth } from "@/hooks/useSystemHealth";

type Tone = "ok" | "warn" | "down" | "pending";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "bg-emerald-400 shadow-emerald-400/50",
  warn: "bg-amber-400 shadow-amber-400/50",
  down: "bg-rose-500 shadow-rose-500/50",
  pending: "bg-slate-500 shadow-transparent",
};

function StatusRow({ label, value, tone }: { label: string; value: string; tone: Tone }) {
  return (
    <div className="flex items-center justify-between gap-4 border-t border-slate-800 py-3 first:border-t-0">
      <span className="text-sm text-slate-400">{label}</span>
      <span className="flex items-center gap-2 font-mono text-sm text-slate-100">
        <span className={`h-2 w-2 rounded-full shadow-[0_0_8px] ${TONE_CLASSES[tone]}`} />
        {value}
      </span>
    </div>
  );
}

export function SystemStatusCard() {
  const { health, error, loading } = useSystemHealth();

  const apiTone: Tone = loading ? "pending" : error ? "down" : "ok";
  const apiValue = loading ? "checking…" : error ? "unreachable" : `online · v${health?.version}`;

  const db = health?.database;
  const dbTone: Tone = !db ? "pending" : db.status === "ok" ? "ok" : "down";
  const dbValue = !db ? "—" : db.status === "ok" ? `connected · ${db.latency_ms} ms` : "unavailable";

  const ai = health?.ai;
  const aiTone: Tone = !ai ? "pending" : ai.configured_keys > 0 ? "ok" : "warn";
  const aiValue = !ai
    ? "—"
    : `${ai.provider} · ${ai.model} · ${ai.configured_keys} key${ai.configured_keys === 1 ? "" : "s"}`;

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="rounded-xl border border-slate-800 bg-slate-900/60 p-6 backdrop-blur"
      aria-labelledby="system-status-heading"
    >
      <h2 id="system-status-heading" className="mb-2 text-xs font-semibold tracking-widest text-slate-400 uppercase">
        System Status
      </h2>
      <StatusRow label="Backend API" value={apiValue} tone={apiTone} />
      <StatusRow label="PostgreSQL" value={dbValue} tone={dbTone} />
      <StatusRow label="AI provider" value={aiValue} tone={aiTone} />
      {error && <p className="mt-3 text-sm text-rose-400">{error}</p>}
    </motion.section>
  );
}
