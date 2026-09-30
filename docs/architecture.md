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

## Safety model

The AI proposes, the backend validates, a human approves risky actions, and the backend executes
only allowlisted actions. Read-only tools and approval-gated tools are separated by an explicit
permission model enforced in the orchestrator (Phase 6–8).
