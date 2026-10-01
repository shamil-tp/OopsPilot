import { notFound } from "next/navigation";

import { IncidentReportPage } from "@/components/incident/IncidentReportPage";

export default async function ReportPage({ params }: PageProps<"/incidents/[id]/report">) {
  const { id } = await params;
  const incidentId = Number(id);
  if (!Number.isInteger(incidentId) || incidentId <= 0) notFound();

  return <IncidentReportPage incidentId={incidentId} />;
}
