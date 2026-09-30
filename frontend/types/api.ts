// Typed mirror of the backend's Pydantic schemas and enums (backend/app/schemas, app/models/enums.py).
// Keep in sync with the OpenAPI contract served at `${API_URL}/docs`.

export type IncidentStatus =
  | "DETECTED"
  | "INVESTIGATING"
  | "ANALYZING"
  | "AWAITING_APPROVAL"
  | "REMEDIATING"
  | "VERIFYING"
  | "RESOLVED"
  | "FAILED"
  | "ESCALATED";

export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export type ServiceStatus = "HEALTHY" | "DEGRADED" | "DOWN";

export interface DatabaseHealth {
  status: "ok" | "unavailable";
  latency_ms: number | null;
  error: string | null;
}

export interface AIProviderHealth {
  provider: string;
  model: string;
  configured_keys: number;
}

export interface SystemHealth {
  status: "ok" | "degraded";
  app: string;
  version: string;
  environment: string;
  database: DatabaseHealth;
  ai: AIProviderHealth;
}
