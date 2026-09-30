# OpsPilot — Final QA (Phase 12)

Run on 2026-09-30 / 2026-10-01 (UTC) on Windows 11: backend (uvicorn) and frontend
(`next start`, production build) run locally against the shared **Supabase** PostgreSQL, with
real Gemini (`gemini-3.1-flash-lite`, 4-key pool). The UI was driven in **headless Chrome**
(DevTools protocol: real clicks, screenshots, console/network capture). Automated tests use fake
AI providers only. Only results that were actually observed are marked PASS.

## Result matrix

| Area | Test | Expected | Result |
| --- | --- | --- | --- |
| Reset | `POST /api/demo/reset` ×3 (+ full simulation between runs in tests) | Same state every time: 0 incidents, all services HEALTHY, payment-api v1.8.1 active, 0 CI/CD events | **PASS** (live ×3, 1.1–1.7 s; `test_reset_is_deterministic`) |
| GitHub | Signed `push`, tag `push`, `workflow_run` over real HTTP | 201 recorded; v1.8.2 resolved from the tag; deployment v1.8.2 created | **PASS** (live) |
| GitHub | Same delivery again | 200 duplicate, nothing written | **PASS** (live + tests) |
| GitHub | Bad / missing / malformed signature, tampered body, oversized body | 401 / 401 / 401 / 401 / 413 | **PASS** (tests; bad signature also live) |
| Incident | Click **Simulate Incident** | INC-001, payment-api, HIGH, DETECTED, 37% / 2800 ms, GitHub events on the timeline | **PASS** (browser) |
| Investigation | Click **Start investigation** | INVESTIGATING; 20 evidence items incl. CI/CD `C1–C3`; findings cite valid ids; fabricated ids dropped | **PASS** (browser + tests) |
| RCA | Click **Run root cause analysis** | ANALYZING; deployment/config change → DB connection failure → 500s → degraded; citations on root cause, chain, alternatives | **PASS** (browser; wording varies per run, citations validated) |
| Remediation | Click **Propose remediation** | ROLLBACK_DEPLOYMENT payment-api v1.8.2 → v1.8.1, risk MEDIUM, approval PENDING, AWAITING_APPROVAL; nothing editable | **PASS** (browser + tests) |
| Approval | Click **Approve**; approve again; reject after | APPROVED, REMEDIATING → VERIFYING, one execution; repeat 200 idempotent; reject 409 | **PASS** (browser + live repeat + tests) |
| Execution | Simulated rollback | v1.8.2 ROLLED_BACK, v1.8.1 active, 37% → 0.8%, 2800 → 180 ms, HEALTHY, exactly once | **PASS** (browser + tests) |
| Verification | Click **Verify recovery** | 6/6 backend checks pass, recovered, RESOLVED; AI cannot override | **PASS** (browser + tests) |
| Report | `GET /api/incidents/1/report`, report panel, `.md` download | Summary, timeline (GitHub → resolution), root cause, CI/CD evidence, remediation + human decision, before/after, outcome RESOLVED; no AI call | **PASS** (browser + live GET + tests) |
| WebSocket | Live events during the run | Badge LIVE; every state transition streamed in order; no duplicates | **PASS** (browser + tests) |
| Reconnect | Stop backend with the incident page open, then restart | Page keeps its data, shows POLLING + "Cannot reach the OpsPilot API"; returns to LIVE after restart | **PASS** (browser) |
| Refresh | Reload the resolved incident page | Full state and report restored over REST | **PASS** (browser) |
| Browser console | Whole click-through | 0 console errors / exceptions / failed requests | **PASS** (after fixes below) |
| Responsive | Dashboard + incident page at 390 / 768 / 1440 px | No horizontal overflow | **PASS** (after fix below) |
| State machine | Illegal transitions (e.g. DETECTED → verify/approve/analyze, ANALYZING → verify, AWAITING_APPROVAL → verify, ESCALATED → approve/verify, RESOLVED → reject) | 404/409, state unchanged, nothing executed | **PASS** (`test_illegal_transitions_are_rejected_and_change_nothing` and per-phase tests) |
| Idempotency | Repeat investigate / analyze / remediate / approve / verify / webhook | Stored result, 200, no new AI call, no second execution | **PASS** (live + tests) |
| Concurrency | 2× investigate, analyze, remediate, approve, approve+reject, verify, execute, report, webhook | One run / decision / execution / record; others 409 or duplicate | **PASS** (tests; investigate bug found and fixed) |
| AI budget | Whole pipeline | investigate 1, analyze 1, remediate 1, approve 0, execute 0, verify 1, report 0, webhook 0, simulate 0 | **PASS** (`test_each_phase_makes_exactly_its_intended_ai_calls`; live: 4 AI requests) |
| AI failures | Timeout, 429, 503, network, invalid key, invalid/truncated JSON, empty response, fabricated/missing evidence, unexpected exception | Bounded retries with key fallback; incident restored to the previous state; retryable; generic safe errors | **PASS** (tests; live: one Gemini 503 retried on another key) |
| Security | Secret scan (working tree, untracked files, full git history), logs, API/WS responses, browser bundle | No real secrets; only placeholders/test values | **PASS** |
| Security | `eval`/`exec`/`subprocess`/`os.system`/`shell=True`/raw SQL with input | None in application code | **PASS** |
| Backend | `ruff check`, `ruff format --check`, `pytest` | Clean; all pass | **PASS** — 346 passed, 1 skipped (RLS test: PostgreSQL only) |
| Frontend | `npm run lint`, `npm run typecheck`, `npm run build` | Clean | **PASS** |
| Database | `alembic current`, `alembic check`, `scripts.check_database` | 0004 head, no drift, 9 tables with RLS | **PASS** |
| Fresh clone | Copy of exactly the files git ships → new venv, `pip install -r requirements-dev.txt`, `pytest`, `npm ci`, lint/typecheck/build, `.env` configured, `alembic current`, `check_database`, start backend | Everything works from the README alone | **PASS** (346 passed / 1 skipped; `npm ci` 0 vulnerabilities; health 200 with DB + 4 keys) |
| CI | GitHub Actions on the last pushed commit (`6b7dd5e`) | Backend CI, Frontend CI, Docker all green | **PASS** for `6b7dd5e`; **not yet run** for the Phase 11–12 changes (not pushed) |
| Docker | `docker compose up --build` | Both services healthy | **NOT RUN locally** (Docker not installed on the QA machine); Docker workflow green on `6b7dd5e` |

## Bugs found and fixed in Phase 12

| Bug | Impact | Fix |
| --- | --- | --- |
| Two simultaneous **Start investigation** requests: the loser read an expired ORM attribute after rollback (`MissingGreenlet`) | 503 "Database unavailable" instead of 409 on a double-click / two viewers (PostgreSQL too) | Read the incident reference before rollback (`app/agents/investigation.py`); regression test added |
| "±" in an evidence message double-encoded ("Â±") by a Phase 11 edit | Garbled timeline text | Restored UTF-8 (`app/agents/evidence.py`); repo scanned for other mojibake: none |
| Backend facts/reports in UTC, UI in local 12-hour time | Same moment shown with two clock times | UI formats all times in UTC, 24-hour (`frontend/lib/format.ts`) |
| Incident page requested phase results before they existed | Expected 404/409 responses logged as red console errors | Fetch a phase's result only after the timeline shows that phase ran (`useIncidentConsole.ts`) |
| Grids without a mobile column definition | Dashboard 470 px wide on a 390 px phone; clipped text | `grid-cols-1` (minmax(0,1fr)) on responsive grids; wrapped deployment rows and service headers |
| Approval parameters shown as raw keys (`from_version`) in random order | Less clear approval card | Labels *Affected service / Current version / Target version*, fixed order (display only) |
| Orphaned `next start` servers from earlier QA runs on ports 3000/3001 | Stale builds served | Stopped; servers now started so stopping them stops Node |

## Performance (observed, not benchmarks)

Backend on a laptop, database on Supabase (ap-southeast-1) reached over the internet: every
database round trip costs ~0.3 s from this network, so **each simple GET takes ~0.6 s**
regardless of payload. That is network latency, not query cost (all list queries are bounded and
indexed).

| Step | Total (browser click → UI shows next state) | Of which Gemini |
| --- | --- | --- |
| Simulate incident (incl. GitHub replay) | 5.7 s | none |
| Investigation | 13.1 s | 4.7 s (393 output tokens) |
| Root cause analysis | 10.7 s | 4.6 s on the successful attempt, after one Gemini 503 retried on another key |
| Remediation proposal | 6.6 s | 3.8 s |
| Approve + execute | 3.7 s | none |
| Verification | 8.1 s | 2.4 s |
| Report `GET` | 0.58 s (28 KB) | none |
| Webhook delivery | 0.8–1.2 s warm, 3.1 s first (cold pool) | none |
| Reset | 1.1–1.7 s | none |
| Dashboard reads (`/incidents`, `/services`, `/cicd/events`, health) | ~0.6 s each, loaded in parallel | none |

The non-AI time in each agent step is database round trips (runs, events, evidence tools; each
committed so the live timeline updates). A backend deployed next to the database would remove most
of it.

## Final state

After the final run the demo was reset: 0 incidents, 0 pending approvals, 0 running agent runs,
0 CI/CD events, all services HEALTHY, payment-api v1.8.1 active, Alembic revision 0004.
