# OpsPilot REST API

The authoritative, always-current contract is the OpenAPI schema FastAPI generates:
**http://localhost:8000/docs** (Swagger UI) and `/openapi.json`. This page summarizes it.
Typed mirrors for the frontend live in `frontend/types/api.ts`.

Conventions:

- All paths are under `/api`. Request and response bodies are JSON.
- Timestamps are ISO 8601 in UTC with a `Z` suffix (e.g. `2026-09-30T11:44:00Z`).
- Errors use FastAPI's shape: `{"detail": "..."}`. Validation errors (bad ids, bad query
  parameters) return `422` with FastAPI's standard validation body.
- If the database is unreachable, any endpoint returns `503 {"detail": "Database unavailable. Please try again shortly."}`.
  Connection details and stack traces are never included.

## System

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/system/health` | App, database, and AI provider status. `503` if the database is unavailable |

## Incidents

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/incidents/simulate` | Run the payment-api incident scenario. `201` with the new incident, or `200` with the existing one if a simulated incident is still active |
| `GET` | `/api/incidents` | Incidents, newest first. Query: `limit` (1–200, default 50), `offset` |
| `GET` | `/api/incidents/{id}` | One incident. `404` if it doesn't exist, `422` if `id` isn't an integer |
| `POST` | `/api/incidents/{id}/investigate` | Run the Investigation Agent (see below) |
| `GET` | `/api/incidents/{id}/events` | The incident's agent timeline, oldest first. `404` if the incident doesn't exist |
| `POST` | `/api/incidents/{id}/analyze` | Run the Root Cause Analysis Agent (see below) |
| `GET` | `/api/incidents/{id}/analysis` | Latest RCA run (RUNNING, FAILED or COMPLETED with result). `404` if none |
| `POST` | `/api/incidents/{id}/remediate` | Run the Remediation Agent: propose an action, request approval (see below) |
| `GET` | `/api/incidents/{id}/remediation` | Latest remediation proposal and its approval. `404` if none |
| `POST` | `/api/incidents/{id}/approve` | Approve the pending proposal and execute it (simulated). No body |
| `POST` | `/api/incidents/{id}/reject` | Reject the pending proposal; incident `ESCALATED`. No body |
| `GET` | `/api/incidents/{id}/execution` | The approved remediation's execution. `404` if none |
| `POST` | `/api/incidents/{id}/verify` | Run the Verification Agent (see below). No body |
| `GET` | `/api/incidents/{id}/verification` | Latest verification. `404` if none |

Incident:

```json
{
  "id": 1,
  "reference": "INC-001",
  "title": "Payment API Production Incident",
  "description": "Payment API is returning HTTP 500 errors with elevated error rate and latency.",
  "severity": "HIGH",
  "status": "DETECTED",
  "service_name": "payment-api",
  "created_at": "2026-09-30T11:44:00Z",
  "updated_at": "2026-09-30T11:44:00Z"
}
```

`severity`: `LOW | MEDIUM | HIGH | CRITICAL`, set by a deterministic rule over the service's
current health (≥ 20% errors → `HIGH`). `status`: `DETECTED | INVESTIGATING | ANALYZING |
AWAITING_APPROVAL | REMEDIATING | VERIFYING | RESOLVED | FAILED | ESCALATED`.

### Investigation

`POST /api/incidents/{id}/investigate` moves a `DETECTED` incident to `INVESTIGATING`, collects
bounded evidence with read-only tools, makes **one** structured AI call, and returns the stored
run. The incident stays `INVESTIGATING` (no root cause is concluded, nothing is remediated).
Design: [`agent-design.md`](agent-design.md).

| Status | Meaning |
| --- | --- |
| `201` | Investigation ran; `status: "COMPLETED"` with `result` |
| `200` | Already investigated: the existing run is returned, no AI call |
| `404` | Incident not found |
| `409` | An investigation is running, or the incident is not `DETECTED` |
| `502` | AI analysis failed (rate limit, timeout, invalid output); incident back to `DETECTED`, retry allowed |
| `503` | AI not configured, or database/telemetry unavailable; incident back to `DETECTED` |

```json
{
  "run_id": 2,
  "incident_id": 1,
  "incident_reference": "INC-001",
  "incident_status": "INVESTIGATING",
  "agent": "investigation",
  "status": "COMPLETED",
  "started_at": "2026-09-30T11:44:02Z",
  "completed_at": "2026-09-30T11:44:09Z",
  "summary": "The payment-api service began experiencing elevated HTTP 500 errors ...",
  "result": {
    "incident_id": 1,
    "incident_reference": "INC-001",
    "service": "payment-api",
    "status": "investigation_complete",
    "summary": "...",
    "findings": [
      {"kind": "observation", "statement": "The service is experiencing database connection failures and pool exhaustion.", "evidence_ids": ["L5", "L6", "L7"]},
      {"kind": "hypothesis", "statement": "The database configuration changes in v1.8.2 are causing the connectivity issues.", "evidence_ids": ["L3", "L5", "L7"]}
    ],
    "evidence": [
      {"id": "H1", "source": "health", "service": "payment-api", "timestamp": "2026-09-30T11:44:00Z",
       "fact": "payment-api DEGRADED at 11:44:00 (T+0s, current): error_rate 37%, latency 2800 ms, ...", "data": {}}
    ],
    "related_deployments": [{"service_name": "payment-api", "version": "v1.8.2", "status": "SUCCEEDED", "timestamp": "...", "commit_sha": "e4a7c52"}],
    "related_previous_incidents": [],
    "confidence": 0.9,
    "next_step": "root_cause_analysis",
    "model": "gemini:gemini-3.1-flash-lite"
  }
}
```

Agent event (`GET /api/incidents/{id}/events`):

```json
{"id": 12, "incident_id": 1, "agent": "investigation", "event_type": "tool_completed",
 "message": "get_application_logs returned 27 record(s)",
 "metadata": {"tool": "get_application_logs", "records": 27}, "timestamp": "2026-09-30T11:44:04.123Z"}
```

### Root cause analysis

`POST /api/incidents/{id}/analyze` requires a completed investigation. It moves the incident from
`INVESTIGATING` to `ANALYZING`, correlates the **stored** investigation evidence with one
structured AI call, validates every citation, and returns the stored run. The incident stays
`ANALYZING`; nothing is remediated.

| Status | Meaning |
| --- | --- |
| `201` | Analysis ran; `status: "COMPLETED"` with `result` |
| `200` | Already analyzed: the stored run is returned, no AI call |
| `404` | Incident not found |
| `409` | No completed investigation, an analysis is running, or the incident is not `INVESTIGATING` |
| `502` | AI failure or a root cause without valid evidence; incident back to `INVESTIGATING`, retry allowed |
| `503` | AI not configured or database unavailable; incident back to `INVESTIGATING` |

```json
{
  "run_id": 4, "incident_id": 1, "incident_reference": "INC-001", "incident_status": "ANALYZING",
  "agent": "root_cause", "status": "COMPLETED", "summary": "The deployment of v1.8.2 introduced ...",
  "result": {
    "status": "root_cause_identified",
    "root_cause": "The deployment of v1.8.2 introduced incorrect database configuration settings, specifically the host and pool size, leading to connection failures and pool exhaustion.",
    "category": "configuration_error",
    "confidence": 0.9,
    "supporting_evidence": [{"id": "L3", "source": "logs", "fact": "... Application configuration reloaded [changed_keys=['database.host', 'database.pool_size']]", "...": "..."}],
    "causal_chain": [{"statement": "Deployment v1.8.2 initiated and completed, applying new database configuration keys.", "evidence_ids": ["L2", "L3", "L4"]}],
    "contributing_factors": [{"statement": "...", "evidence_ids": ["L3", "L5"]}],
    "alternative_explanations": [{"explanation": "Database service failure.", "assessment": "ruled_out", "reason": "The database dependency is reported as healthy ...", "evidence_ids": ["H3"]}],
    "missing_evidence": ["The specific values applied to 'database.host' and 'database.pool_size' ..."],
    "reasoning_summary": "The incident began immediately following the deployment of v1.8.2 ...",
    "recommended_next_step": "propose_remediation",
    "investigation_run_id": 3,
    "model": "gemini:gemini-3.1-flash-lite"
  }
}
```

### Remediation proposal

`POST /api/incidents/{id}/remediate` requires a completed RCA and an `ANALYZING` incident. The
agent proposes one of `ROLLBACK_DEPLOYMENT`, `RESTART_SERVICE`, `NO_ACTION`, `ESCALATE_TO_HUMAN`;
the backend validates it (target, rollback version against the deployments table, citations) and
sets risk and the approval requirement itself. Risky actions create a `PENDING` approval and move
the incident to `AWAITING_APPROVAL`. **Nothing is executed.** The request takes no body.

| Status | Meaning |
| --- | --- |
| `201` | Proposal made; `approval` is set for risky actions |
| `200` | A proposal already exists: it is returned, no AI call, no second approval |
| `404` | Incident not found |
| `409` | No completed RCA, a proposal is running, or the incident is not `ANALYZING` |
| `502` | AI failure, or **proposal rejected by backend policy** (e.g. unknown rollback version); no approval created, incident stays `ANALYZING` |
| `503` | AI not configured or database unavailable |

```json
{
  "run_id": 8, "incident_id": 1, "incident_reference": "INC-001",
  "incident_status": "AWAITING_APPROVAL", "agent": "remediation", "status": "COMPLETED",
  "summary": "roll back payment-api from v1.8.2 to v1.8.1",
  "result": {
    "status": "approval_required",
    "action": "ROLLBACK_DEPLOYMENT",
    "target": "v1.8.2",
    "parameters": {"service": "payment-api", "from_version": "v1.8.2", "to_version": "v1.8.1"},
    "reason": "Deployment v1.8.2 introduced incorrect database configuration parameters ...",
    "risk": "MEDIUM",
    "requires_approval": true,
    "approval_id": 3,
    "supporting_evidence": [{"id": "L3", "source": "logs", "fact": "... Application configuration reloaded ...", "...": "..."}],
    "confidence": 0.9,
    "executed": false,
    "root_cause_run_id": 7,
    "model": "gemini:gemini-3.1-flash-lite"
  },
  "approval": {
    "id": 3, "incident_id": 1, "action_type": "ROLLBACK_DEPLOYMENT", "target": "v1.8.2",
    "risk": "MEDIUM", "reason": "...", "status": "PENDING", "decided_at": null,
    "parameters": {"service": "payment-api", "from_version": "v1.8.2", "to_version": "v1.8.1", "remediation_run_id": 8},
    "requested_at": "2026-09-30T11:45:30Z"
  }
}
```

Event types so far: `incident_created`, `agent_started`, `tool_started`, `tool_completed`,
`evidence_found`, `investigation_analysis_started`, `investigation_completed`,
`evidence_evaluated`, `root_cause_analysis_started`, `root_cause_identified`,
`remediation_analysis_started`, `remediation_recommended`, `approval_required`,
`approval_received`, `remediation_started`, `remediation_completed`, `incident_escalated`,
`verification_started`, `recovery_check`, `verification_completed`, `incident_resolved`, `error`.

### Approve / reject

Both take **no request body**: they decide on the incident's stored `PENDING` approval, so the
client can never choose the action, target, version or parameters. Approve executes the stored,
re-validated parameters immediately (project spec §18); no AI is called.

| Status | Approve | Reject |
| --- | --- | --- |
| `200` | Approved and executed; `incident_status: "VERIFYING"`. Repeat: current state, no second execution | Rejected; `incident_status: "ESCALATED"`, nothing executed. Repeat: current state |
| `404` | Incident or approval request not found | same |
| `409` | Already rejected; decided concurrently; incident not `AWAITING_APPROVAL`; or **execution refused** (stale/invalid stored parameters: incident `FAILED`, nothing changed) | Already approved; decided concurrently; wrong state |
| `503` / `500` | Database or unexpected failure during execution: nothing changed, incident `FAILED` | |

```json
{
  "incident_id": 1, "incident_reference": "INC-001", "incident_status": "VERIFYING",
  "approval": {"id": 4, "action_type": "ROLLBACK_DEPLOYMENT", "target": "v1.8.2", "risk": "MEDIUM",
               "status": "APPROVED", "decided_at": "2026-09-30T11:48:32Z",
               "parameters": {"service": "payment-api", "from_version": "v1.8.2", "to_version": "v1.8.1", "remediation_run_id": 11}, "...": "..."},
  "execution": {
    "run_id": 12, "status": "COMPLETED", "summary": "rolled back payment-api from v1.8.2 to v1.8.1",
    "result": {
      "action": "ROLLBACK_DEPLOYMENT", "target": "v1.8.2", "simulated": true, "status": "executed",
      "parameters": {"service": "payment-api", "from_version": "v1.8.2", "to_version": "v1.8.1"},
      "before": {"active_version": "v1.8.2", "status": "DEGRADED", "error_rate": 37.0, "latency_ms": 2800.0},
      "after":  {"active_version": "v1.8.1", "status": "HEALTHY", "error_rate": 0.8, "latency_ms": 180.0},
      "next_step": "verification", "...": "..."
    }
  }
}
```

The incident stays `VERIFYING` until `POST /verify`.

### Verification

`POST /api/incidents/{id}/verify` takes **no body**: every fact comes from backend state. The
backend runs six recovery checks and decides `recovered`; one AI call only writes the summary.

| Status | Meaning |
| --- | --- |
| `201` | Verified. `recovered: true` → incident `RESOLVED`; `recovered: false` → incident `FAILED` (with `failed_checks`) |
| `200` | Already verified: stored result, no AI call |
| `404` | Incident not found |
| `409` | No completed approved execution, verification running, or incident not `VERIFYING` |
| `502` | AI explanation failed or cited no supplied evidence; incident stays `VERIFYING` (retry) |
| `503` | AI not configured or database unavailable; incident stays `VERIFYING` |

```json
{
  "run_id": 17, "incident_id": 1, "incident_reference": "INC-001", "incident_status": "RESOLVED",
  "agent": "verification", "status": "COMPLETED", "summary": "payment-api recovered: 6/6 checks passed",
  "result": {
    "recovered": true, "confidence": 1.0,
    "before": {"active_version": "v1.8.2", "status": "DEGRADED", "error_rate": 37.0, "latency_ms": 2800.0},
    "after":  {"active_version": "v1.8.1", "status": "HEALTHY", "error_rate": 0.8, "latency_ms": 180.0},
    "checks": [
      {"name": "error_rate_recovered", "passed": true, "expected": "< 5% (was 37%)", "actual": "0.8%", "evidence_ids": ["V2", "V3"]},
      "... remediation_executed, target_deployment_active, service_healthy, latency_recovered, telemetry_fresh"
    ],
    "failed_checks": [],
    "supporting_evidence": [{"id": "V3", "source": "health", "fact": "payment-api now: HEALTHY ...", "...": "..."}],
    "reasoning_summary": "The payment-api service recovered following the rollback to v1.8.1 ...",
    "next_step": "incident_report",
    "execution_run_id": 16, "approval_id": 4, "model": "gemini:gemini-3.1-flash-lite"
  }
}
```

## Services

The simulated services are `payment-api`, `auth-api` and `database`. Any other name returns `404`
from the per-service endpoints.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/services` | The services, their dependencies, and the status from each one's latest health snapshot (`null` if none) |
| `GET` | `/api/services/{name}/health` | Latest persisted health snapshot. `404` if the service is unknown or has no health recorded yet |
| `GET` | `/api/services/{name}/logs` | Logs in chronological order (oldest first): the newest `limit` entries that match the filters |
| `GET` | `/api/services/{name}/deployments` | Deployment history, newest first. Query: `limit` (1–100, default 20) |

Log query parameters:

| Parameter | Default | Description |
| --- | --- | --- |
| `limit` | 100 | 1–500 |
| `level` | all | `DEBUG`, `INFO`, `WARN`, `ERROR`. Repeatable: `?level=ERROR&level=WARN` |
| `since`, `until` | none | ISO 8601 timestamps (inclusive), for time-window filtering |

Health:

```json
{
  "service_name": "payment-api",
  "timestamp": "2026-09-30T11:44:00Z",
  "status": "DEGRADED",
  "error_rate": 37.0,
  "latency_ms": 2800.0,
  "cpu_usage": 43.0,
  "memory_usage": 68.0
}
```

Log entry:

```json
{
  "timestamp": "2026-09-30T11:42:00Z",
  "service_name": "payment-api",
  "level": "ERROR",
  "message": "Database connection failed",
  "metadata": {"error": "ConnectionRefusedError", "host": "payments-db.internal", "port": 5433, "attempt": 1}
}
```

Deployment:

```json
{
  "service_name": "payment-api",
  "version": "v1.8.2",
  "status": "SUCCEEDED",
  "timestamp": "2026-09-30T11:41:00Z",
  "commit_sha": "e4a7c52"
}
```

## Demo

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/demo/reset` | Delete all incidents (their agent runs, events, approvals and reports cascade) and the simulated services' telemetry, then seed a healthy environment on payment-api v1.8.1. Incident numbering restarts at INC-001 |

```json
{
  "status": "reset",
  "message": "Demo reset: all services healthy, no incidents.",
  "incidents_deleted": 1,
  "logs_deleted": 36,
  "deployments_deleted": 5,
  "health_records_deleted": 14,
  "cicd_events_deleted": 3
}
```

Reset never drops or truncates tables, and it only deletes telemetry rows whose `service_name` is
one of the simulated services, plus CI/CD events of the demo repository
(`opspilot-demo/payment-api`). Events from a real configured repository are kept.

## Report and live events

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/incidents/{id}/report` | Final report assembled from the stored phase results (no AI call); created once after verification. `404` unknown incident, `409` not verified yet |
| `WS` | `/ws/incidents/{id}?after=<event id>` | Full event history, then new events as they are committed, plus `incident_status` messages |

## GitHub webhook

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/webhooks/github` | GitHub delivery. Headers `X-GitHub-Event`, `X-GitHub-Delivery`, `X-Hub-Signature-256` (HMAC SHA-256 of the raw body with `GITHUB_WEBHOOK_SECRET`) |
| `GET` | `/api/webhooks/github/status` | `configured`, `repository`, `service`, `supported_events`, `max_payload_bytes` (never the secret) |

| Status | Meaning |
| --- | --- |
| `201` | Recorded (`status: recorded`, normalized `event`) |
| `200` | Duplicate delivery (`status: duplicate`, the stored event, nothing written) or `ping` (`pong`) |
| `202` | Ignored: unsupported event, or not the configured `GITHUB_REPOSITORY` |
| `400` | Missing/invalid `X-GitHub-Event` / `X-GitHub-Delivery`, or body is not a JSON object |
| `401` | Missing, malformed or invalid signature (message never includes the secret or a digest) |
| `413` | Body larger than 2 MiB |
| `422` | Payload does not match the declared event |
| `503` | `GITHUB_WEBHOOK_SECRET` is not configured |

```json
{
  "status": "recorded",
  "delivery_id": "3f0c…",
  "event_type": "workflow_run",
  "reason": null,
  "event": {
    "id": 3, "category": "DEPLOYMENT", "repository": "opspilot-demo/payment-api",
    "workflow_name": "deploy-production", "run_number": 57, "branch": "main",
    "commit_sha": "e4a7c52d9b1f3a6c8e0f2b4d6a8c1e3f5b7d9a0c", "status": "COMPLETED",
    "conclusion": "SUCCESS", "service_name": "payment-api", "environment": "production",
    "version": "v1.8.2", "version_source": "tag", "deployment_id": 12,
    "occurred_at": "2026-09-30T11:41:45Z", "…": "…"
  }
}
```

## CI/CD events

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/cicd/events` | Newest first. Filters: `repository`, `branch`, `commit_sha` (hex prefix, 4–40), `workflow`, `service`, `category`, `status`, `conclusion`, `since`, `until`; `limit` 1–50 (default 20). Invalid filters → `422` |
| `GET` | `/api/cicd/events/{id}` | One event (`404` if unknown) |

Setup and normalization rules: [github-webhooks.md](github-webhooks.md).

## Code reviews

Every `push` to the default branch of a repository listed in `MONITORED_PROJECTS` is reviewed once
(unique per repository + commit) in the background: the diff is fetched from `api.github.com`,
lockfiles and secret files are skipped, credentials are replaced with `[REDACTED]`, and one
structured Gemini call returns a summary, a deployment risk and findings. Findings on files outside
the diff are dropped; findings are sorted most severe first. A review never changes the repository.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/code-reviews` | Newest first. Filters: `service`; `limit` 1–50 (default 20) |
| `GET` | `/api/code-reviews/{id}` | One review (`404` if unknown) |
| `POST` | `/api/code-reviews/{id}/retry` | Re-run a `FAILED` review; `409` for any other status |

```json
{
  "id": 1, "repository": "owner/repo", "service_name": "mallutyping-web",
  "commit_sha": "acb9cf9…", "branch": "main", "status": "COMPLETED", "risk": "HIGH",
  "summary": "…",
  "findings": [{
    "severity": "critical", "category": "bug", "file": "src/components/typing/PracticeArea.tsx",
    "line": 433, "title": "Invalid React hook call", "explanation": "…", "recommendation": "…"
  }],
  "files": [{"filename": "…", "additions": 1, "deletions": 1}], "skipped_files": [], "truncated": false
}
```

Status: `PENDING` → `COMPLETED` or `FAILED` (`error` holds a safe reason, e.g. a private repository
without `GITHUB_TOKEN`); `SKIPPED` when nothing reviewable changed. During an incident on the same
service, recent reviews are evidence (`R1`, `R2`, …) through the read-only `get_recent_code_reviews`
tool.
