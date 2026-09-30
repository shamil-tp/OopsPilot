import { notFound } from "next/navigation";

import { IncidentConsole } from "@/components/incident/IncidentConsole";

export default async function IncidentPage({ params }: PageProps<"/incidents/[id]">) {
  const { id } = await params;
  const incidentId = Number(id);
  if (!Number.isInteger(incidentId) || incidentId <= 0) notFound();

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 py-8 sm:px-6">
      <IncidentConsole incidentId={incidentId} />
    </main>
  );
}
