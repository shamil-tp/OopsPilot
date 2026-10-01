"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ReportPanel } from "@/components/incident/ReportPanel";
import { Empty, ErrorNote } from "@/components/ui";
import { ApiError, getReport } from "@/lib/api";
import type { IncidentReport } from "@/types/api";

/** The stored incident report alone, laid out for "Save as PDF". */
export function IncidentReportPage({ incidentId }: { incidentId: number }) {
  const [report, setReport] = useState<IncidentReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getReport(incidentId)
      .then((row) => {
        if (cancelled) return;
        if (row) setReport(row);
        else setError("This incident has no report yet: it is created after verification.");
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load the report");
      });
    return () => {
      cancelled = true;
    };
  }, [incidentId]);

  return (
    <div className="flex flex-col gap-4">
      <nav aria-label="Breadcrumb" className="text-xs text-muted print:hidden">
        <Link href={`/incidents/${incidentId}`} className="hover:text-ink">
          ← Back to the incident
        </Link>
      </nav>
      {error ? <ErrorNote>{error}</ErrorNote> : !report ? <Empty>Loading report…</Empty> : <ReportPanel report={report} printable />}
    </div>
  );
}
