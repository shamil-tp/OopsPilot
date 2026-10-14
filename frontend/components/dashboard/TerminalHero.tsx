import { WindowDots } from "@/components/ui";

// 5-row block glyphs for the banner ("OPSPILOT").
const GLYPHS: Record<string, string[]> = {
  O: [" ███ ", "█   █", "█   █", "█   █", " ███ "],
  P: ["████ ", "█   █", "████ ", "█    ", "█    "],
  S: [" ████", "█    ", " ███ ", "    █", "████ "],
  I: ["███", " █ ", " █ ", " █ ", "███"],
  L: ["█    ", "█    ", "█    ", "█    ", "█████"],
  T: ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
};

const BANNER = [0, 1, 2, 3, 4]
  .map((row) => [..."OPSPILOT"].map((ch) => GLYPHS[ch][row]).join("  ").trimEnd())
  .join("\n");

const COMMANDS: [string, string][] = [
  ["detect", "watch services for anomalies and open an incident"],
  ["investigate", "agents pull logs, health, deployments, past incidents"],
  ["root-cause", "correlate the evidence into the most likely cause"],
  ["approve", "a human signs off before any risky action runs"],
  ["recover", "roll back, verify recovery, write the report"],
];

function Prompt({ children, cursor = false }: { children?: string; cursor?: boolean }) {
  return (
    <p className={cursor ? "cursor" : undefined}>
      <span className="text-term">opspilot@prod</span>
      <span className="text-muted">:</span>
      <span className="text-sky-400">~</span>
      <span className="text-muted">$ </span>
      {children && <span className="text-ink">{children}</span>}
    </p>
  );
}

/**
 * Dashboard banner: a terminal session with the OpsPilot banner and its workflow.
 * `live` is null while incident data is unavailable (loading, or the API is unreachable). */
export function TerminalHero({ live }: { live: boolean | null }) {
  return (
    <div className="border border-line-strong bg-panel shadow-pixel">
      <div className="flex items-center gap-3 border-b border-line-strong bg-hover px-3 py-1.5">
        <WindowDots />
        <p className="flex-1 truncate text-center text-xs text-muted">opspilot — zsh — 120×32</p>
        <span aria-hidden className="w-12" />
      </div>

      <div className="space-y-3 overflow-x-auto p-4 font-mono text-[13px] leading-relaxed sm:p-6">
        <Prompt>opspilot --banner</Prompt>
        <pre
          aria-label="OpsPilot"
          className="glow text-[7px] leading-[1.05] text-term sm:text-[10px] md:text-xs"
        >
          {BANNER}
        </pre>
        <p className="text-muted">
          AI-Powered Autonomous Incident Response &amp; DevOps Copilot{" "}
          <span className="text-subtle">· powered by Gemini</span>
        </p>

        <Prompt>opspilot --help</Prompt>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-0.5 pl-2">
          {COMMANDS.map(([cmd, text]) => (
            <div key={cmd} className="contents">
              <dt className="text-term">{cmd}</dt>
              <dd className="text-muted">{text}</dd>
            </div>
          ))}
        </dl>

        <Prompt>opspilot status</Prompt>
        <p className="pl-2">
          {live === null ? (
            <span className="text-amber-400">
              <span className="font-bold">[WAIT]</span> incident data unavailable
            </span>
          ) : live ? (
            <span className="text-red-400">
              <span className="font-bold">[ALERT]</span> incident in progress: open it below
            </span>
          ) : (
            <span className="text-emerald-400">
              <span className="font-bold">[ OK ]</span> no active incidents
            </span>
          )}
        </p>
        <Prompt cursor />
      </div>
    </div>
  );
}
