# OpsPilot Architecture

> Living document. Sections are filled in as each phase lands (see `CLAUDE.md` §51).

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

## Safety model

The AI proposes, the backend validates, a human approves risky actions, and the backend executes
only allowlisted actions. Read-only tools and approval-gated tools are separated by an explicit
permission model enforced in the orchestrator (Phase 6–8).
