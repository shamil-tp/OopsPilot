"use client";

import { useState } from "react";
import Link from "next/link";
import { Button, Dot, Panel, type Tone } from "@/components/ui";
import { dateTime, label } from "@/lib/format";
import type { CicdEvent } from "@/types/api";
import { cicdOutcome, cicdTitle } from "@/components/cicd/CicdTable";

interface CicdEventModalProps {
  event: CicdEvent | null;
  isOpen: boolean;
  onClose: () => void;
}

export function CicdEventModal({ event, isOpen, onClose }: CicdEventModalProps) {
  const [copiedJson, setCopiedJson] = useState(false);

  if (!isOpen || !event) return null;

  const outcome = cicdOutcome(event);
  const metadataJson = JSON.stringify(event.metadata ?? {}, null, 2);

  const handleCopyJson = async () => {
    await navigator.clipboard.writeText(metadataJson);
    setCopiedJson(true);
    setTimeout(() => setCopiedJson(false), 2000);
  };

  const commitLink =
    event.commit_sha && event.repository
      ? `https://github.com/${event.repository}/commit/${event.commit_sha}`
      : null;

  const repoLink = event.repository ? `https://github.com/${event.repository}` : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-xs">
      <div
        className="w-full max-w-2xl max-h-[90vh] flex flex-col rounded-xl border border-line bg-panel shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cicd-modal-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line px-6 py-4 bg-canvas/40">
          <div className="flex items-center gap-2.5">
            <Dot tone={outcome.tone} className="h-2.5 w-2.5" />
            <div>
              <h2 id="cicd-modal-title" className="text-base font-semibold text-ink">
                {cicdTitle(event)}
              </h2>
              <p className="text-xs text-muted">
                Event #{event.id} · Received {dateTime(event.received_at || event.occurred_at)}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted hover:text-ink text-sm px-2 py-1 rounded"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto flex flex-col gap-6 text-sm">
          {/* Status banner */}
          <div className="flex flex-wrap items-center justify-between gap-3 p-3.5 rounded-lg border border-line bg-canvas">
            <div className="flex items-center gap-2">
              <span className="text-xs uppercase font-medium tracking-wide text-muted">Outcome:</span>
              <span className="font-semibold text-ink">{outcome.text}</span>
              {event.status && (
                <span className="text-xs text-muted">({label(event.status)})</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {event.html_url && (
                <a
                  href={event.html_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-ink hover:underline"
                >
                  View on GitHub ↗
                </a>
              )}
              {event.service_name && (
                <Link
                  href={`/projects/${event.service_name}`}
                  className="inline-flex items-center gap-1 text-xs font-medium text-ink hover:underline"
                >
                  View project ({event.service_name}) ↗
                </Link>
              )}
            </div>
          </div>

          {/* Details grid */}
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
            <div>
              <dt className="text-xs text-muted">Category</dt>
              <dd className="mt-0.5 font-medium text-ink font-mono text-xs">{event.category}</dd>
            </div>

            <div>
              <dt className="text-xs text-muted">Event Type</dt>
              <dd className="mt-0.5 font-medium text-ink font-mono text-xs">{event.event_type}</dd>
            </div>

            <div>
              <dt className="text-xs text-muted">Repository</dt>
              <dd className="mt-0.5 text-xs">
                {repoLink ? (
                  <a
                    href={repoLink}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-ink underline underline-offset-2"
                  >
                    {event.repository}
                  </a>
                ) : (
                  <span className="font-mono text-muted">—</span>
                )}
              </dd>
            </div>

            <div>
              <dt className="text-xs text-muted">Branch / Ref</dt>
              <dd className="mt-0.5 font-mono text-xs font-medium text-ink">
                {event.branch ? (
                  <span className="rounded bg-hover px-1.5 py-0.5 border border-line">
                    {event.branch}
                  </span>
                ) : (
                  "—"
                )}
              </dd>
            </div>

            <div>
              <dt className="text-xs text-muted">Commit</dt>
              <dd className="mt-0.5 text-xs">
                {event.commit_sha ? (
                  commitLink ? (
                    <a
                      href={commitLink}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-ink underline underline-offset-2 font-medium"
                    >
                      {event.commit_sha.slice(0, 7)}
                    </a>
                  ) : (
                    <span className="font-mono text-ink">{event.commit_sha.slice(0, 7)}</span>
                  )
                ) : (
                  "—"
                )}
                {event.actor && <span className="text-muted ml-1.5">by {event.actor}</span>}
              </dd>
            </div>

            <div>
              <dt className="text-xs text-muted">Environment / Version</dt>
              <dd className="mt-0.5 text-xs text-ink font-medium">
                {event.environment ? label(event.environment) : "—"}
                {event.version && (
                  <span className="ml-1.5 font-mono text-muted">({event.version})</span>
                )}
              </dd>
            </div>

            {event.workflow_name && (
              <div>
                <dt className="text-xs text-muted">Workflow</dt>
                <dd className="mt-0.5 text-xs font-mono text-ink">
                  {event.workflow_name}
                  {event.run_number ? ` #${event.run_number}` : ""}
                </dd>
              </div>
            )}

            {event.delivery_id && (
              <div>
                <dt className="text-xs text-muted">GitHub Delivery ID</dt>
                <dd className="mt-0.5 text-xs font-mono text-muted truncate" title={event.delivery_id}>
                  {event.delivery_id}
                </dd>
              </div>
            )}
          </dl>

          {/* Commit Message */}
          {event.commit_message && (
            <div>
              <span className="text-xs text-muted block mb-1">Commit Message</span>
              <div className="rounded-md border border-line bg-canvas p-3 font-mono text-xs text-ink whitespace-pre-wrap break-words">
                {event.commit_message}
              </div>
            </div>
          )}

          {/* Raw Metadata / Payload */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs text-muted font-medium">Webhook Metadata</span>
              <Button
                variant="quiet"
                className="text-xs py-0.5 px-2 h-auto"
                onClick={() => void handleCopyJson()}
              >
                {copiedJson ? "Copied!" : "Copy JSON"}
              </Button>
            </div>
            <Panel className="p-3 bg-canvas font-mono text-xs text-ink max-h-48 overflow-y-auto">
              <pre className="whitespace-pre-wrap break-all">{metadataJson}</pre>
            </Panel>
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-line px-6 py-3.5 bg-canvas/40 flex items-center justify-end">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
