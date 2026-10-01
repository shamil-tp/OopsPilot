"use client";

import { useState } from "react";
import { Button, Panel } from "@/components/ui";
import { API_URL } from "@/lib/config";

interface WebhookModalProps {
  isOpen: boolean;
  onClose: () => void;
  repository: string | null;
  serviceName: string;
}

export function WebhookModal({ isOpen, onClose, repository, serviceName }: WebhookModalProps) {
  const [copiedUrl, setCopiedUrl] = useState(false);

  if (!isOpen) return null;

  const payloadUrl = `${API_URL}/api/webhooks/github`;
  const githubSetupUrl = repository ? `https://github.com/${repository}/settings/hooks/new` : null;

  const handleCopy = async (text: string) => {
    await navigator.clipboard.writeText(text);
    setCopiedUrl(true);
    setTimeout(() => setCopiedUrl(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-xs">
      <div
        className="w-full max-w-lg rounded-xl border border-line bg-panel shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150"
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="text-base font-semibold text-ink">
            GitHub Webhook Setup · {serviceName}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-muted hover:text-ink text-sm px-1.5 py-0.5 rounded"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="p-6 flex flex-col gap-4">
          <p className="text-xs text-muted">
            Add this webhook in your GitHub repository settings to enable automated AI code reviews on every push, build verification, and deployment tracking:
          </p>

          <Panel className="p-4 flex flex-col gap-3 bg-canvas text-xs font-mono">
            <div>
              <span className="text-[11px] text-muted uppercase font-sans font-medium block mb-1">
                1. Payload URL
              </span>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={payloadUrl}
                  className="w-full rounded border border-line bg-panel px-2.5 py-1 text-xs text-ink select-all"
                />
                <Button
                  variant="secondary"
                  className="px-2.5 py-1 text-xs shrink-0"
                  onClick={() => void handleCopy(payloadUrl)}
                >
                  {copiedUrl ? "Copied!" : "Copy"}
                </Button>
              </div>
            </div>

            <div>
              <span className="text-[11px] text-muted uppercase font-sans font-medium block mb-1">
                2. Content type
              </span>
              <div className="font-medium text-ink">application/json</div>
            </div>

            <div>
              <span className="text-[11px] text-muted uppercase font-sans font-medium block mb-1">
                3. Secret
              </span>
              <div className="text-muted font-sans text-xs">
                Enter the <code className="font-mono text-ink">GITHUB_WEBHOOK_SECRET</code> configured on your OpsPilot backend.
              </div>
            </div>

            <div>
              <span className="text-[11px] text-muted uppercase font-sans font-medium block mb-1">
                4. Select events
              </span>
              <div className="flex flex-wrap gap-1 font-sans">
                <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink border border-line">Pushes</span>
                <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink border border-line">Deployment statuses</span>
                <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-muted border border-line">Workflow runs</span>
              </div>
            </div>
          </Panel>

          <div className="flex items-center justify-between border-t border-line pt-4">
            {githubSetupUrl ? (
              <a
                href={githubSetupUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-blue-600 hover:underline"
              >
                Open GitHub Webhook Settings ↗
              </a>
            ) : <span />}
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
