import { Suspense } from "react";
import type { Metadata } from "next";
import { CicdDashboard } from "@/components/cicd/CicdDashboard";
import { Empty } from "@/components/ui";

export const metadata: Metadata = {
  title: "CI/CD Activity · OpsPilot",
  description: "Live GitHub commits, workflow runs, and production deployments captured via webhooks.",
};

export default function CicdPage() {
  return (
    <Suspense fallback={<Empty>Loading CI/CD activity…</Empty>}>
      <CicdDashboard />
    </Suspense>
  );
}
