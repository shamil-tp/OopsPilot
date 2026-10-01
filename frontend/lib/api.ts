import { API_URL } from "@/lib/config";
import type {
  AgentEvent,
  CicdEvent,
  CodeReview,
  CicdEventQuery,
  DecisionResponse,
  DemoResetResponse,
  Deployment,
  ExecutionRun,
  Incident,
  ProjectCreateInput,
  ProjectCreateResponse,
  Projects,
  IncidentReport,
  InvestigationRun,
  RemediationRun,
  RootCauseRun,
  ServiceHealth,
  ServiceSummary,
  SystemHealth,
  VerificationRun,
  WebhookStatus,
} from "@/types/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<{ status: number; data: T }> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(`Cannot reach the OpsPilot API at ${API_URL}`, null);
  }

  const data: unknown = await response.json().catch(() => null);
  return { status: response.status, data: data as T };
}

/** The backend's safe `{"detail": ...}` message, or a generic one. */
function detailOf(data: unknown, status: number): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return `Request failed (HTTP ${status})`;
}

async function getJson<T>(path: string): Promise<T> {
  const { status, data } = await request<T>(path);
  if (status >= 200 && status < 300 && data !== null) return data;
  throw new ApiError(detailOf(data, status), status, data);
}

/** For resources that may not exist yet (e.g. a phase that has not run): 404/409 -> null. */
async function getOptional<T>(path: string): Promise<T | null> {
  const { status, data } = await request<T>(path);
  if (status === 404 || status === 409) return null;
  if (status >= 200 && status < 300 && data !== null) return data;
  throw new ApiError(detailOf(data, status), status, data);
}

/** POST without a body: every command acts on server-side state only. */
async function post<T>(path: string): Promise<T> {
  const { status, data } = await request<T>(path, { method: "POST" });
  if (status >= 200 && status < 300 && data !== null) return data;
  throw new ApiError(detailOf(data, status), status, data);
}

async function postJson<T, B = unknown>(path: string, body: B): Promise<T> {
  const { status, data } = await request<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (status >= 200 && status < 300 && data !== null) return data;
  throw new ApiError(detailOf(data, status), status, data);
}

async function del(path: string): Promise<void> {
  const { status, data } = await request<unknown>(path, { method: "DELETE" });
  if (status >= 200 && status < 300) return;
  throw new ApiError(detailOf(data, status), status, data);
}

export async function getSystemHealth(): Promise<SystemHealth> {
  const { status, data } = await request<SystemHealth>("/api/system/health");
  // 503 still carries a SystemHealth body describing which dependency is down.
  if ((status === 200 || status === 503) && data) {
    return data;
  }
  throw new ApiError(`System health check failed (HTTP ${status})`, status, data);
}

// --- Environment -------------------------------------------------------------------------------

export const listServices = () => getJson<ServiceSummary[]>("/api/services");
// Optional so a backend without project support (older deployment) still shows the demo.
export const getProjects = () => getOptional<Projects>("/api/services/projects");
export const createProject = (input: ProjectCreateInput) =>
  postJson<ProjectCreateResponse, ProjectCreateInput>("/api/services/projects", input);
export const deleteProject = (service: string) =>
  del(`/api/services/projects/${encodeURIComponent(service)}`);
export const getServiceHealth = (name: string) =>
  getOptional<ServiceHealth>(`/api/services/${encodeURIComponent(name)}/health`);
export const listDeployments = (name: string) =>
  getJson<Deployment[]>(`/api/services/${encodeURIComponent(name)}/deployments?limit=5`);
export const resetDemo = () => post<DemoResetResponse>("/api/demo/reset");

// --- Incidents ---------------------------------------------------------------------------------

export const listIncidents = () => getJson<Incident[]>("/api/incidents");
export const getIncident = (id: number) => getJson<Incident>(`/api/incidents/${id}`);
export const simulateIncident = () => post<Incident>("/api/incidents/simulate");
export const listEvents = (id: number) => getJson<AgentEvent[]>(`/api/incidents/${id}/events`);

// Phase results (null until that phase has run).
export const getInvestigation = (id: number) =>
  getOptional<InvestigationRun>(`/api/incidents/${id}/investigation`);
export const getAnalysis = (id: number) => getOptional<RootCauseRun>(`/api/incidents/${id}/analysis`);
export const getRemediation = (id: number) =>
  getOptional<RemediationRun>(`/api/incidents/${id}/remediation`);
export const getExecution = (id: number) => getOptional<ExecutionRun>(`/api/incidents/${id}/execution`);
export const getVerification = (id: number) =>
  getOptional<VerificationRun>(`/api/incidents/${id}/verification`);
export const getReport = (id: number) => getOptional<IncidentReport>(`/api/incidents/${id}/report`);

// Commands. None of them sends a body: the backend decides everything from its stored state.
export const investigate = (id: number) => post<InvestigationRun>(`/api/incidents/${id}/investigate`);
export const analyze = (id: number) => post<RootCauseRun>(`/api/incidents/${id}/analyze`);
export const remediate = (id: number) => post<RemediationRun>(`/api/incidents/${id}/remediate`);
export const approve = (id: number) => post<DecisionResponse>(`/api/incidents/${id}/approve`);
export const reject = (id: number) => post<DecisionResponse>(`/api/incidents/${id}/reject`);
export const verify = (id: number) => post<VerificationRun>(`/api/incidents/${id}/verify`);
/** Real applications: the operator has performed the approved action (no body). */
export const confirmExecution = (id: number) => post<ExecutionRun>(`/api/incidents/${id}/execution/confirm`);

// --- CI/CD telemetry (read-only; GitHub delivers events to the backend, never to the browser) ---

export function listCicdEvents(query: CicdEventQuery = {}): Promise<CicdEvent[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const qs = params.toString();
  return getJson<CicdEvent[]>(`/api/cicd/events${qs ? `?${qs}` : ""}`);
}
export const getCicdEvent = (id: number) => getJson<CicdEvent>(`/api/cicd/events/${id}`);
export const getWebhookStatus = () => getJson<WebhookStatus>("/api/webhooks/github/status");

// --- AI code reviews of pushes ------------------------------------------------------------------

export function listCodeReviews(query: { service?: string; limit?: number } = {}): Promise<CodeReview[]> {
  const params = new URLSearchParams();
  if (query.service) params.set("service", query.service);
  if (query.limit) params.set("limit", String(query.limit));
  const qs = params.toString();
  return getJson<CodeReview[]>(`/api/code-reviews${qs ? `?${qs}` : ""}`);
}
export const getCodeReview = (id: number) => getJson<CodeReview>(`/api/code-reviews/${id}`);
export const retryCodeReview = (id: number) => post<CodeReview>(`/api/code-reviews/${id}/retry`);
export const getHealthHistory = (name: string, limit = 20) =>
  getJson<ServiceHealth[]>(`/api/services/${encodeURIComponent(name)}/health/history?limit=${limit}`);
