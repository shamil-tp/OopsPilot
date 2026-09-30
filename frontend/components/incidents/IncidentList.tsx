"use client";

import { useEffect, useState } from "react";

import { IncidentTable } from "@/components/incidents/IncidentTable";
import { Empty, ErrorNote, PageHeader } from "@/components/ui";
import { ApiError, listIncidents } from "@/lib/api";
import { TERMINAL } from "@/lib/format";
import type { Incident } from "@/types/api";

const REFRESH_MS = 5000;

export function IncidentList() {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      listIncidents()
        .then((rows) => {
          if (cancelled) return;
          setIncidents(rows);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load incidents");
        });
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const open = incidents?.filter((i) => !TERMINAL.includes(i.status)).length ?? 0;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Incidents"
        description={incidents ? `${incidents.length} total · ${open} open` : "All incidents, newest first."}
      />
      {error && <ErrorNote>{error}</ErrorNote>}
      {!incidents ? (
        <Empty>Loading incidents…</Empty>
      ) : (
        <IncidentTable incidents={incidents} empty="No incidents yet. Use Simulate incident on the Overview page." />
      )}
    </div>
  );
}
