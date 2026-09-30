import type { ReactNode } from "react";

import { label } from "@/lib/format";
import type { EvidenceItem, IncidentStatus, RiskLevel, ServiceStatus, Severity } from "@/types/api";

type Tone = "cyan" | "emerald" | "amber" | "rose" | "violet" | "slate" | "sky";

const TONES: Record<Tone, string> = {
  cyan: "border-cyan-400/30 bg-cyan-400/10 text-cyan-300",
  emerald: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  amber: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  rose: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  violet: "border-violet-400/30 bg-violet-400/10 text-violet-300",
  sky: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  slate: "border-slate-600 bg-slate-800/60 text-slate-300",
};

export function Badge({ tone = "slate", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-[11px] font-medium tracking-wide uppercase ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

const STATUS_TONE: Record<IncidentStatus, Tone> = {
  DETECTED: "rose",
  INVESTIGATING: "sky",
  ANALYZING: "violet",
  AWAITING_APPROVAL: "amber",
  REMEDIATING: "cyan",
  VERIFYING: "cyan",
  RESOLVED: "emerald",
  FAILED: "rose",
  ESCALATED: "amber",
};

export function StatusBadge({ status }: { status: IncidentStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{label(status)}</Badge>;
}

const SEVERITY_TONE: Record<Severity, Tone> = { LOW: "slate", MEDIUM: "amber", HIGH: "rose", CRITICAL: "rose" };

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={SEVERITY_TONE[severity]}>{severity}</Badge>;
}

const RISK_TONE: Record<RiskLevel, Tone> = { LOW: "emerald", MEDIUM: "amber", HIGH: "rose" };

export function RiskBadge({ risk }: { risk: RiskLevel }) {
  return <Badge tone={RISK_TONE[risk]}>Risk {risk}</Badge>;
}

export function ServiceStatusBadge({ status }: { status: ServiceStatus | null }) {
  if (!status) return <Badge>No data</Badge>;
  return <Badge tone={status === "HEALTHY" ? "emerald" : status === "DEGRADED" ? "amber" : "rose"}>{status}</Badge>;
}

export function Card({
  title,
  eyebrow,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  eyebrow?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/60 p-5 backdrop-blur ${className}`}>
      {(title || eyebrow || actions) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            {eyebrow && <p className="font-mono text-[11px] tracking-widest text-cyan-400 uppercase">{eyebrow}</p>}
            {title && <h2 className="text-base font-semibold text-slate-100">{title}</h2>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-slate-500">{children}</p>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
      {children}
    </p>
  );
}

/** Citation chips: hovering shows the backend-supplied fact behind each evidence id. */
export function EvidenceRefs({ ids, evidence }: { ids: string[]; evidence: Map<string, EvidenceItem> }) {
  if (ids.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {ids.map((id) => (
        <span
          key={id}
          title={evidence.get(id)?.fact ?? "Evidence not in this view"}
          className="cursor-help rounded border border-slate-700 bg-slate-800 px-1.5 font-mono text-[10px] text-cyan-300"
        >
          {id}
        </span>
      ))}
    </span>
  );
}

export function evidenceIndex(...lists: EvidenceItem[][]): Map<string, EvidenceItem> {
  const index = new Map<string, EvidenceItem>();
  for (const list of lists) for (const item of list) index.set(item.id, item);
  return index;
}

export function Meter({ value, tone = "cyan" }: { value: number; tone?: "cyan" | "emerald" | "amber" }) {
  const color = { cyan: "bg-cyan-400", emerald: "bg-emerald-400", amber: "bg-amber-400" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }} />
    </div>
  );
}
