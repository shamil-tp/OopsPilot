import Link from "next/link";

import { Empty, Indicator, RiskLabel, table, type Tone } from "@/components/ui";
import { label, shortDateTime } from "@/lib/format";
import type { CodeReview, CodeReviewStatus, FindingSeverity, ReviewFinding } from "@/types/api";

export const SEVERITY_TONE: Record<FindingSeverity, Tone> = {
  critical: "critical",
  high: "critical",
  medium: "warn",
  low: "neutral",
  info: "neutral",
};

const STATUS_TONE: Record<CodeReviewStatus, Tone> = {
  PENDING: "info",
  COMPLETED: "ok",
  FAILED: "critical",
  SKIPPED: "neutral",
};

export function ReviewStatus({ review }: { review: CodeReview }) {
  return <Indicator tone={STATUS_TONE[review.status]}>{label(review.status)}</Indicator>;
}

/** "2 issues · high" or "No issues". */
export function issueSummary(review: CodeReview): { text: string; tone: Tone } {
  if (review.status !== "COMPLETED") return { text: label(review.status), tone: STATUS_TONE[review.status] };
  if (review.findings.length === 0) return { text: "No issues · Clean", tone: "ok" };
  const worst = review.findings[0].severity;
  return {
    text: `${review.findings.length} issue${review.findings.length === 1 ? "" : "s"} · ${worst}`,
    tone: SEVERITY_TONE[worst],
  };
}

/**
 * Critical or high findings in a project's latest reviewed push. Health checks only see the
 * server's response, so a change that crashes in the browser (the page still loads with HTTP 200)
 * shows up here rather than as DOWN. A later push without such findings clears it.
 */
export function codeRisk(review: CodeReview | undefined): { severity: FindingSeverity; count: number } | null {
  if (!review || review.status !== "COMPLETED") return null;
  const serious = review.findings.filter((f) => f.severity === "critical" || f.severity === "high");
  if (serious.length === 0) return null;
  return { severity: serious.some((f) => f.severity === "critical") ? "critical" : "high", count: serious.length };
}

/** "Critical code issue", linked to the review; nothing when the latest push is clean. */
export function CodeRiskBadge({ review }: { review: CodeReview | undefined }) {
  const risk = codeRisk(review);
  if (!risk || !review) return null;
  return (
    <Link href={`/code-reviews/${review.id}`} className="hover:underline" title="Found in the latest reviewed push">
      <Indicator tone="critical">
        {risk.severity === "critical" ? "Critical" : "High-risk"} code issue{risk.count === 1 ? "" : "s"}
      </Indicator>
    </Link>
  );
}

export function fileUrl(review: CodeReview, finding: ReviewFinding): string {
  const path = finding.file.split("/").map(encodeURIComponent).join("/");
  return `https://github.com/${review.repository}/blob/${review.commit_sha}/${path}${finding.line ? `#L${finding.line}` : ""}`;
}

/** Each finding: severity, where, what is wrong, and how to fix it. */
export function FindingList({ review }: { review: CodeReview }) {
  if (review.findings.length === 0) {
    return <Empty>{review.status === "COMPLETED" ? "No issues found in this change." : (review.summary ?? "")}</Empty>;
  }
  return (
    <ol className="divide-y divide-line border-y border-line">
      {review.findings.map((finding, i) => (
        <li key={i} className="py-3 break-inside-avoid">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <Indicator tone={SEVERITY_TONE[finding.severity]}>
              <span className="font-medium uppercase">{finding.severity}</span>
            </Indicator>
            <span className="text-xs text-muted">{label(finding.category)}</span>
            <a
              href={fileUrl(review, finding)}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-xs break-all text-ink underline underline-offset-2"
            >
              {finding.file}
              {finding.line ? `:${finding.line}` : ""}
            </a>
          </div>
          <p className="mt-1 text-sm font-medium text-ink">{finding.title}</p>
          <p className="mt-1 text-sm text-ink">{finding.explanation}</p>
          <p className="mt-1 text-sm text-ink">
            <span className="font-medium">How to fix: </span>
            {finding.recommendation}
          </p>
        </li>
      ))}
    </ol>
  );
}

/** Reviews of pushes, newest first. */
export function ReviewTable({ reviews, showService = true }: { reviews: CodeReview[]; showService?: boolean }) {
  if (reviews.length === 0) {
    return <Empty>No code reviews yet. Each push to a project&apos;s main branch is reviewed automatically.</Empty>;
  }
  return (
    <div className={table.wrap}>
      <table className={table.table}>
        <thead>
          <tr>
            <th scope="col" className={table.th}>Commit</th>
            {showService && <th scope="col" className={`${table.th} hidden md:table-cell`}>Project</th>}
            <th scope="col" className={table.th}>Issues</th>
            <th scope="col" className={`${table.th} hidden sm:table-cell`}>Risk</th>
            <th scope="col" className={`${table.th} text-right`}>Pushed (UTC)</th>
          </tr>
        </thead>
        <tbody>
          {reviews.map((review) => {
            const issues = issueSummary(review);
            return (
              <tr key={review.id} className="hover:bg-hover">
                <td className={table.td}>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <Link
                      href={`/code-reviews/${review.id}`}
                      className="font-mono text-[13px] font-medium text-ink underline-offset-2 hover:underline"
                    >
                      {review.commit_sha.slice(0, 7)}
                    </Link>
                    {showService && (
                      <span className="font-mono text-[11px] text-muted md:hidden">
                        · {review.service_name}
                      </span>
                    )}
                  </div>
                  <span className="block max-w-xs text-xs break-words text-muted sm:truncate">
                    {review.commit_message ?? review.branch}
                  </span>
                </td>
                {showService && (
                  <td className={`${table.td} ${table.mono} hidden md:table-cell`}>{review.service_name}</td>
                )}
                <td className={table.td}>
                  <div className="flex flex-col">
                    <Indicator tone={issues.tone}>{issues.text}</Indicator>
                    {review.findings.length > 0 ? (
                      <span className="mt-0.5 block max-w-xs text-xs text-muted truncate sm:max-w-sm" title={review.findings[0].title}>
                        {review.findings[0].title}
                      </span>
                    ) : review.status === "COMPLETED" ? (
                      <span className="mt-0.5 block max-w-xs text-xs text-muted truncate sm:max-w-sm">
                        All clear · Clean change
                      </span>
                    ) : null}
                  </div>
                </td>
                <td className={`${table.td} hidden sm:table-cell`}>
                  {review.risk ? <RiskLabel risk={review.risk} /> : <span className="text-muted">—</span>}
                </td>
                <td className={`${table.td} text-right whitespace-nowrap text-muted`}>
                  {shortDateTime(review.created_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
