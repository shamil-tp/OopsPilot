import { notFound } from "next/navigation";

import { ProjectDetail } from "@/components/projects/ProjectDetail";

export default async function ProjectPage({ params }: PageProps<"/projects/[service]">) {
  const { service } = await params;
  if (!/^[a-z0-9][a-z0-9-]{1,62}$/.test(service)) notFound();

  return <ProjectDetail service={service} />;
}
