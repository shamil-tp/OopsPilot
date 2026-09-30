"use client";

import { motion } from "framer-motion";

import { label, LIFECYCLE } from "@/lib/format";
import type { IncidentStatus } from "@/types/api";

/** The backend lifecycle; FAILED/ESCALATED are shown as the backend reports them. */
export function LifecycleStepper({ status }: { status: IncidentStatus }) {
  const index = LIFECYCLE.indexOf(status);
  const offPath = index === -1; // FAILED or ESCALATED

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <ol className="grid grid-cols-7 gap-1">
        {LIFECYCLE.map((step, i) => {
          const done = !offPath && i < index;
          const current = !offPath && i === index;
          const resolved = current && step === "RESOLVED";
          return (
            <li key={step} className="flex flex-col items-center gap-2 text-center">
              <div className="flex w-full items-center">
                <div className={`h-0.5 flex-1 ${i === 0 ? "opacity-0" : done || current ? "bg-cyan-400/70" : "bg-slate-700"}`} />
                <motion.div
                  layout
                  className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border font-mono text-[11px] ${
                    resolved
                      ? "border-emerald-400 bg-emerald-400/20 text-emerald-300"
                      : current
                        ? "border-cyan-300 bg-cyan-400/20 text-cyan-200 shadow-[0_0_14px] shadow-cyan-400/40"
                        : done
                          ? "border-cyan-500/60 bg-cyan-500/10 text-cyan-300"
                          : "border-slate-700 bg-slate-900 text-slate-500"
                  }`}
                  animate={current && !resolved ? { scale: [1, 1.12, 1] } : { scale: 1 }}
                  transition={current && !resolved ? { repeat: Infinity, duration: 1.6 } : { duration: 0.2 }}
                >
                  {done || resolved ? "✓" : i + 1}
                </motion.div>
                <div className={`h-0.5 flex-1 ${i === LIFECYCLE.length - 1 ? "opacity-0" : done ? "bg-cyan-400/70" : "bg-slate-700"}`} />
              </div>
              <span className={`text-[11px] leading-tight ${current ? "text-slate-100" : "text-slate-500"}`}>
                {label(step)}
              </span>
            </li>
          );
        })}
      </ol>
      {offPath && (
        <p className="mt-3 text-center text-sm text-amber-300">
          The incident left the normal lifecycle: <span className="font-mono">{status}</span>
        </p>
      )}
    </div>
  );
}
