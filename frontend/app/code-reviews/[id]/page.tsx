import { notFound } from "next/navigation";

import { CodeReviewDetail } from "@/components/code-review/CodeReviewPages";

export default async function CodeReviewPage({ params }: PageProps<"/code-reviews/[id]">) {
  const { id } = await params;
  const reviewId = Number(id);
  if (!Number.isInteger(reviewId) || reviewId <= 0) notFound();

  return <CodeReviewDetail reviewId={reviewId} />;
}
