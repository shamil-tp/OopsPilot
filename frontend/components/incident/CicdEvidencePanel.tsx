import { CicdTable, type ContextRow } from "@/components/cicd/CicdTable";
import { Empty, EvidenceRefs, evidenceIndex, Section } from "@/components/ui";
import type { CicdEvent, InvestigationRun } from "@/types/api";

function gap(fromIso: string, toIso: string): string {
  const seconds = Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 1000);
  return Math.abs(seconds) < 120 ? `${seconds} s` : `${Math.round(seconds / 60)} min`;
}

/**
 * What changed before the incident: the service's GitHub events, the first error the
 * investigation found, and detection, in time order. Everything shown is stored backend data;
 * citation ids (C1, L5, ...) are the evidence ids the agents cite.
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
  const index = evidenceIndex(evidence);
  const citations = new Map(evidence.filter((e) => e.source === "cicd").map((e) => [Number(e.data.id), e.id] as const));
  const firstError = evidence
    .filter((e) => e.source === "logs" && e.data.level === "ERROR" && e.timestamp)
    .sort((a, b) => Date.parse(a.timestamp ?? "") - Date.parse(b.timestamp ?? ""))[0];
  const detected = Date.parse(detectedAt);
  const deploy = cicd
    .filter((e) => e.category === "DEPLOYMENT" && e.conclusion === "SUCCESS" && Date.parse(e.occurred_at) <= detected)
    .sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at))[0];

  const context: ContextRow[] = [
    ...(firstError?.timestamp
      ? [
          {
            key: "first-error",
            at: firstError.timestamp,
            tone: "critical" as const,
            text: `First error: ${String(firstError.data.message ?? firstError.fact)}`,
            cite: <EvidenceRefs ids={[firstError.id]} evidence={index} />,
          },
        ]
      : []),
    { key: "detected", at: detectedAt, tone: "critical", text: "Incident detected" },
  ];

  return (
    <Section id="changes" title="What changed" aside={`${cicd.length} GitHub event${cicd.length === 1 ? "" : "s"} in the 2 h before detection`}>
      {cicd.length === 0 ? (
        <Empty>No GitHub CI/CD events for this service in the 2 hours before detection.</Empty>
      ) : (
        <>
          {deploy && (
            <p className="mb-3 text-sm text-ink">
              <span className="font-mono">{deploy.workflow_name ?? "A deployment"}</span>
              {deploy.version ? ` shipped ${deploy.version}` : " completed"} (commit{" "}
              <span className="font-mono">{deploy.commit_sha?.slice(0, 7) ?? "unknown"}</span>){" "}
              {gap(deploy.occurred_at, detectedAt)} before detection
              {firstError?.timestamp && `; the first error followed ${gap(deploy.occurred_at, firstError.timestamp)} later`}.
            </p>
          )}
          <CicdTable
            events={cicd}
            context={context}
            cite={(event) => {
              const id = citations.get(event.id);
              return id ? <EvidenceRefs ids={[id]} evidence={index} /> : null;
            }}
          />
          {!investigation?.result && (
            <p className="mt-2 text-xs text-muted">The investigation cites these events as C1, C2, … once it runs.</p>
          )}
        </>
      )}
    </Section>
  );
}
