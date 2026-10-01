"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { FindingList, issueSummary, ReviewStatus, ReviewTable } from "@/components/code-review/Findings";
import { Button, Empty, ErrorNote, Indicator, PageHeader, PrintButton, RiskLabel, Section, table } from "@/components/ui";
import { ApiError, getCodeReview, listCodeReviews, retryCodeReview } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { CodeReview } from "@/types/api";

const REFRESH_MS = 10_000;

export function CodeReviewList() {
  const [reviews, setReviews] = useState<CodeReview[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      listCodeReviews({ limit: 50 })
        .then((rows) => {
          if (!cancelled) {
            setReviews(rows);
            setError(null);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load code reviews");
        });
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Code reviews"
        description="Every push to a monitored project's main branch is reviewed automatically: what could break, and how to fix it."
      />
      {error && <ErrorNote>{error}</ErrorNote>}
      {reviews === null ? (
        !error && <Empty>Loading code reviews…</Empty>
      ) : (
        <ReviewTable reviews={reviews} />
      )}
    </div>
  );
}

export function CodeReviewDetail({ reviewId }: { reviewId: number }) {
  const [review, setReview] = useState<CodeReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  const load = useCallback(async () => {
    try {
      setReview(await getCodeReview(reviewId));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the code review");
    }
  }, [reviewId]);

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      getCodeReview(reviewId)
        .then((row) => {
          if (!cancelled) {
            setReview(row);
            setError(null);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load the code review");
        });
    void tick();
    const timer = setInterval(() => void tick(), 5000); // pending reviews finish in seconds
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [reviewId]);

  async function retry() {
    setRetrying(true);
    try {
      await retryCodeReview(reviewId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not retry");
    } finally {
      setRetrying(false);
      await load();
    }
  }

  if (!review) return error ? <ErrorNote>{error}</ErrorNote> : <Empty>Loading code review…</Empty>;
  const issues = issueSummary(review);
  const commitUrl = `https://github.com/${review.repository}/commit/${review.commit_sha}`;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <nav aria-label="Breadcrumb" className="text-xs text-muted print:hidden">
          <Link href="/code-reviews" className="hover:text-ink">
            Code reviews
          </Link>
          <span aria-hidden className="mx-1.5">/</span>
          <span className="font-mono text-ink">{review.commit_sha.slice(0, 7)}</span>
        </nav>
        <PageHeader
          title={`Code review · ${review.commit_message ?? review.commit_sha.slice(0, 7)}`}
          description={
            <>
              <a href={commitUrl} target="_blank" rel="noreferrer" className="font-mono text-ink underline underline-offset-2">
                {review.repository}@{review.commit_sha.slice(0, 7)}
              </a>
              {` · ${review.branch ?? "unknown branch"}`}
              {review.author && ` · ${review.author}`}
              {` · pushed ${dateTime(review.created_at)}`}
            </>
          }
          actions={
            <>
              {review.status === "FAILED" && (
                <Button className="print:hidden" disabled={retrying} onClick={() => void retry()}>
                  {retrying ? "Retrying…" : "Retry review"}
                </Button>
              )}
              <PrintButton />
            </>
          }
        />
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <ReviewStatus review={review} />
          <Indicator tone={issues.tone}>{issues.text}</Indicator>
          {review.risk && (
            <span className="text-muted">
              Deployment risk <RiskLabel risk={review.risk} />
            </span>
          )}
          <span className="text-xs text-muted print:hidden">
            Project{" "}
            <Link href={`/projects/${review.service_name}`} className="font-mono text-ink underline underline-offset-2">
              {review.service_name}
            </Link>
          </span>
        </p>
        {error && <ErrorNote>{error}</ErrorNote>}
        {review.error && <ErrorNote>{review.error}</ErrorNote>}
      </div>

      {review.status === "PENDING" ? (
        <Empty>Reviewing the change… this usually takes a few seconds.</Empty>
      ) : (
        <>
          <Section title="Summary" aside={review.model ?? undefined}>
            <p className="max-w-3xl text-sm text-ink">{review.summary ?? "—"}</p>
          </Section>

          <Section title="Issues and fixes" aside={`${review.findings.length} finding(s), most severe first`}>
            <FindingList review={review} />
          </Section>

          <Section title="Files" aside={review.truncated ? "Cut to the review size limit" : undefined}>
            <div className={table.wrap}>
              <table className={table.table}>
                <thead>
                  <tr>
                    <th scope="col" className={table.th}>File</th>
                    <th scope="col" className={table.th}>Reviewed</th>
                    <th scope="col" className={`${table.th} hidden text-right sm:table-cell`}>Changes</th>
                  </tr>
                </thead>
                <tbody>
                  {review.files.map((f) => (
                    <tr key={f.filename}>
                      <td className={`${table.td} font-mono text-xs break-all`}>{f.filename}</td>
                      <td className={table.td}>
                        <Indicator tone="ok">Yes</Indicator>
                      </td>
                      <td className={`${table.td} ${table.mono} hidden text-right whitespace-nowrap sm:table-cell`}>
                        <span className="text-emerald-700">+{f.additions}</span>{" "}
                        <span className="text-red-700">-{f.deletions}</span>
                      </td>
                    </tr>
                  ))}
                  {review.skipped_files.map((f) => (
                    <tr key={`skip-${f.filename}`}>
                      <td className={`${table.td} font-mono text-xs break-all text-muted`}>{f.filename}</td>
                      <td className={`${table.td} text-xs text-muted`} colSpan={2}>
                        Not reviewed: {f.reason}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-muted">
              Secrets and secret files are never sent for review: credentials found in the change are replaced with
              [REDACTED] first. The review reports and recommends; it never changes your repository.
            </p>
          </Section>
        </>
      )}
    </div>
  );
}
