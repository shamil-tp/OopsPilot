# 🚨 OpsPilot — AI-Powered Multi-Agent Incident Response System

> An AI-powered DevOps Copilot that investigates software incidents, identifies likely root causes, recommends safe remediation actions, and verifies whether the issue has been resolved.

---

## 📌 Overview

**OpsPilot** is an AI-powered incident response system designed to help developers investigate and resolve software incidents faster.

When an application suddenly starts producing errors, developers usually need to manually check application logs, service health, recent deployments, and previous incidents to identify the cause.

OpsPilot automates this investigation using a system of **five specialized AI agents**.

The system can:

- Understand an incoming incident
- Create an investigation plan
- Collect evidence from multiple sources
- Identify the likely root cause
- Recommend a corrective action
- Request human approval before risky actions
- Execute the approved action
- Verify whether the issue was actually resolved
- Generate an incident report

---

## 🎯 Problem Statement

When a software application suddenly fails or starts producing errors, developers must manually investigate logs, server status, recent deployments, and previous incidents to determine what went wrong.

This process can be time-consuming and may delay recovery.

OpsPilot addresses this problem by providing an AI-powered incident response workflow that can investigate incidents, identify possible causes, recommend safe corrective actions, and verify the result while keeping a human involved in risky decisions.

---

## 💡 Proposed Solution

OpsPilot uses multiple specialized AI agents instead of relying on a single AI chatbot.

Each agent is responsible for a specific stage of the incident response process.

```text
Incident Alert
      ↓
Incident Manager
      ↓
Investigation Agent
      ↓
Root Cause Agent
      ↓
Remediation Agent
      ↓
Human Approval
      ↓
Corrective Action
      ↓
Verification Agent
      ↓
Incident Report```

---

## 🏗️ Architecture

```text
Next.js dashboard  ──REST + WebSocket──▶  FastAPI backend  ──SQLAlchemy + asyncpg──▶  Supabase PostgreSQL
```

- **Supabase is used only as managed PostgreSQL.** No Supabase Auth, Realtime, Storage, Edge
  Functions, or client libraries.
- **Only the backend talks to the database.** The browser never receives database credentials
  and never queries Supabase directly: the backend validates every action (for example, human
  approval before a rollback), which a direct browser-to-database path would bypass.
- **Row Level Security is enabled on every table with no policies** (migration `0002`). This
  locks out Supabase's auto-generated Data API (reachable with the public anon key), while the
  backend, connecting as the table owner, is unaffected.
- **There is no database container.** Supabase is the single shared development database, so
  there is one database architecture to maintain and every teammate sees the same data.

## 🧱 Repository Layout

```text
.
├── backend/                 FastAPI + SQLAlchemy (async) + Alembic
│   ├── app/
│   │   ├── api/             REST routes (/api/...)
│   │   ├── agents/          investigation, root cause, remediation, verification agents; approval gate
│   │   ├── ai/              AIProvider interface, Gemini provider, key pool, factory
│   │   ├── core/            settings, structured logging
│   │   ├── db/              engine, session, declarative base
│   │   ├── models/          ORM models + domain enums
│   │   ├── prompts/         concise agent prompts
│   │   ├── schemas/         Pydantic request/response models
│   │   ├── services/        simulated environment, telemetry queries, business logic
│   │   ├── tools/           tool registry, permissions, read-only telemetry tools
│   │   ├── websocket/       live incident events         (Phase 10)
│   │   └── main.py
│   ├── alembic/             database migrations
│   ├── scripts/             check_database.py (non-destructive DB verification)
│   └── tests/
├── frontend/                Next.js (App Router) + TypeScript + Tailwind + Framer Motion
│   ├── app/  components/  hooks/  lib/  types/
├── docs/                    architecture.md, agent-design.md, api.md, demo.md
├── .github/workflows/       backend CI, frontend CI, Docker build + smoke test
├── docker-compose.yml       backend + frontend (the database is Supabase)
└── .env.example
```

## 🚀 Getting Started

### 1. Configure Supabase

1. Create a project at [supabase.com](https://supabase.com) and note the database password.
2. Open **Connect** in the project dashboard and copy the **Session pooler** connection string
   (`postgres.<project-ref>@aws-0-<region>.pooler.supabase.com:5432`).
   - *Session pooler* works over IPv4 (including from Docker) and supports the prepared statements
     asyncpg uses. The *direct connection* (`db.<project-ref>.supabase.co`) is IPv6-only unless
     you buy the IPv4 add-on. The *transaction pooler* (port 6543) breaks asyncpg's prepared
     statements, so don't use it.
3. `cp .env.example .env` and set `DATABASE_URL` to that string with your password filled in.
   URL-encode special characters in the password (`@` → `%40`, `/` → `%2F`, `#` → `%23`).
   `postgresql://` is converted to `postgresql+asyncpg://` automatically, and SSL is required
   for Supabase hosts.

### 2. Create the schema

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt

alembic current               # prints the target database (password masked); check it first
alembic upgrade head          # creates/updates the tables
python -m scripts.check_database
```

Alternatively, paste [`docs/supabase-schema.sql`](docs/supabase-schema.sql) into the Supabase SQL
Editor. It creates exactly the same schema and records revision `0003` for Alembic. Use one
method or the other, not both.

`check_database` verifies the connection, the Alembic revision, that all 8 tables exist with RLS
enabled, and CRUD across every table. Its writes run in one transaction that is always rolled
back, so it never changes data.

> ⚠️ The Supabase database is shared by the whole team. Never run `alembic downgrade` against
> it, and coordinate before applying a new migration.

### 3. Run the app

**Local development**

```bash
# Backend
cd backend
uvicorn app.main:app --reload --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev
```

**Docker Compose** (backend + frontend; reads `DATABASE_URL` from `.env`)

```bash
docker compose up --build
docker compose run --rm backend alembic upgrade head   # only when there are new migrations
```

- Dashboard: http://localhost:3000
- API docs (OpenAPI): http://localhost:8000/docs
- Health: http://localhost:8000/api/system/health (503 with `database.status = "unavailable"`
  if Supabase can't be reached; the app never falls back to another database)

## 🎬 Demo: simulated incident (backend)

OpsPilot investigates a deterministic, simulated production environment (`payment-api`,
`auth-api`, `database`) whose logs, deployments and health metrics are stored in PostgreSQL.

1. Start the backend with `DATABASE_URL` pointing at Supabase (see above).
2. `POST /api/demo/reset`: healthy environment, payment-api on v1.8.1, no incidents.
3. `POST /api/incidents/simulate`: v1.8.2 is deployed, database connection errors and HTTP 500s
   follow, and incident **INC-001** (HIGH, DETECTED) is created.
4. `GET /api/incidents/1`
5. `GET /api/services/payment-api/health`: `DEGRADED`, error rate 37%, latency 2800 ms
6. `GET /api/services/payment-api/logs?level=ERROR&level=WARN`
7. `GET /api/services/payment-api/deployments`
8. `POST /api/incidents/1/investigate`: the **Investigation Agent** gathers bounded evidence
   with read-only tools and makes one structured Gemini call; `GET /api/incidents/1/events`
   shows its timeline. The incident moves to `INVESTIGATING`.
9. `POST /api/incidents/1/analyze`: the **Root Cause Analysis Agent** correlates the stored
   evidence into the most likely cause (v1.8.2's database configuration change), with cited
   evidence, a causal chain and ruled-out alternatives. The incident moves to `ANALYZING`.
10. `POST /api/incidents/1/remediate`: the **Remediation Agent** proposes a rollback of
    payment-api v1.8.2 → v1.8.1; the backend validates it, sets risk MEDIUM, and creates a
    `PENDING` approval (incident `AWAITING_APPROVAL`). Nothing is executed yet.
11. `POST /api/incidents/1/approve` (or `/reject`): the backend re-validates and executes the
    approved rollback (simulated): v1.8.1 active, payment-api HEALTHY (0.8%, 180 ms), incident
    `VERIFYING`. No AI call. Rejecting escalates the incident instead.
12. `POST /api/incidents/1/verify`: the **Verification Agent**'s backend checks confirm recovery
    (v1.8.1 active, HEALTHY, 0.8% < 5%, 180 ms < 500 ms, fresh telemetry): incident `RESOLVED`.
13. Observe the chain the agents inferred (it is never stated in the data):

   ```text
   deployment v1.8.2 → database connection errors → payment API 500s → degraded health
   ```

Try it in Swagger UI at http://localhost:8000/docs. Repeating **simulate** while the incident is
active returns the same incident; **reset** makes the demo repeatable. Details:
[`docs/demo.md`](docs/demo.md).

## 🔌 API Overview

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/system/health` | App, database, and AI provider status |
| `POST` | `/api/incidents/simulate` | Create the simulated payment-api incident (idempotent while active) |
| `GET` | `/api/incidents` | Incidents, newest first |
| `GET` | `/api/incidents/{id}` | Incident details (`404` if unknown) |
| `POST` | `/api/incidents/{id}/investigate` | Run the Investigation Agent (read-only tools + 1 structured AI call) |
| `GET` | `/api/incidents/{id}/events` | Agent timeline, oldest first |
| `POST` | `/api/incidents/{id}/analyze` | Run the Root Cause Analysis Agent on the stored investigation (1 structured AI call) |
| `GET` | `/api/incidents/{id}/analysis` | Latest root cause analysis |
| `POST` | `/api/incidents/{id}/remediate` | Run the Remediation Agent: validated proposal + PENDING approval (no execution) |
| `GET` | `/api/incidents/{id}/remediation` | Latest remediation proposal and approval |
| `POST` | `/api/incidents/{id}/approve` | Approve the pending proposal; backend executes it (simulated) |
| `POST` | `/api/incidents/{id}/reject` | Reject the pending proposal; incident escalated |
| `GET` | `/api/incidents/{id}/execution` | Execution of the approved remediation |
| `POST` | `/api/incidents/{id}/verify` | Run the Verification Agent: backend recovery checks → RESOLVED or FAILED |
| `GET` | `/api/incidents/{id}/verification` | Latest verification result |
| `GET` | `/api/services` | Simulated services and their current status |
| `GET` | `/api/services/{name}/health` | Latest health snapshot |
| `GET` | `/api/services/{name}/logs` | Logs, chronological; filter by `level`, `since`, `until`, `limit` |
| `GET` | `/api/services/{name}/deployments` | Deployment history, newest first |
| `POST` | `/api/demo/reset` | Reset to a healthy environment with no incidents |

Full reference: [`docs/api.md`](docs/api.md) and the OpenAPI docs at `/docs`.

## ⚙️ Environment Variables

All configuration comes from environment variables; see [`.env.example`](.env.example) for the full list.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | **Required.** Supabase session-pooler connection string |
| `TEST_DATABASE_URL` | Separate database for the test suite (defaults to local SQLite) |
| `GEMINI_API_KEY_1` … `GEMINI_API_KEY_4` | Gemini keys shared by all agents through a key pool (never logged) |
| `GEMINI_MODEL` | Gemini model used by the agents |
| `GEMINI_KEY_COOLDOWN_SECONDS` | How long a rate-limited key is skipped (default 60; Gemini's own retry delay wins when given) |
| `LLM_TIMEOUT_SECONDS`, `LLM_TEMPERATURE`, `LLM_MAX_OUTPUT_TOKENS` | Defaults for every LLM request (30 / 0.2 / 2048); agents can override per call |
| `AI_PROVIDER` | `gemini` (Ollama + Qwen is planned, not implemented) |
| `MAX_AGENT_STEPS`, `LLM_MAX_RETRIES`, `TOOL_MAX_RETRIES` | Agent loop safety limits (8 / 2 / 2) |
| `GITHUB_WEBHOOK_SECRET` | Optional; validates GitHub webhook signatures |
| `CORS_ORIGINS` | Comma-separated origins allowed to call the API |
| `NEXT_PUBLIC_API_URL` | API URL used by the browser (baked in at frontend build time) |

Never commit `.env`, API keys, database passwords, or webhook secrets. The frontend only ever
gets `NEXT_PUBLIC_API_URL`.

### Gemini

Create API keys in [Google AI Studio](https://aistudio.google.com/apikey) and set any of
`GEMINI_API_KEY_1`–`4` (at least one for AI features; the rest of the API works without any).
`/api/system/health` reports how many are configured. The SDK is
[`google-genai`](https://pypi.org/project/google-genai/).

All agents share one provider (`app.ai.factory.get_ai_provider()`) and one key pool:

- **Round-robin** over the configured keys; keys are logged only as `gemini-key-<n>`.
- **Fallback:** a retryable failure (429 rate limit/quota, invalid key, 5xx, timeout, network)
  moves to the next available key; at most `1 + LLM_MAX_RETRIES` attempts per request.
- **Cooldown:** a rate-limited key is skipped for Gemini's suggested delay or
  `GEMINI_KEY_COOLDOWN_SECONDS`; a rejected key for 15 minutes. Never permanently.
- **No retry** for malformed requests (400) or an unknown model (404): they fail immediately
  with a clear `AIProviderError` / `AIConfigurationError`.
- **Structured output:** `generate_structured(prompt, PydanticModel)` requests JSON with the
  model's JSON schema and returns a validated instance, or raises `AIStructuredOutputError`.

The default model is **`gemini-3.1-flash-lite`**: a pinned version (it never changes
underneath the demo), verified live for text and structured output with the team's free-tier
keys, and cheap (no hidden thinking tokens). Alternatives seen during Phase 4: `gemini-2.5-flash`
returns *404: no longer available to new users* for these keys; `gemini-flash-latest` and
`gemini-3.8-flash` work but often return *503 high demand*. If root-cause reasoning needs a
stronger model later, change `GEMINI_MODEL` only; no code changes.

## 🧪 Testing

```bash
cd backend
ruff check . && ruff format --check .
pytest                        # local SQLite file
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/opspilot_test pytest

cd ../frontend
npm run lint && npm run typecheck && npm run build
```

Tests build the schema through the real Alembic migrations and **migrate down to an empty schema**,
so they only ever use `TEST_DATABASE_URL` and **refuse to start if it points at the same database
as `DATABASE_URL`**. Never point it at the shared Supabase database. For PostgreSQL coverage, use
a throwaway local PostgreSQL or a separate Supabase project; CI uses a disposable PostgreSQL
service container.

## 🔁 CI/CD

| Workflow | What it does |
| --- | --- |
| `backend-ci.yml` | Ruff lint/format, pytest against a disposable PostgreSQL service, `alembic check` for model/migration drift |
| `frontend-ci.yml` | `npm ci`, ESLint, TypeScript, production build |
| `docker.yml` | Builds both images, starts Compose, checks the backend boots and reports a database outage (CI never connects to Supabase) and the dashboard serves |

## 📍 Status

- [x] **Phase 1** — monorepo, FastAPI + Next.js skeletons, Docker Compose, CI
- [x] **Phase 2** — database models and migrations, hosted on Supabase PostgreSQL
- [x] **Phase 3** — simulated incident environment: services, deployments, logs, health, incident + demo reset APIs
- [x] **Phase 4** — AI provider interface, Gemini provider (text + structured output), multi-key pool with round-robin, cooldown and fallback
- [x] **Phase 5** — Investigation Agent: read-only tools with permission enforcement, deterministic evidence collection, one structured Gemini call, stored runs/events, investigate + events APIs
- [x] **Phase 6** — Root Cause Analysis Agent: correlates the stored investigation (timeline, dependencies, alternatives) into a cited, validated root cause; analyze + analysis APIs
- [x] **Phase 7** — Remediation Agent: proposes a supported action; backend policy validates target/version/citations, sets risk and approval, creates a PENDING approval (no execution)
- [x] **Phase 8** — Human approval gate (approve/reject, no request body) and deterministic simulated execution of the stored, re-validated proposal; incident → VERIFYING
- [x] **Phase 9** — Verification Agent: six deterministic backend recovery checks decide RESOLVED/FAILED; one AI call explains with validated citations
- [ ] **Phase 10** — Incident report & real-time dashboard integration (next)
