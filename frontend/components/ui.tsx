import type { ButtonHTMLAttributes, ReactNode } from "react";

import { label } from "@/lib/format";
import type { EvidenceItem, IncidentStatus, RiskLevel, ServiceStatus, Severity } from "@/types/api";

/** Semantic tones. Color always accompanies text, never replaces it. */
export type Tone = "ok" | "warn" | "critical" | "info" | "neutral";

const DOT: Record<Tone, string> = {
  ok: "bg-emerald-400 shadow-[0_0_6px_rgb(52_211_153/0.8)]",
  warn: "bg-amber-400 shadow-[0_0_6px_rgb(251_191_36/0.8)]",
  critical: "bg-red-500 shadow-[0_0_6px_rgb(239_68_68/0.9)]",
  info: "bg-sky-400 shadow-[0_0_6px_rgb(56_189_248/0.8)]",
  neutral: "bg-zinc-500",
};

export const TEXT: Record<Tone, string> = {
  ok: "text-emerald-400",
  warn: "text-amber-400",
  critical: "text-red-400",
  info: "text-sky-400",
  neutral: "text-muted",
};

export function Dot({ tone, className = "" }: { tone: Tone; className?: string }) {
  return <span aria-hidden className={`inline-block h-2 w-2 shrink-0 ${DOT[tone]} ${className}`} />;
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
        <p aria-hidden className="mb-1 text-xs text-muted">
          <span className="text-term">opspilot@prod</span>:<span className="text-sky-400">~</span>$ open
        </p>
        <h1 className="cursor text-4xl leading-none text-term sm:text-5xl">{title}</h1>
        {description && <p className="mt-2 text-sm text-muted"># {description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** A titled region of a page, framed as a terminal window with a title bar. */
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
    <section
      id={id}
      aria-labelledby={id ? `${id}-title` : undefined}
      className={`min-w-0 scroll-mt-6 border border-line-strong bg-panel/80 shadow-pixel ${className}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-line-strong bg-hover px-3 py-1.5">
        <div className="flex min-w-0 items-center gap-3">
          <WindowDots />
          <h2 id={id ? `${id}-title` : undefined} className="truncate text-xl leading-none text-term">
            <span aria-hidden className="text-muted">~/</span>
            {title}
          </h2>
        </div>
        {aside && <div className="text-xs text-muted">{aside}</div>}
      </div>
      <div className="p-3 sm:p-4">{children}</div>
    </section>
  );
}

/** A small uppercase label for sub-parts of a section. */
export function Subheading({ children }: { children: ReactNode }) {
  return <h3 className="mt-5 mb-2 text-xs font-semibold tracking-wider text-term uppercase before:text-muted before:content-['##_']">{children}</h3>;
}

/** The three window-control lights of a terminal title bar. */
export function WindowDots() {
  return (
    <span aria-hidden className="flex shrink-0 gap-1.5">
      <span className="h-2.5 w-2.5 rounded-full bg-red-500/80" />
      <span className="h-2.5 w-2.5 rounded-full bg-amber-400/80" />
      <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/80" />
    </span>
  );
}

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`border border-line-strong bg-panel shadow-pixel ${className}`}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted before:content-['//_']">{children}</p>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="border border-red-500/60 bg-red-950/40 px-3 py-2 text-sm text-red-300 before:font-bold before:content-['[ERR]_']">
      {children}
    </p>
  );
}

// --- controls ------------------------------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "quiet";

const BUTTON: Record<ButtonVariant, string> = {
  primary:
    "border border-term bg-term text-canvas shadow-pixel-sm hover:bg-[#7dffb0] disabled:border-line-strong disabled:bg-line disabled:text-muted",
  secondary: "border border-term/50 text-term hover:bg-term/10 hover:border-term disabled:border-line disabled:text-subtle",
  quiet: "border border-transparent text-muted hover:text-term disabled:text-subtle",
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
      className={`inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-bold tracking-wider uppercase transition-colors before:opacity-60 before:content-['['] after:opacity-60 after:content-[']'] active:translate-y-px disabled:cursor-not-allowed ${BUTTON[variant]} ${className}`}
    />
  );
}

// --- tables --------------------------------------------------------------------------------------

export const table = {
  wrap: "overflow-x-auto",
  table: "w-full border-collapse text-left text-sm",
  th: "border-b border-line-strong px-3 py-2 text-[11px] font-semibold tracking-wider whitespace-nowrap text-muted uppercase first:pl-0 last:pr-0",
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
            className="cursor-help border border-line-strong bg-canvas px-1 font-mono text-[11px] text-term no-underline"
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
