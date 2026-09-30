import { STATUS_TONE } from "@/components/ui";
import { label, LIFECYCLE } from "@/lib/format";
import type { IncidentStatus } from "@/types/api";

const CURRENT_BAR = { ok: "bg-emerald-600", warn: "bg-amber-500", critical: "bg-red-600", info: "bg-blue-600", neutral: "bg-zinc-400" };

/** The backend lifecycle as a segmented progress bar. FAILED/ESCALATED are shown as reported. */
export function LifecycleStepper({ status }: { status: IncidentStatus }) {
  const index = LIFECYCLE.indexOf(status);
  const offPath = index === -1;

  return (
    <div>
      <p className="mb-2 text-xs text-muted sm:hidden">
        {offPath ? `Left the normal lifecycle: ${label(status)}` : `Step ${index + 1} of ${LIFECYCLE.length} · ${label(status)}`}
      </p>
      <ol className="grid grid-cols-7 gap-1" aria-label="Incident lifecycle">
        {LIFECYCLE.map((step, i) => {
          const done = !offPath && i < index;
          const current = !offPath && i === index;
          const bar = current ? CURRENT_BAR[STATUS_TONE[status]] : done ? "bg-zinc-700" : "bg-line";
          return (
            <li key={step} aria-current={current ? "step" : undefined} className="min-w-0">
              <div className={`h-1 rounded-full ${bar}`} />
              <span
                className={`sr-only mt-1.5 truncate text-xs sm:not-sr-only sm:block ${
                  current ? "font-medium text-ink" : done ? "text-muted" : "text-subtle"
                }`}
              >
                {label(step)}
              </span>
              <span className="sr-only">{current ? " (current)" : done ? " (done)" : ""}</span>
            </li>
          );
        })}
      </ol>
      {offPath && (
        <p className="mt-2 hidden text-xs text-amber-700 sm:block">
          The incident left the normal lifecycle: <span className="font-medium">{label(status)}</span>
        </p>
      )}
    </div>
  );
}
