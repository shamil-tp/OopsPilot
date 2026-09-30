# OpsPilot Architecture

## Final architecture

```text
GitHub (push, workflow_run, deployment_status)
   │  signed webhook (HMAC SHA-256 over the raw body)
   ▼
FastAPI  POST /api/webhooks/github ── validate ── normalize ── cicd_events ── deployments
   │                                                          (CI/CD & deployment telemetry)
   ▼
Incident detected (simulated payment-api regression; deterministic telemetry)
   │  POST /api/incidents/{id}/investigate
   ▼
Investigation Agent ── read-only, allowlisted, bounded tools ── evidence L/H/D/P/C ── 1 Gemini call
   │  POST .../analyze
   ▼
Root Cause Agent ── stored evidence as one timeline ── cited root cause + causal chain ── 1 Gemini call
   │  POST .../remediate
   ▼
Remediation Agent ── proposes 1 of 4 allowlisted actions ── backend policy: risk, approval, versions ── 1 Gemini call
   │  approval PENDING  (incident AWAITING_APPROVAL)
   ▼
Human approval  POST .../approve | .../reject   (no request body)
   │
   ▼
Execution ── backend re-validates and runs exactly the stored parameters, once (simulated rollback) ── 0 AI calls
   │  POST .../verify
   ▼
Verification Agent ── 6 deterministic backend checks decide RESOLVED / FAILED ── 1 Gemini call (summary only)
   │
   ▼
Incident report ── assembled from stored results, stored once ── 0 AI calls
   │
   ▼
Next.js dashboard ◀── REST (state, commands)  +  WS /ws/incidents/{id} (live events; REST polling fallback)

Cross-cutting:  AIProvider → GeminiProvider (gemini-3.1-flash-lite) → key pool (4 keys: round-robin,
                cooldown, fallback, bounded retries) · PostgreSQL on Supabase (RLS on, backend-only access)
                · Docker Compose (backend + frontend) · GitHub Actions (lint, tests on PostgreSQL,
                alembic check, frontend build, Docker smoke test)
```

Every step is a separate REST command that the backend accepts only in the right incident state
(atomic, conflict-safe transitions); repeating a completed step returns the stored result without
another AI call or execution.

## Components

| Component | Tech | Responsibility |
| --- | --- | --- |
| Dashboard | Next.js, React, Tailwind, Framer Motion | Incident views, live timeline, approvals |
| API | FastAPI, Pydantic | REST for state and commands, WebSocket for live events |
| Orchestrator + agents | Python | Investigation → root cause → remediation → verification |
| AI provider | `AIProvider` → Gemini (MVP), Ollama (future) | LLM calls behind one interface |
| Database | Supabase PostgreSQL via SQLAlchemy (async) + asyncpg, Alembic | Incidents, telemetry, agent events, approvals, reports |

REST carries persistent state and commands; WebSocket carries live event streams only.

```text
Next.js ──REST + WebSocket──▶ FastAPI ──SQLAlchemy + asyncpg──▶ Supabase PostgreSQL
```

## Database hosting: Supabase

- **Supabase is used only as managed PostgreSQL.** Auth, Realtime, Storage, Edge Functions and
  the Supabase client libraries are not used. The backend owns all database access.
- **Connection:** the Supavisor *session pooler* (port 5432). It is reachable over IPv4 (the
  direct `db.<ref>.supabase.co` host is IPv6-only by default, which Docker usually lacks) and,
  unlike the transaction pooler (port 6543), supports asyncpg's prepared statements. SSL is
  enforced (`ssl=require`) for Supabase hosts. The pool is small (5 + 5 overflow) because the
  pooler's connection limit is shared by every teammate.
- **No browser access.** The frontend never receives database credentials or the Supabase
  anon key. Letting the browser query the database would bypass backend rules such as the
  human approval gate.
- **RLS:** enabled on every table with no policies (migration `0002`). This blocks Supabase's
  auto-generated Data API, which is reachable with the public anon key. The backend connects
  as the table owner and is unaffected. Per-user policies are unnecessary because there is no
  end-user database access.
- **No local database container.** Docker PostgreSQL was removed so there is exactly one
  development database (no drift between two setups) that the whole team shares.
- **Migrations are explicit.** Containers do not run `alembic upgrade` on start. Tests never
  use `DATABASE_URL` (see README → Testing).

## Data model

| Table | Purpose |
| --- | --- |
| `incidents` | One row per incident; `status` follows the incident state machine; shown as `INC-001` |
| `logs` | Simulated application logs (`service_name`, `timestamp`, `level`, `message`, `metadata`) |
| `deployments` | Deployment history (`version`, `status`, `commit_sha`) |
| `service_health` | Health snapshots (`status`, `error_rate`, `latency_ms`, `cpu_usage`, `memory_usage`) |
| `agent_runs` | One execution of a logical agent, with a user-safe `summary` and validated `output` JSON |
| `agent_events` | Timeline events; persisted and broadcast over `WS /ws/incidents/{id}` |
| `approvals` | Human approval gate for risky actions (`action_type`, `target`, `risk`, `status`, `decided_at`) |
| `incident_reports` | Final report: root cause, confidence, evidence, action taken, recovery status |
| `cicd_events` | Normalized GitHub CI/CD events (push, workflow_run, deployment_status); unique `delivery_id`, optional link to the `deployments` row it created or matched |

Every agent, event, approval, and report row carries `incident_id` (with `ON DELETE CASCADE`), so
concurrent investigations never mix evidence.

Enums are stored as `VARCHAR` rather than native PostgreSQL enums, so adding a value needs no type
migration. Timestamps are timezone-aware.

### Incident states

`DETECTED → INVESTIGATING → ANALYZING → AWAITING_APPROVAL → REMEDIATING → VERIFYING → RESOLVED`,
with `FAILED` and `ESCALATED` as terminal alternatives.

## Simulated environment (Phase 3)

The "production" the agents investigate is simulated and deterministic, and it is persisted in the
same tables real telemetry would use.

| Module | Responsibility |
| --- | --- |
| `app/services/service_catalog.py` | The simulated services (`payment-api`, `auth-api`, `database`) and their dependencies |
| `app/services/scenario.py` | Pure data: the incident and healthy timelines as offsets from an anchor time, plus the deterministic severity rule |
| `app/services/simulator.py` | `simulate_incident` and `reset_demo`: write the scenario through SQLAlchemy |
| `app/services/telemetry.py` | Read queries (latest health, logs, deployments). Shared by the REST API now and the agents' read-only tools later |
| `app/api/routes/{incidents,services,demo}.py` | Thin HTTP layer: validation, 404s, response schemas |

Design choices:

- **Telemetry describes the environment, not an incident.** `logs`, `deployments` and
  `service_health` have no `incident_id`. When a new simulated incident starts, the simulator
  replaces the simulated services' telemetry with a fresh timeline anchored at "now", so runs
  never interleave. Only rows whose `service_name` is a simulated service are touched.
- **No duplicates.** `simulate` returns the active simulated incident if one exists (not
  `RESOLVED`/`FAILED`/`ESCALATED`). Past incidents remain as history for the future
  "previous incidents" tool.
- **Evidence, not answers.** No field states the root cause. Normal traffic and unrelated warnings
  are mixed in, and the database/auth services stay healthy, so the agents must correlate
  timestamps to reach the conclusion.
- **Reset** deletes incidents (children cascade), rewinds the incident id sequence so the demo
  always shows INC-001, and seeds a healthy baseline. It never drops or truncates tables.
- Database failures surface as `503 {"detail": "Database unavailable..."}` (`app/api/errors.py`).
  Only the exception type is logged.

## AI provider layer (Phase 4)

```text
Agents (Phase 5+) ──▶ AIProvider ──▶ GeminiProvider ──▶ GeminiKeyPool ──▶ key 1 … key 4 ──▶ Gemini API
```

| Module | Responsibility |
| --- | --- |
| `app/ai/base.py` | `AIProvider` interface (`generate`, `generate_structured`, `aclose`), `GenerationOptions`, `AIResponse` |
| `app/ai/errors.py` | `AIProviderError` and subclasses: configuration, authentication, rate limit, timeout, structured output |
| `app/ai/key_pool.py` | `GeminiKeyPool`: round-robin, per-key cooldown, usage stats; thread/async safe |
| `app/ai/gemini_provider.py` | `google-genai` async client, error classification, bounded retries across keys, JSON-schema output |
| `app/ai/factory.py` | `get_ai_provider()`: lazily built shared instance from `AI_PROVIDER` |

Model: `GEMINI_MODEL`, default `gemini-3.1-flash-lite` (pinned version, verified live with the
team's keys; `gemini-2.5-flash` is no longer available to them, and the `-latest` alias was often
overloaded). The model is configuration only: switching it needs no code changes.

- **Provider independence.** Agents import only `app.ai.base`, `app.ai.errors` and the factory.
  Adding Ollama + Qwen means one new `AIProvider` subclass and one factory branch.
- **Lazy initialization.** The provider is built on first use, so missing or invalid AI
  configuration never stops the API from starting; it surfaces as `AIConfigurationError` where
  AI is needed. An unknown `AI_PROVIDER` is reported the same way.
- **Retries.** Each attempt takes the next available key; at most `1 + LLM_MAX_RETRIES`
  attempts. The SDK's own retries are disabled so requests are never multiplied behind our back.
  429 and rejected keys put that key in cooldown; 5xx/timeouts/network errors retry after a short
  backoff without penalizing the key; 400 and 404 fail immediately.
- **Structured output.** `response_mime_type=application/json` plus the Pydantic model's JSON
  schema (`response_json_schema`), then `model_validate_json`. Truncated or invalid output raises
  `AIStructuredOutputError`; invalid data is never returned.
- **Secrets.** Keys live in `SecretStr`, are sent only in the SDK's `x-goog-api-key` header, and
  are identified in logs and stats as `gemini-key-<n>`. SDK exceptions are never chained into
  ours. Prompts and responses are not logged; token counts and latency are.
- **No execution of model output.** Automatic function calling is disabled; the model's output is
  data validated by the backend, never code or commands.

## Agents (Phase 5+)

See [`agent-design.md`](agent-design.md). Implemented: the **Remediation Agent** (Phase 7), which
proposes one supported action that the backend validates against policy and deployment data
and turns into a PENDING approval (nothing is executed; `app/services/remediation_policy.py`
decides risk and approval), the **Root Cause Analysis Agent**
(Phase 6), which correlates the stored investigation into the most likely cause with validated
citations (one structured call, no tools), and the **Investigation Agent**
(`app/agents/`), using read-only tools from `app/tools/` (permission registry + `ToolExecutor`),
a deterministic evidence collector, a short prompt (`app/prompts/`), and one structured call
through `AIProvider`. Runs and events are stored in `agent_runs` / `agent_events` (no schema
change); `GET /api/incidents/{id}/events` lists them. WebSocket streaming is Phase 10.

### Approval boundary

```text
Remediation Agent --proposal--> backend policy --valid--> approvals (PENDING, exact parameters)
                                               --invalid--> run FAILED, no approval
                                                                  |
                               POST /approve  -> backend re-validates -> simulated execution
                               POST /reject   -> ESCALATED, nothing executed          (Phase 8)
```

The model cannot set risk or approval, cannot name an unsupported action, and cannot pick a
rollback version that is not a previous successful deployment. No endpoint accepts an action from
a client, and no agent can run an approval-gated tool. Approve/reject take no body; execution
(`app/agents/approval.py`) uses only the approval's stored parameters, re-validated against live
deployment data, with no AI call.

### Verification (Phase 9)

`app/agents/verification.py` reads current telemetry and runs six deterministic recovery checks
(thresholds from `app/services/scenario.py`). The backend alone sets `recovered` and the
incident's final state (`RESOLVED` or `FAILED`); the model's schema contains only a summary and
cited evidence ids. Verification never executes, retries or proposes remediation.

## GitHub webhooks & CI/CD telemetry (Phase 11)

```text
GitHub → POST /api/webhooks/github → HMAC SHA-256 (raw body) → normalize (app/github)
       → cicd_events (unique delivery_id) → deployments (successful production deploys only)
       → agent_events of an active incident on that service → WS /ws/incidents/{id}
Investigation Agent → get_recent_cicd_events (READ_ONLY, ≤ 24 h, ≤ 10) → evidence C1… → RCA
```

The webhook layer is separate from the agents: agents read normalized rows, never raw payloads,
and never talk to GitHub. Ingestion is deterministic (no AI call). CI/CD failures are evidence
only; they never create incidents. Versions come from explicit deployment metadata or release
tags, never from guessing. The demo replays deterministic deliveries through the same ingestion
service when an incident is simulated. Details: [github-webhooks.md](github-webhooks.md).

## Security architecture

The AI proposes, the backend validates, a human approves risky actions, and the backend executes
only allowlisted actions.

**The AI can:** inspect the evidence the backend supplies (collected by allowlisted, bounded,
read-only tools); identify patterns; propose a root cause; propose one of four allowlisted
actions (`ROLLBACK_DEPLOYMENT`, `RESTART_SERVICE`, `NO_ACTION`, `ESCALATE_TO_HUMAN`); explain
the verification result.

**The AI cannot:** execute shell commands, SQL, HTTP requests or filesystem operations (no such
tool exists; the model only fills a JSON schema); invent evidence (citations to ids the backend
did not supply are dropped, and output without valid support fails); set risk or the approval
requirement; choose an unvalidated service or version; execute anything; bypass approval; decide
recovery or resolve an incident.

**The backend controls:**

| Control | Where |
| --- | --- |
| Tool allowlist per agent, READ_ONLY vs REQUIRES_HUMAN_APPROVAL, bounded Pydantic arguments, call cap, cache | `app/tools/registry.py` |
| Input validation: Pydantic schemas, bounded strings / time windows / limits, known services, known repositories | `app/schemas`, `app/api/routes`, `app/github` |
| Evidence citations (unknown ids removed; unsupported output fails) | each agent's guardrails |
| Incident state machine: atomic `UPDATE … WHERE status = expected` claims; conflicts → 409 | `app/agents/common.py` |
| Remediation policy: risk, approval requirement, target/version validation against real deployments | `app/services/remediation_policy.py` |
| Execution: approved, re-validated, exactly the stored parameters, exactly once | `app/agents/approval.py` |
| Verification: six deterministic checks decide the outcome | `app/agents/verification.py` |
| Secrets: environment only; never logged, returned, streamed or sent to the browser | `app/core/config.py` (SecretStr), tests |

**The human controls:** every risky remediation. Approve/reject take no request body, so a
client can neither change the action, target, version, risk or parameters nor approve anything
other than the stored proposal. Rejecting escalates the incident and executes nothing.

**External input** follows the same rule: GitHub webhooks are authenticated (HMAC SHA-256 over
the raw body, constant-time comparison), bounded (2 MiB), validated, idempotent, stored as data
only, and can only add evidence. They cannot trigger remediation, workflows, commands or
incidents. Webhook ingestion and report generation never call the AI.
