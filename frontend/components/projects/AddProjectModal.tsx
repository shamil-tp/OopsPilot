"use client";

import { useState } from "react";
import { Button, ErrorNote, Panel } from "@/components/ui";
import { ApiError, createProject } from "@/lib/api";
import type { ProjectCreateInput, ProjectCreateResponse } from "@/types/api";

interface AddProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProjectAdded?: () => void;
}

export function AddProjectModal({ isOpen, onClose, onProjectAdded }: AddProjectModalProps) {
  const [name, setName] = useState("");
  const [service, setService] = useState("");
  const [url, setUrl] = useState("");
  const [repository, setRepository] = useState("");
  const [environment, setEnvironment] = useState("production");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProjectCreateResponse | null>(null);
  const [copiedUrl, setCopiedUrl] = useState(false);

  if (!isOpen) return null;

  const handleNameChange = (val: string) => {
    setName(val);
    if (!service || service === deriveSlug(name)) {
      setService(deriveSlug(val));
    }
  };

  const deriveSlug = (text: string) =>
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      const payload: ProjectCreateInput = {
        name: name.trim(),
        service: service.trim() || undefined,
        url: url.trim() || undefined,
        repository: repository.trim(),
        environment: environment.trim() || "production",
      };
      const resp = await createProject(payload);
      setResult(resp);
      onProjectAdded?.();
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Failed to register project");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCopy = async (text: string) => {
    await navigator.clipboard.writeText(text);
    setCopiedUrl(true);
    setTimeout(() => setCopiedUrl(false), 2000);
  };

  const resetAndClose = () => {
    setName("");
    setService("");
    setUrl("");
    setRepository("");
    setEnvironment("production");
    setError(null);
    setResult(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-xs">
      <div
        className="w-full max-w-xl rounded-xl border border-line bg-panel shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150"
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="text-base font-semibold text-ink">
            {result ? "Project Added · Webhook Setup" : "Add Monitored Project"}
          </h2>
          <button
            type="button"
            onClick={resetAndClose}
            className="text-muted hover:text-ink text-sm px-1.5 py-0.5 rounded"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="p-6">
          {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}

          {!result ? (
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <p className="text-xs text-muted">
                Connect a GitHub repository for autonomous health checks, CI/CD telemetry, and AI code reviews on every push.
              </p>

              <div>
                <label htmlFor="project-name" className="block text-xs font-medium text-ink mb-1">
                  Project Name <span className="text-red-500">*</span>
                </label>
                <input
                  id="project-name"
                  type="text"
                  required
                  placeholder="e.g. Mallu Typing or Payment Service"
                  value={name}
                  onChange={(e) => handleNameChange(e.target.value)}
                  className="w-full rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-sm text-ink placeholder:text-subtle focus:outline-blue-600"
                />
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor="service-slug" className="block text-xs font-medium text-ink mb-1">
                    Service Identifier (Slug)
                  </label>
                  <input
                    id="service-slug"
                    type="text"
                    placeholder="e.g. mallutyping-web"
                    value={service}
                    onChange={(e) => setService(e.target.value)}
                    className="w-full font-mono rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-xs text-ink placeholder:text-subtle focus:outline-blue-600"
                  />
                  <p className="mt-1 text-[11px] text-muted">Unique internal ID for telemetry & logs.</p>
                </div>

                <div>
                  <label htmlFor="project-env" className="block text-xs font-medium text-ink mb-1">
                    Environment
                  </label>
                  <input
                    id="project-env"
                    type="text"
                    value={environment}
                    onChange={(e) => setEnvironment(e.target.value)}
                    className="w-full rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-sm text-ink focus:outline-blue-600"
                  />
                  <p className="mt-1 text-[11px] text-muted">e.g. production, staging</p>
                </div>
              </div>

              <div>
                <label htmlFor="github-repo" className="block text-xs font-medium text-ink mb-1">
                  GitHub Repository <span className="text-red-500">*</span>
                </label>
                <input
                  id="github-repo"
                  type="text"
                  required
                  placeholder="owner/repo (e.g. MrNihalT/mallutyping or GitHub URL)"
                  value={repository}
                  onChange={(e) => setRepository(e.target.value)}
                  className="w-full font-mono rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-xs text-ink placeholder:text-subtle focus:outline-blue-600"
                />
              </div>

              <div>
                <label htmlFor="app-url" className="block text-xs font-medium text-ink mb-1">
                  Application URL (Optional)
                </label>
                <input
                  id="app-url"
                  type="url"
                  placeholder="https://yourapp.domain.com"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  className="w-full rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-sm text-ink placeholder:text-subtle focus:outline-blue-600"
                />
                <p className="mt-1 text-[11px] text-muted">
                  OpsPilot sends non-intrusive HTTP GET health checks every 60s to monitor uptime & latency.
                </p>
              </div>

              <div className="mt-2 flex items-center justify-end gap-2 border-t border-line pt-4">
                <Button variant="quiet" onClick={resetAndClose} type="button">
                  Cancel
                </Button>
                <Button variant="primary" disabled={submitting || !name || !repository} type="submit">
                  {submitting ? "Adding…" : "Add Project"}
                </Button>
              </div>
            </form>
          ) : (
            <div className="flex flex-col gap-5">
              <div className="rounded-lg bg-emerald-50 border border-emerald-200 p-3 text-xs text-emerald-900">
                <span className="font-semibold">{result.project.name}</span> has been added to monitored projects!
              </div>

              <div>
                <h3 className="text-sm font-semibold text-ink mb-1">Next: Add GitHub Webhook</h3>
                <p className="text-xs text-muted">
                  To receive commits, CI/CD telemetry, and automatic AI code reviews, add a webhook in your repository settings:
                </p>
              </div>

              <Panel className="p-4 flex flex-col gap-3 bg-canvas text-xs font-mono">
                <div>
                  <span className="text-[11px] text-muted uppercase font-sans font-medium block mb-1">
                    1. Payload URL
                  </span>
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      value={result.webhook.payload_url}
                      className="w-full rounded border border-line bg-panel px-2.5 py-1 text-xs text-ink select-all"
                    />
                    <Button
                      variant="secondary"
                      className="px-2.5 py-1 text-xs shrink-0"
                      onClick={() => void handleCopy(result.webhook.payload_url)}
                    >
                      {copiedUrl ? "Copied!" : "Copy"}
                    </Button>
                  </div>
                </div>

                <div>
                  <span className="text-[11px] text-muted uppercase font-sans font-medium block mb-1">
                    2. Content type
                  </span>
                  <div className="font-medium text-ink">{result.webhook.content_type}</div>
                </div>

                <div>
                  <span className="text-[11px] text-muted uppercase font-sans font-medium block mb-1">
                    3. Secret
                  </span>
                  <div className="text-muted font-sans text-xs">
                    {result.webhook.secret_configured
                      ? "Enter the GITHUB_WEBHOOK_SECRET configured on your OpsPilot backend."
                      : "Optional (GITHUB_WEBHOOK_SECRET is currently empty on server)."}
                  </div>
                </div>

                <div>
                  <span className="text-[11px] text-muted uppercase font-sans font-medium block mb-1">
                    4. Events to trigger
                  </span>
                  <div className="flex flex-wrap gap-1 font-sans">
                    <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink border border-line">Pushes</span>
                    <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink border border-line">Deployment statuses</span>
                    <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-muted border border-line">Workflow runs</span>
                  </div>
                </div>
              </Panel>

              <div className="flex items-center justify-between border-t border-line pt-4">
                {result.webhook.github_setup_url && (
                  <a
                    href={result.webhook.github_setup_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs font-semibold text-blue-600 hover:underline"
                  >
                    Open GitHub Webhook Settings ↗
                  </a>
                )}
                <Button variant="primary" onClick={resetAndClose}>
                  Done
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
