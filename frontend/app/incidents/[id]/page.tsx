import { notFound } from "next/navigation";

import { IncidentConsole } from "@/components/incident/IncidentConsole";

export default async function IncidentPage({ params }: PageProps<"/incidents/[id]">) {
  const { id } = await params;
  const incidentId = Number(id);
  if (!Number.isInteger(incidentId) || incidentId <= 0) notFound();

  return <IncidentConsole incidentId={incidentId} />;
}
