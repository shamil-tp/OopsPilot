import { CicdEventRow } from "@/components/cicd/CicdEventRow";
import { Badge, Card, Empty } from "@/components/ui";
import { time } from "@/lib/format";
import type { CicdEvent, EvidenceItem, InvestigationRun } from "@/types/api";

type Entry =
  | { kind: "cicd"; at: number; event: CicdEvent; citation?: string }
  | { kind: "error"; at: number; item: EvidenceItem }
  | { kind: "detected"; at: number };

function minutesBetween(from: number, to: number): string {
  const seconds = Math.round((to - from) / 1000);
  return Math.abs(seconds) < 120 ? `${seconds} s` : `${Math.round(seconds / 60)} min`;
}

/**
 * CI/CD evidence for the incident: the service's GitHub events, the first error the investigation
 * found, and detection, in time order. Everything shown is stored backend data; the citation chips
 * (C1, L5, ...) are the evidence ids the agents cite.
 */
export function CicdEvidencePanel({
  cicd,
  investigation,
  detectedAt,
}: {
  cicd: CicdEvent[];
  investigation: InvestigationRun | null;
  detectedAt: string;
}) {
  const evidence = investigation?.result?.evidence ?? [];
  const citations = new Map(
    evidence.filter((e) => e.source === "cicd").map((e) => [Number(e.data.id), e.id] as const),
  );
  const firstError = evidence
    .filter((e) => e.source === "logs" && e.data.level === "ERROR" && e.timestamp)
    .sort((a, b) => Date.parse(a.timestamp ?? "") - Date.parse(b.timestamp ?? ""))[0];
  const detected = Date.parse(detectedAt);

  const entries: Entry[] = [
    ...cicd.map((event) => ({
      kind: "cicd" as const,
      at: Date.parse(event.occurred_at),
      event,
      citation: citations.get(event.id),
    })),
    ...(firstError ? [{ kind: "error" as const, at: Date.parse(firstError.timestamp ?? ""), item: firstError }] : []),
    { kind: "detected" as const, at: detected },
  ].sort((a, b) => a.at - b.at);

  const deploy = [...cicd]
    .filter((e) => e.category === "DEPLOYMENT" && e.conclusion === "SUCCESS" && Date.parse(e.occurred_at) <= detected)
    .sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at))[0];

  return (
    <Card
      eyebrow="GitHub · CI/CD evidence"
      title="What changed before the incident"
      actions={<Badge tone="cyan">{cicd.length} CI/CD events</Badge>}
    >
      {cicd.length === 0 ? (
        <Empty>No GitHub CI/CD events for this service in the 2 h before detection.</Empty>
      ) : (
        <>
          {deploy && (
            <p className="mb-4 text-sm text-slate-300">
              <span className="font-mono text-cyan-300">{deploy.workflow_name ?? "Deployment"}</span>
              {deploy.version ? ` shipped ${deploy.version}` : " deployed"} (commit{" "}
              <span className="font-mono">{deploy.commit_sha?.slice(0, 7) ?? "unknown"}</span>){" "}
              {minutesBetween(Date.parse(deploy.occurred_at), detected)} before detection
              {firstError?.timestamp &&
                `; the first error followed ${minutesBetween(Date.parse(deploy.occurred_at), Date.parse(firstError.timestamp))} after it completed`}
              .
            </p>
          )}
          <ol className="space-y-3 border-l border-slate-800 pl-4">
            {entries.map((entry) => (
              <li key={`${entry.kind}-${entry.kind === "cicd" ? entry.event.id : entry.at}`}>
                {entry.kind === "cicd" && (
                  <CicdEventRow
                    event={entry.event}
                    extra={
                      entry.citation && (
                        <span className="rounded border border-slate-700 bg-slate-800 px-1.5 font-mono text-[10px] text-cyan-300">
                          {entry.citation}
                        </span>
                      )
                    }
                  />
                )}
                {entry.kind === "error" && (
                  <div className="flex gap-3 text-sm">
                    <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-rose-500/50 bg-rose-500/10 text-xs">
                      ✖
                    </span>
                    <p className="min-w-0 text-rose-200">
                      <span className="mr-2 font-mono text-[11px] text-slate-500">{time(entry.item.timestamp)}</span>
                      First error: {String(entry.item.data.message ?? entry.item.fact)}{" "}
                      <span className="rounded border border-slate-700 bg-slate-800 px-1.5 font-mono text-[10px] text-cyan-300">
                        {entry.item.id}
                      </span>
                    </p>
                  </div>
                )}
                {entry.kind === "detected" && (
                  <div className="flex gap-3 text-sm">
                    <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-amber-400/50 bg-amber-400/10 text-xs">
                      🚨
                    </span>
                    <p className="text-amber-200">
                      <span className="mr-2 font-mono text-[11px] text-slate-500">{time(detectedAt)}</span>
                      Incident detected
                    </p>
                  </div>
                )}
              </li>
            ))}
          </ol>
          {!investigation?.result && (
            <p className="mt-3 text-[11px] text-slate-500">
              The investigation cites these events as C1, C2, … once it runs.
            </p>
          )}
        </>
      )}
    </Card>
  );
}
