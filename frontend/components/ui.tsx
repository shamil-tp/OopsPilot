import type { ButtonHTMLAttributes, ReactNode } from "react";

import { label } from "@/lib/format";
import type { EvidenceItem, IncidentStatus, RiskLevel, ServiceStatus, Severity } from "@/types/api";

/** Semantic tones. Color always accompanies text, never replaces it. */
export type Tone = "ok" | "warn" | "critical" | "info" | "neutral";

const DOT: Record<Tone, string> = {
  ok: "bg-emerald-600",
  warn: "bg-amber-500",
  critical: "bg-red-600",
  info: "bg-blue-600",
  neutral: "bg-zinc-400",
};

export const TEXT: Record<Tone, string> = {
  ok: "text-emerald-700",
  warn: "text-amber-700",
  critical: "text-red-700",
  info: "text-blue-700",
  neutral: "text-muted",
};

export function Dot({ tone, className = "" }: { tone: Tone; className?: string }) {
  return <span aria-hidden className={`inline-block h-2 w-2 shrink-0 rounded-full ${DOT[tone]} ${className}`} />;
}

/** Dot + text, e.g. "● Investigating". */
export function Indicator({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <Dot tone={tone} />
      <span className={tone === "neutral" ? "text-ink" : TEXT[tone]}>{children}</span>
    </span>
  );
}

export const STATUS_TONE: Record<IncidentStatus, Tone> = {
  DETECTED: "critical",
  INVESTIGATING: "info",
  ANALYZING: "info",
  AWAITING_APPROVAL: "warn",
  REMEDIATING: "info",
  VERIFYING: "info",
  RESOLVED: "ok",
  FAILED: "critical",
  ESCALATED: "warn",
};

export function StatusIndicator({ status }: { status: IncidentStatus }) {
  return <Indicator tone={STATUS_TONE[status]}>{label(status)}</Indicator>;
}

const SEVERITY_TONE: Record<Severity, Tone> = { CRITICAL: "critical", HIGH: "critical", MEDIUM: "warn", LOW: "neutral" };

export function SeverityLabel({ severity }: { severity: Severity }) {
  return (
    <span className={`text-xs font-semibold tracking-wide ${TEXT[SEVERITY_TONE[severity]]}`}>
      <span className="sr-only">Severity </span>
      {severity}
    </span>
  );
}

export function ServiceStatusIndicator({ status }: { status: ServiceStatus | null }) {
  if (!status) return <Indicator tone="neutral">No data</Indicator>;
  const tone: Tone = status === "HEALTHY" ? "ok" : status === "DEGRADED" ? "warn" : "critical";
  return <Indicator tone={tone}>{label(status)}</Indicator>;
}

const RISK_TONE: Record<RiskLevel, Tone> = { LOW: "neutral", MEDIUM: "warn", HIGH: "critical" };

export function RiskLabel({ risk }: { risk: RiskLevel }) {
  return <span className={`font-medium ${TEXT[RISK_TONE[risk]]}`}>{label(risk)}</span>;
}

// --- layout --------------------------------------------------------------------------------------

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** A titled region of a page, separated by a rule rather than wrapped in a card. */
export function Section({
  id,
  title,
  aside,
  children,
  className = "",
}: {
  id?: string;
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} aria-labelledby={id ? `${id}-title` : undefined} className={`scroll-mt-6 ${className}`}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line pb-2">
        <h2 id={id ? `${id}-title` : undefined} className="text-[15px] font-semibold text-ink">
          {title}
        </h2>
        {aside && <div className="text-xs text-muted">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

/** A small uppercase label for sub-parts of a section. */
export function Subheading({ children }: { children: ReactNode }) {
  return <h3 className="mt-5 mb-2 text-xs font-medium tracking-wide text-muted uppercase">{children}</h3>;
}

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-md border border-line bg-panel ${className}`}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="border-l-2 border-red-600 bg-red-50 px-3 py-2 text-sm text-red-800">
      {children}
    </p>
  );
}

// --- controls ------------------------------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "quiet";

const BUTTON: Record<ButtonVariant, string> = {
  primary: "bg-ink text-white hover:bg-zinc-700 disabled:bg-zinc-400",
  secondary: "border border-line-strong bg-panel text-ink hover:bg-hover disabled:text-subtle",
  quiet: "text-muted hover:text-ink disabled:text-subtle",
};

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${BUTTON[variant]} ${className}`}
    />
  );
}

// --- tables --------------------------------------------------------------------------------------

export const table = {
  wrap: "overflow-x-auto",
  table: "w-full border-collapse text-left text-sm",
  th: "border-b border-line px-3 py-2 text-xs font-medium whitespace-nowrap text-muted first:pl-0 last:pr-0",
  td: "border-b border-line px-3 py-2 align-top first:pl-0 last:pr-0",
  mono: "font-mono text-[13px]",
};

// --- evidence citations --------------------------------------------------------------------------

/** Citation ids; hovering (or focusing) one shows the backend-supplied fact behind it. */
export function EvidenceRefs({ ids, evidence }: { ids: string[]; evidence: Map<string, EvidenceItem> }) {
  if (ids.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-baseline">
      {ids.map((id) => {
        const fact = evidence.get(id)?.fact ?? "Evidence not in this view";
        return (
          <abbr
            key={id}
            title={fact}
            tabIndex={0}
            aria-label={`Evidence ${id}: ${fact}`}
            className="cursor-help rounded-sm border border-line px-1 font-mono text-[11px] text-muted no-underline"
          >
            {id}
          </abbr>
        );
      })}
    </span>
  );
}

export function evidenceIndex(...lists: EvidenceItem[][]): Map<string, EvidenceItem> {
  const index = new Map<string, EvidenceItem>();
  for (const list of lists) for (const item of list) index.set(item.id, item);
  return index;
}

/** Opens the browser's print dialog; choose "Save as PDF". Hidden in the printed document. */
export function PrintButton({ label = "Download PDF" }: { label?: string }) {
  return (
    <Button className="print:hidden" onClick={() => window.print()}>
      {label}
    </Button>
  );
}
