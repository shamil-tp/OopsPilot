# OpsPilot

**AI-Powered Autonomous Incident Response & DevOps Copilot**

OpsPilot is an **AI-assisted incident response and DevOps copilot with deterministic backend
controls and human approval for risky remediation**. It investigates a production incident from
structured operational evidence (logs, health, deployments, previous incidents, GitHub CI/CD
events), identifies the most likely root cause with citations, proposes a safe remediation,
waits for a human to approve it, executes the approved action in a simulated environment,
verifies recovery with objective checks, and writes the incident report.

> The AI proposes. The backend validates. A human approves risky actions. The backend executes
> only allowlisted actions. Verification decides — not the model.

---

## Problem

When a service suddenly starts failing, on-call engineers have to piece the story together by
hand: which errors started when, what changed (a deploy? a config?), whether dependencies are
healthy, whether this happened before, what is safe to do, and whether the fix actually worked.
It is slow, error-prone and hard to explain afterwards. Asking a chatbot "what is wrong?" is not
an answer: it has no evidence, can invent causes, and must never be allowed to change production.

## Solution

OpsPilot runs a closed, explainable loop over **structured evidence** gathered by the backend:

```text
GitHub push / deploy ──webhook──▶ CI/CD telemetry ─┐
                                                   ▼
Incident detected ─▶ Investigation ─▶ Root cause ─▶ Remediation ─▶ HUMAN APPROVAL
                     (evidence + AI)   (AI, cited)  (AI proposes,    (approve / reject)
                                                    backend policy)        │
Report ◀─ Verification (6 backend checks decide) ◀─ Execution (backend, allowlisted, simulated)
```

| Why it matters | How OpsPilot does it |
| --- | --- |
| **Evidence-based** | The backend collects bounded, deterministic evidence first (±10 min of logs, health vs. baseline, dependencies, deployments, previous incidents, GitHub CI/CD events). Every AI claim must cite evidence ids (`L5`, `D1`, `C3`, …); unknown ids are dropped. |
| **Safe** | The LLM only fills a schema. Actions are allowlisted; risk and "requires approval" come from backend policy; rollback versions are validated against real deployment history; a human approves; the backend re-validates and executes exactly the stored parameters. |
| **Closed loop** | Investigate → analyze → remediate → approve → execute → verify → report. Recovery is decided by deterministic checks, not by the model. |
| **Explainable** | Evidence, citations, causal chain, alternatives considered, live timeline, approval record, before/after metrics, and a downloadable report. |
| **DevOps-aware** | Signed GitHub webhooks (push, workflow runs, deployments) become CI/CD evidence and deployment telemetry, so "what changed?" is answered with commits and workflow runs. |

## Key Features

- **AI investigation** over bounded, deterministic evidence (1 Gemini call).
- **Evidence-based root cause analysis** with a cited causal chain, contributing factors and
  ruled-out alternatives (1 Gemini call).
- **GitHub / CI-CD integration**: HMAC-verified webhooks, idempotent on delivery id, normalized
  into CI/CD telemetry and correlated with deployments; cited as `C1`, `C2`, ….
- **Human-approved remediation**: the Remediation Agent proposes (1 Gemini call); the backend
  validates, sets risk, and creates a `PENDING` approval. Approve/Reject take **no request body**.
- **Automated verification**: six backend recovery checks decide `RESOLVED` or `FAILED`; the AI
  only writes the summary (1 Gemini call).
- **Incident report**: assembled from stored results with **no AI call**; Markdown/JSON download.
- **Real-time dashboard**: WebSocket event stream with automatic REST polling fallback.
- **Demo mode**: deterministic scenario, one-click reset, repeatable end to end.

## Architecture

```text
 GitHub ──signed webhook──▶ ┌──────────────────────────── FastAPI backend ─────────────────────────────┐
                            │ /api/webhooks/github → verify HMAC → normalize → cicd_events → deployments│
 Next.js dashboard ◀─REST──▶│ /api/incidents/...   REST commands + state                                │
   (React, Tailwind,  ◀─WS──│ /ws/incidents/{id}   live agent events (tails agent_events)                │
    Framer Motion)          │                                                                           │
                            │ Agents (orchestrated per REST step, one shared AI provider):              │
                            │   Investigation ─▶ Root Cause ─▶ Remediation ─▶ [Human approval gate]     │
                            │   ─▶ Execution (backend, simulated) ─▶ Verification ─▶ Report (no AI)     │
                            │ Read-only tool allowlist · remediation policy · state machine · key pool  │
                            └──────────────┬─────────────────────────────────────────┬──────────────────┘
                                           │ SQLAlchemy + asyncpg                    │ google-genai
                                           ▼                                         ▼
                              Supabase PostgreSQL (RLS on)            Gemini gemini-3.1-flash-lite
                                                                      (4-key pool: round-robin,
 Docker Compose (backend + frontend) · GitHub Actions (lint, tests,    cooldown, fallback)
 migrations drift check, build, Docker smoke test)
```

- **Supabase is used only as managed PostgreSQL.** Only the backend talks to the database; the
  browser never gets database credentials. Row Level Security is enabled on every table with no
  policies, which locks out Supabase's public Data API. There is no database container.
- Details: [`docs/architecture.md`](docs/architecture.md) · [`docs/agent-design.md`](docs/agent-design.md)
  · [`docs/github-webhooks.md`](docs/github-webhooks.md) · [`docs/api.md`](docs/api.md)

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js (App Router), React, TypeScript (strict), Tailwind CSS, Framer Motion |
| Backend | Python, FastAPI, Pydantic, SQLAlchemy (async) + asyncpg, Alembic, Uvicorn |
| Database | PostgreSQL on Supabase |
| AI | Google Gemini (`gemini-3.1-flash-lite`) behind an `AIProvider` interface (Ollama/Qwen planned) |
| DevOps | Docker, Docker Compose, GitHub Actions, GitHub Webhooks |

## Demo Flow

The full 5–7 minute script, with timings and emergency recovery, is in
[`docs/demo.md`](docs/demo.md). In short:

```text
Reset ─▶ GitHub push + deploy-production (v1.8.2) ─▶ Simulate Incident (INC-001, HIGH, 37% / 2800 ms)
─▶ Start investigation ─▶ Run root cause analysis ─▶ Propose remediation (rollback v1.8.2 → v1.8.1)
─▶ Approve (human) ─▶ rollback executes ─▶ Verify recovery (0.8% / 180 ms) ─▶ RESOLVED ─▶ Report
```

## 🧱 Repository Layout

```text
.
├── backend/                 FastAPI + SQLAlchemy (async) + Alembic
│   ├── app/
│   │   ├── api/             REST routes (/api/...)
│   │   ├── agents/          investigation, root cause, remediation, verification agents; approval gate; report
│   │   ├── ai/              AIProvider interface, Gemini provider, key pool, factory
│   │   ├── core/            settings, structured logging
│   │   ├── db/              engine, session, declarative base
│   │   ├── github/          webhook signature verification, payload normalization, demo deliveries
│   │   ├── models/          ORM models + domain enums
│   │   ├── prompts/         concise agent prompts
│   │   ├── schemas/         Pydantic request/response models
│   │   ├── services/        simulated environment, telemetry, CI/CD ingestion, business logic
│   │   ├── tools/           tool registry, permissions, read-only telemetry/CI-CD tools
│   │   ├── websocket/       live incident event stream
│   │   └── main.py
│   ├── alembic/             database migrations (0001–0004)
│   ├── scripts/             check_database.py, send_github_webhook.py
│   └── tests/
├── frontend/                Next.js (App Router) + TypeScript + Tailwind + Framer Motion
│   ├── app/  components/  hooks/  lib/  types/
├── docs/                    architecture, agent design, API, GitHub webhooks, demo script, final QA
├── .github/workflows/       backend CI, frontend CI, Docker build + smoke test
├── docker-compose.yml       backend + frontend (the database is Supabase)
└── .env.example
```

## 🚀 Setup

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
Editor. It creates exactly the same schema and records revision `0005` for Alembic. Use one
method or the other, not both.

`check_database` verifies the connection, the Alembic revision, that all 9 tables exist with RLS
enabled, and CRUD across every table. Its writes run in one transaction that is always rolled
back, so it never changes data.

> ⚠️ The Supabase database is shared by the whole team. Never run `alembic downgrade` against
> it, and coordinate before applying a new migration.

### 3. Run the app

**Local development**

```bash
# Backend (from backend/, with the venv active)
uvicorn app.main:app --reload --port 8000

# Frontend (second terminal)
cd frontend
npm ci
npm run dev                   # or: npm run build && npm start
```

Then open http://localhost:3000, click **Reset demo**, and follow the [demo flow](#demo-flow).

**Docker Compose** (backend + frontend; reads `DATABASE_URL` from `.env`)

```bash
docker compose up --build
docker compose run --rm backend alembic upgrade head   # only when there are new migrations
```

- Dashboard: http://localhost:3000
- API docs (OpenAPI): http://localhost:8000/docs
- Health: http://localhost:8000/api/system/health (503 with `database.status = "unavailable"`
  if Supabase can't be reached; the app never falls back to another database)


## 🎬 Quick demo from the API (no browser)

Everything the dashboard does is a REST call; you can run the whole lifecycle from Swagger UI at
http://localhost:8000/docs or with curl:

```bash
curl -X POST localhost:8000/api/demo/reset                 # healthy, payment-api v1.8.1, no incidents
python -m scripts.send_github_webhook                      # optional: signed GitHub push + deploy (needs GITHUB_WEBHOOK_SECRET)
curl -X POST localhost:8000/api/incidents/simulate         # INC-001, HIGH, DETECTED (replays the GitHub deliveries)
curl -X POST localhost:8000/api/incidents/1/investigate    # INVESTIGATING  (1 Gemini call)
curl -X POST localhost:8000/api/incidents/1/analyze        # ANALYZING      (1 Gemini call)
curl -X POST localhost:8000/api/incidents/1/remediate      # AWAITING_APPROVAL: rollback v1.8.2 -> v1.8.1, PENDING
curl -X POST localhost:8000/api/incidents/1/approve        # backend executes the stored rollback -> VERIFYING (no AI)
curl -X POST localhost:8000/api/incidents/1/verify         # 6 backend checks -> RESOLVED (1 Gemini call)
curl localhost:8000/api/incidents/1/report                 # stored report (no AI)
```

The chain the agents infer — it is never stated in the data:

```text
GitHub deploy-production v1.8.2 (config change) → database connection failures → POST /payment 500s → 37% errors, 2800 ms
```

Repeating a step is safe (it returns the stored result, no new AI call or execution); steps out of
order return `409`. Details: [`docs/demo.md`](docs/demo.md).

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
| `GET` | `/api/incidents/{id}/investigation` | Latest investigation (evidence incl. CI/CD) |
| `GET` | `/api/incidents/{id}/report` | Final incident report (stored once after verification) |
| `WS` | `/ws/incidents/{id}` | Live agent/CI-CD events for one incident |
| `POST` | `/api/webhooks/github` | GitHub webhook (HMAC-verified): push, workflow_run, deployment_status |
| `GET` | `/api/webhooks/github/status` | Webhook configuration (never the secret) |
| `GET` | `/api/cicd/events` | Normalized CI/CD events (bounded filters) |
| `GET` | `/api/cicd/events/{id}` | One CI/CD event |
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
| `GITHUB_WEBHOOK_SECRET` | Secret for GitHub webhook HMAC SHA-256 signatures; unset = deliveries rejected (503) |
| `GITHUB_REPOSITORY`, `GITHUB_SERVICE` | Optional repository allowlist (`owner/repo`) and the service it deploys (default: `MONITORED_SERVICE`) |
| `DEMO_MODE` | `true` (default) enables the simulated payment-api scenario; `false` hides and refuses Simulate/Reset for a real deployment |
| `MONITORED_PROJECT_NAME`, `MONITORED_ENVIRONMENT`, `MONITORED_SERVICE`, `MONITORED_SERVICE_URL` | The real application OpsPilot watches (e.g. `Mallu Typing`, `production`, `mallutyping-web`, `https://mallutyping.nihalt.in`); the backend health-checks the URL |
| `MONITORED_HEALTH_INTERVAL_SECONDS`, `MONITORED_LATENCY_SLO_MS` | Health-check interval (60 s) and the latency above which a check counts as degraded (3000 ms) |
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
| `docker.yml` | Builds both images, starts Compose, checks the backend boots and reports a database outage (CI never connects to Supabase), the GitHub webhook refuses deliveries without a secret, and the dashboard serves |

CI never needs real secrets: Gemini and GitHub are faked in tests (webhooks are signed with a
test secret inside the test suite), and the database is a disposable PostgreSQL container.

### GitHub webhook → CI/CD telemetry

`POST /api/webhooks/github` verifies `X-Hub-Signature-256` over the raw body, normalizes `push`,
`workflow_run` and `deployment_status` events into `cicd_events` (idempotent on
`X-GitHub-Delivery`), links successful production deploys to the `deployments` table (versions
are never invented), and adds them to an active incident's live timeline. The Investigation Agent
reads them through the read-only `get_recent_cicd_events` tool and cites them as `C1`, `C2`, ….
Setup (repository webhook, secret, events) and local testing with
`python -m scripts.send_github_webhook`: [`docs/github-webhooks.md`](docs/github-webhooks.md).

## 🔒 Security & AI Safety

| The AI **can** | The AI **cannot** |
| --- | --- |
| read the evidence the backend collected with allowlisted, bounded, read-only tools | run shell commands, arbitrary SQL, arbitrary HTTP, or touch the filesystem |
| identify patterns and propose a root cause (with citations) | invent evidence (unknown citation ids are dropped; unsupported output fails) |
| propose one of four allowlisted actions | choose risk, approval requirement, or an unvalidated target/version |
| explain the verification result | decide recovery or resolve an incident |

**The backend controls** tool allowlists and argument bounds, input validation (Pydantic, bounded
strings/windows/limits, known services), evidence citations, the incident state machine (atomic,
conflict-safe transitions), remediation policy (risk, approval, version validation), execution
(re-validated, exactly the stored parameters, once), and verification. **A human controls** every
risky remediation: approve/reject endpoints take no body, so a client cannot change what is
executed. GitHub webhooks are HMAC SHA-256 verified over the raw body, size-limited, idempotent,
stored as data only, and never trigger actions. Secrets come only from environment variables and
never appear in logs, API responses, WebSocket messages or the browser bundle (tested).

## ⚠️ Limitations

- **Simulated environment.** Services, telemetry and the rollback are simulated in PostgreSQL; there
  is no real infrastructure control, by design.
- **One scenario.** The demo tells one deterministic story (payment-api v1.8.2 regression). The
  agents are generic, but have only been exercised on this scenario.
- **LLM wording varies.** Gemini's phrasing of findings and root cause differs between runs; the
  backend guarantees citations, schema, actions and outcome, not wording. The root cause is the
  *most likely* cause, not a proof.
- **No authentication.** The API is meant for a local/demo deployment; anyone who can reach it can
  approve a remediation. Put it behind authentication before exposing it.
- **Shared database.** Reset clears all incidents in the configured database.
- **Times are UTC** everywhere (dashboard, evidence, reports).
- **Ollama/Qwen** is designed for (provider interface) but not implemented.

## 👥 Team

A 4-person student hackathon team. Roles as defined in [`CLAUDE.md`](CLAUDE.md) §50:

| Role | Owns |
| --- | --- |
| AI / Agents — _name_ | `backend/app/agents/`, `backend/app/ai/`, `backend/app/prompts/` |
| Backend / DevOps simulation — _name_ | `backend/app/api/`, `tools/`, `services/`, `models/`, `db/` |
| Frontend — _name_ | `frontend/` |
| Integration / CI-CD / QA — _name_ | `.github/`, `docs/`, `docker-compose.yml`, tests |

## 📍 Status

All 12 phases are implemented: simulated environment, AI provider and key pool, the four agents,
human approval and execution, verification, report, real-time dashboard, GitHub webhook and CI/CD
telemetry, and final QA ([`docs/final-qa.md`](docs/final-qa.md)).
