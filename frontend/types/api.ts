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

export type LogLevel = "DEBUG" | "INFO" | "WARN" | "ERROR";

export type DeploymentStatus = "IN_PROGRESS" | "SUCCEEDED" | "FAILED" | "ROLLED_BACK";

// Timestamps are ISO 8601 strings in UTC (with a trailing "Z").

export interface Incident {
  id: number;
  reference: string; // e.g. "INC-001"
  title: string;
  description: string;
  severity: Severity;
  status: IncidentStatus;
  service_name: string;
  created_at: string;
  updated_at: string;
}

export interface ServiceSummary {
  name: string;
  display_name: string;
  description: string;
  dependencies: string[];
  /** "real": the monitored application; "demo": the simulated payment-api scenario. */
  kind: "real" | "demo";
  /** URL the backend health-checks (real services only). */
  url: string | null;
  status: ServiceStatus | null;
  last_health_at: string | null;
}

/** One monitored real application (no secrets). */
export interface Project {
  name: string;
  environment: string;
  service: string;
  url: string | null;
  repository: string | null;
}

/** GET /api/services/projects: what OpsPilot is watching. */
export interface Projects {
  projects: Project[];
  demo_mode: boolean;
  health_check_interval_seconds: number;
}

export interface WebhookInstructions {
  payload_url: string;
  content_type: string;
  secret_configured: boolean;
  events: string[];
  github_setup_url: string | null;
}

export interface ProjectCreateInput {
  name: string;
  service?: string;
  url?: string;
  repository: string;
  environment?: string;
}

export interface ProjectCreateResponse {
  project: Project;
  webhook: WebhookInstructions;
}

export interface ServiceHealth {
  service_name: string;
  timestamp: string;
  status: ServiceStatus;
  error_rate: number; // percent
  latency_ms: number;
  cpu_usage: number | null; // percent; null when not measured
  memory_usage: number | null; // percent; null when not measured
}

export interface LogEntry {
  timestamp: string;
  service_name: string;
  level: LogLevel;
  message: string;
  metadata: Record<string, unknown>;
}

export interface Deployment {
  service_name: string;
  version: string;
  status: DeploymentStatus;
  timestamp: string;
  commit_sha: string | null;
}

export interface DemoResetResponse {
  status: "reset";
  message: string;
  incidents_deleted: number;
  logs_deleted: number;
  deployments_deleted: number;
  health_records_deleted: number;
  /** Demo-repository CI/CD events only; a real repository's events are kept. */
  cicd_events_deleted: number;
}

/** Body of every error response (FastAPI's `{"detail": ...}`). */
export interface ApiErrorBody {
  detail: string;
}

// --- Agents (Phases 5-9) -----------------------------------------------------------------------

export type AgentName = "orchestrator" | "investigation" | "root_cause" | "remediation" | "verification";
export type AgentRunStatus = "RUNNING" | "COMPLETED" | "FAILED";
export type ActionType = "ROLLBACK_DEPLOYMENT" | "RESTART_SERVICE" | "NO_ACTION" | "ESCALATE_TO_HUMAN";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";
export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";
export type RecoveryStatus = "RECOVERED" | "NOT_RECOVERED" | "NOT_ATTEMPTED";
export type EvidenceSource =
  | "logs"
  | "health"
  | "deployments"
  | "previous_incidents"
  | "cicd"
  | "code_review"
  | "execution";

export interface AgentEvent {
  id: number;
  incident_id: number;
  agent: AgentName | null;
  event_type: string;
  message: string;
  metadata: Record<string, unknown>;
  timestamp: string;
}

export interface EvidenceItem {
  id: string; // citation id: L1, H1, D1, P1, V1 ...
  source: EvidenceSource;
  service: string;
  timestamp: string | null;
  fact: string;
  data: Record<string, unknown>;
}

export interface PreviousIncident {
  reference: string;
  title: string;
  severity: Severity;
  status: IncidentStatus;
  created_at: string;
  root_cause: string | null;
}

export interface InvestigationFinding {
  kind: "observation" | "hypothesis";
  statement: string;
  evidence_ids: string[];
}

export interface InvestigationResult {
  incident_id: number;
  incident_reference: string;
  service: string;
  status: "investigation_complete";
  summary: string;
  findings: InvestigationFinding[];
  evidence: EvidenceItem[];
  related_deployments: Deployment[];
  related_previous_incidents: PreviousIncident[];
  confidence: number;
  next_step: "root_cause_analysis" | "collect_more_evidence";
  model: string;
}

/** Common shape of every agent run endpoint (`result` differs per agent). */
export interface AgentRunView<TResult> {
  run_id: number;
  incident_id: number;
  incident_reference: string;
  incident_status: IncidentStatus;
  agent: AgentName;
  status: AgentRunStatus;
  started_at: string;
  completed_at: string | null;
  summary: string | null;
  result: TResult | null;
}

export type InvestigationRun = AgentRunView<InvestigationResult>;

export type RootCauseCategory =
  | "deployment_regression"
  | "configuration_error"
  | "database_failure"
  | "network_connectivity"
  | "resource_exhaustion"
  | "external_dependency"
  | "transient"
  | "unknown";

export interface CitedStatement {
  statement: string;
  evidence_ids: string[];
}

export interface AlternativeExplanation {
  explanation: string;
  assessment: "less_likely" | "ruled_out" | "not_assessable";
  reason: string;
  evidence_ids: string[];
}

export interface RootCauseResult {
  incident_id: number;
  incident_reference: string;
  service: string;
  status: "root_cause_identified";
  root_cause: string;
  category: RootCauseCategory;
  confidence: number;
  supporting_evidence: EvidenceItem[];
  causal_chain: CitedStatement[];
  contributing_factors: CitedStatement[];
  alternative_explanations: AlternativeExplanation[];
  missing_evidence: string[];
  reasoning_summary: string;
  recommended_next_step: "propose_remediation" | "collect_more_evidence" | "escalate_to_human";
  investigation_run_id: number;
  model: string;
}

export type RootCauseRun = AgentRunView<RootCauseResult>;

export interface Approval {
  id: number;
  incident_id: number;
  action_type: ActionType;
  target: string;
  risk: RiskLevel;
  reason: string;
  parameters: Record<string, unknown>;
  status: ApprovalStatus;
  requested_at: string;
  decided_at: string | null;
}

export interface RemediationResult {
  incident_id: number;
  incident_reference: string;
  service: string;
  status: "approval_required" | "no_action" | "escalated";
  action: ActionType;
  target: string;
  parameters: Record<string, unknown>;
  reason: string;
  risk: RiskLevel;
  requires_approval: boolean;
  approval_id: number | null;
  supporting_evidence: EvidenceItem[];
  confidence: number;
  executed: false;
  root_cause_run_id: number;
  model: string;
}

export interface RemediationRun extends AgentRunView<RemediationResult> {
  approval: Approval | null;
}

export interface ServiceSnapshot {
  active_version: string | null;
  status: ServiceStatus | null;
  error_rate: number | null;
  latency_ms: number | null;
}

export interface ExecutionResult {
  incident_id: number;
  approval_id: number;
  remediation_run_id: number | null;
  action: ActionType;
  target: string;
  parameters: Record<string, unknown>;
  status: "executed";
  /** False for a real application: an operator performed the approved action. */
  simulated: boolean;
  performed_by: "opspilot" | "operator";
  service: string;
  before: ServiceSnapshot;
  after: ServiceSnapshot;
  executed_at: string;
  next_step: "verification";
}

export interface ExecutionRun {
  run_id: number;
  status: AgentRunStatus;
  started_at: string;
  completed_at: string | null;
  summary: string | null;
  result: ExecutionResult | null;
  /** "operator": a real application; a human performs the approved action and confirms it. */
  mode: "simulated" | "operator";
  /** What the operator must do (operator mode, until confirmed). */
  instructions: string | null;
}

export interface DecisionResponse {
  incident_id: number;
  incident_reference: string;
  incident_status: IncidentStatus;
  approval: Approval;
  execution: ExecutionRun | null;
}

export type RecoveryCheckName =
  | "remediation_executed"
  | "target_deployment_active"
  | "service_healthy"
  | "error_rate_recovered"
  | "latency_recovered"
  | "telemetry_fresh";

export interface RecoveryCheck {
  name: RecoveryCheckName;
  passed: boolean;
  expected: string;
  actual: string;
  evidence_ids: string[];
}

export interface VerificationResult {
  incident_id: number;
  incident_reference: string;
  service: string;
  status: "verification_complete";
  recovered: boolean;
  confidence: number;
  before: ServiceSnapshot;
  after: ServiceSnapshot;
  checks: RecoveryCheck[];
  failed_checks: RecoveryCheckName[];
  supporting_evidence: EvidenceItem[];
  reasoning_summary: string;
  next_step: "incident_report" | "human_investigation";
  execution_run_id: number;
  approval_id: number;
  model: string;
}

export type VerificationRun = AgentRunView<VerificationResult>;

// --- Final report (Phase 10) -------------------------------------------------------------------

export interface TimelineEntry {
  timestamp: string;
  agent: AgentName | null;
  event_type: string;
  message: string;
}

export interface IncidentReportContent {
  summary: {
    incident_id: number;
    reference: string;
    title: string;
    description: string;
    service: string;
    severity: Severity;
    final_status: IncidentStatus;
    created_at: string;
    resolved_at: string | null;
  };
  timeline: TimelineEntry[];
  investigation: InvestigationResult;
  root_cause: RootCauseResult;
  remediation: RemediationResult;
  approval: Approval;
  execution: ExecutionResult;
  verification: VerificationResult;
  outcome: {
    recovered: boolean;
    final_status: IncidentStatus;
    human_decision: "APPROVED" | "REJECTED" | "PENDING" | "NONE";
    remediation_performed: string | null;
    verification: string;
    recovery_status: RecoveryStatus;
  };
}

export interface IncidentReport {
  report_id: number;
  incident_id: number;
  created_at: string;
  report: IncidentReportContent;
}

// --- Live stream: WS /ws/incidents/{id} --------------------------------------------------------

export interface StreamEventMessage extends AgentEvent {
  type: string; // equals event_type
}

export interface StreamStatusMessage {
  type: "incident_status";
  incident_id: number;
  incident_status: IncidentStatus;
}

export interface StreamErrorMessage {
  type: "error";
  message: string;
}

export type StreamMessage = StreamEventMessage | StreamStatusMessage | StreamErrorMessage;

// --- CI/CD telemetry (GitHub webhooks): app/schemas/cicd.py -------------------------------------

export type CicdCategory = "COMMIT" | "BUILD" | "TEST" | "DEPLOYMENT";
export type CicdStatus = "QUEUED" | "IN_PROGRESS" | "COMPLETED";
export type CicdConclusion = "SUCCESS" | "FAILURE" | "CANCELLED" | "TIMED_OUT" | "NEUTRAL" | "SKIPPED" | "OTHER";

/** One normalized CI/CD event (never the raw GitHub payload). */
export interface CicdEvent {
  id: number;
  provider: string;
  delivery_id: string;
  event_type: string;
  category: CicdCategory;
  repository: string;
  branch: string | null;
  commit_sha: string | null;
  commit_message: string | null;
  actor: string | null;
  workflow_name: string | null;
  workflow_run_id: number | null;
  run_number: number | null;
  status: CicdStatus;
  conclusion: CicdConclusion | null;
  service_name: string | null;
  environment: string | null;
  /** Null when no tag/metadata names a version (never invented). */
  version: string | null;
  version_source: string | null;
  html_url: string | null;
  occurred_at: string;
  started_at: string | null;
  completed_at: string | null;
  received_at: string;
  deployment_id: number | null;
  metadata: Record<string, unknown>;
}

export interface CicdEventQuery {
  service?: string;
  repository?: string;
  branch?: string;
  commit_sha?: string;
  workflow?: string;
  category?: CicdCategory;
  status?: CicdStatus;
  conclusion?: CicdConclusion;
  since?: string;
  until?: string;
  limit?: number;
}

/** Non-sensitive webhook configuration (never the secret). */
export interface WebhookStatus {
  configured: boolean;
  repository: string | null;
  service: string;
  supported_events: string[];
  max_payload_bytes: number;
}

// --- AI code review of pushes: app/schemas/code_review.py --------------------------------------

export type FindingSeverity = "critical" | "high" | "medium" | "low" | "info";
export type FindingCategory =
  | "bug"
  | "security"
  | "performance"
  | "reliability"
  | "configuration"
  | "maintainability"
  | "testing";
export type CodeReviewStatus = "PENDING" | "COMPLETED" | "FAILED" | "SKIPPED";

export interface ReviewFinding {
  severity: FindingSeverity;
  category: FindingCategory;
  file: string;
  line: number | null;
  title: string;
  explanation: string;
  recommendation: string;
  /** "static": the file failed the syntax check (parsed, never run); "ai": from the AI review. */
  source?: "ai" | "static";
}

export interface CodeReview {
  id: number;
  repository: string;
  service_name: string;
  cicd_event_id: number | null;
  commit_sha: string;
  base_sha: string | null;
  branch: string | null;
  commit_message: string | null;
  author: string | null;
  status: CodeReviewStatus;
  risk: RiskLevel | null;
  summary: string | null;
  findings: ReviewFinding[];
  files: { filename: string; status: string; additions: number; deletions: number }[];
  skipped_files: { filename: string; reason: string }[];
  truncated: boolean;
  model: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}
