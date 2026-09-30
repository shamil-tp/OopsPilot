# OpsPilot Demo

A 5–7 minute live demo of one deterministic incident, from GitHub deploy to incident report.
Jump to: [checklist](#before-you-present-5-minutes-earlier) · [presenter script](#presenter-script-57-minutes) · [emergency recovery](#emergency-recovery).

## The scenario

A deterministic payment-api incident. Every run tells the same story, anchored to the moment you
trigger it (shown here as if the incident is detected at 11:44):

```text
6 days ago   payment-api v1.8.0 deployed
2 days ago   payment-api v1.8.1 deployed            (healthy since)
11:34-11:40  normal traffic: payments succeed, /health 200, a few unrelated warnings
11:40        GitHub: push to main, commit e4a7c52 "Release payment-api v1.8.2"; tag v1.8.2
11:41        GitHub Actions deploy-production #57 starts → payment-api v1.8.2 deployment starts
             configuration reloaded (changed keys: database.host, database.pool_size)
             deployment completes; deploy-production #57 SUCCESS (v1.8.2 from the tag)
11:42        ERROR Database connection failed / timeout / failed to acquire connection
             ERROR POST /payment 500 (repeatedly; some payments still succeed after retries)
11:43        health DEGRADED: error rate 37%, latency 2800 ms (CPU 43%, memory 68%: unchanged)
             WARN error rate and p95 latency alerts
11:44        incident INC-001 detected (severity HIGH)
```

Meanwhile `auth-api` and the `database` service stay **HEALTHY**, with normal connection counts.
This is evidence that the problem is specific to payment-api's new release, not the database.

The telemetry never states the root cause. The agents have to infer
*"database connection failure introduced by v1.8.2"* from the timing and content of the logs,
deployments, CI/CD events and health snapshots. The remediation is a rollback v1.8.2 → v1.8.1.

The GitHub part arrives as real, GitHub-shaped webhook deliveries: **Simulate** replays them
through the same ingestion service as `POST /api/webhooks/github`, so no GitHub repository or
secret is needed for the demo. The incident timeline shows them (`cicd_event_recorded`,
`deployment_detected`), the investigation cites them as `C1`–`C3`, and the incident page's
*CI/CD evidence* card lines them up with the first database error. To show the real HTTP path,
set `GITHUB_WEBHOOK_SECRET` and run `python -m scripts.send_github_webhook` (see
[github-webhooks.md](github-webhooks.md)).

## Before you present (5 minutes earlier)

1. Backend and frontend running (`uvicorn app.main:app --port 8000`, `npm start` or
   `docker compose up`); `GITHUB_WEBHOOK_SECRET` set in `.env` if you will show the webhook step.
2. Open http://localhost:3000. The **System status** card must show *Backend API online*,
   *PostgreSQL connected* and *gemini · gemini-3.1-flash-lite · 4 keys*.
3. Click **Reset demo** (or `curl -X POST localhost:8000/api/demo/reset`). Expected: *Active
   incidents (0)*, all three services **HEALTHY**, payment-api **v1.8.1 ACTIVE**, CI/CD activity
   empty. Reset is deterministic: it always produces exactly this state.
4. Optional, to show GitHub before the incident: `cd backend && python -m scripts.send_github_webhook`
   → the dashboard's **CI/CD activity** shows *push to main*, *tag v1.8.2* and
   *deploy-production #57 SUCCESS v1.8.2*, and v1.8.2 becomes the active deployment.
5. Close other tabs showing OpsPilot. Keep this document open on a second screen.

Each AI step takes roughly 5–15 s (one Gemini call plus database round trips); fill the wait by
narrating the live timeline. Measured timings are in [final-qa.md](final-qa.md).

## Presenter script (5–7 minutes)

| Time | Do | Say / point at |
| --- | --- | --- |
| 0:00–0:30 | Dashboard on screen | "OpsPilot is an AI incident response and DevOps copilot. It investigates production incidents, identifies the likely cause from evidence, recommends a safe remediation, requires human approval for risky actions, verifies recovery, and writes the incident report." |
| 0:30–1:00 | Point at the dashboard | Services all **HEALTHY**; payment-api deployments; **CI/CD activity** from GitHub: a push to main, tag v1.8.2 and the `deploy-production` run that shipped v1.8.2 (webhooks are HMAC-verified and stored as normalized telemetry). |
| 1:00–1:30 | Click **🚨 Simulate Incident** | The incident page opens: **INC-001**, **HIGH**, payment-api, **DETECTED**; "37% of payments fail, p95 latency 2800 ms". The *What changed before the incident* card already lines up the GitHub deploy with the first database error. |
| 1:30–2:30 | Click **Start investigation** | Live timeline (WebSocket): tools run (logs, health, deployments, previous incidents, CI/CD), evidence found. Findings are *observations* vs *hypotheses*, each with citation chips (hover a chip to see the fact). "It does not ask an LLM 'what's wrong?' — the backend gathers bounded evidence first, and every claim must cite it." |
| 2:30–3:15 | Click **Run root cause analysis** | Root cause card: deployment/config change → database connection failure → payment API 500s → degraded health; confidence; ruled-out alternatives (database healthy, CPU/memory unchanged); every step cites evidence (`L…`, `D1`, `C3`). |
| 3:15–4:00 | Click **Propose remediation** | **Roll back payment-api from v1.8.2 to v1.8.1**, risk **MEDIUM**, *Human approval required*. "Risk, approval and the target version come from backend policy and the real deployment history, not from the model. OpsPilot does not perform this risky action by itself — it requires human approval." Point out: nothing on this card is editable; Approve/Reject send no parameters. |
| 4:00–4:30 | Click **Approve** | Stepper goes **Remediating → Verifying**; execution card: v1.8.2 rolled back, v1.8.1 active, before/after metrics. "The backend re-validated the stored proposal and executed it exactly once." |
| 4:30–5:00 | Click **Verify recovery** | Six backend checks, all PASS: error rate **37% → 0.8%**, latency **2800 ms → 180 ms**, service **HEALTHY**; stepper shows **RESOLVED**. "The checks decide recovery; the AI only writes the summary." |
| 5:00–6:00 | Scroll to **Final incident report** | Root cause, CI/CD evidence (GitHub), remediation + human decision, recovery before/after, full timeline, final outcome. Click **Download report (.md)**. "The report is assembled from stored results — no AI call." |
| 6:00–7:00 | Architecture slide or README diagram | "The AI proposes decisions, but deterministic backend policy and human approval control risky actions." GitHub → webhook → CI/CD telemetry → agents → human approval → execution → verification → report, over one Gemini key pool, PostgreSQL on Supabase, live WebSocket, Docker and GitHub Actions. |

## Emergency recovery

| Symptom | Fix (fastest first) |
| --- | --- |
| Dashboard or incident page looks stale | Refresh the page. All state lives in the backend; the page reloads it over REST and reconnects the live stream (badge **LIVE**; **POLLING** means it is refreshing every 3 s over REST). |
| "Cannot reach the OpsPilot API" | The backend is down: restart `uvicorn` (or `docker compose restart backend`). The open page recovers by itself. |
| Investigation fails (red message, e.g. AI rate limit) | The incident goes back to **DETECTED**: click **Start investigation** again. If it keeps failing, **Reset demo** and start over. |
| Root cause analysis fails | The incident stays **INVESTIGATING** and the stored investigation is reused: click **Run root cause analysis** again (no new evidence collection). |
| Remediation proposal fails | Click **Propose remediation** again; if it still fails, **Reset demo**. |
| Approval fails or shows a conflict | Refresh (someone may already have decided). Otherwise **Reset demo**. Approving twice never executes twice. |
| Verification fails | Check the *Execution* card. If the incident is still **VERIFYING**, click **Verify recovery** again; if it is **FAILED**, explain the honest outcome or **Reset demo**. |
| Gemini unavailable for the whole demo | **Reset demo**, then walk through the dashboard, the CI/CD evidence card and a previously downloaded report; every non-AI part (webhooks, simulation, approval, execution, report) works without Gemini. |
| Need a clean start right now | `curl -X POST localhost:8000/api/demo/reset` (≈1–2 s), then refresh the dashboard. |

## Appendix: the same flow from the API

1. Start the backend with Supabase configured (`DATABASE_URL` in `.env`, schema at revision 0004):

   ```bash
   cd backend
   uvicorn app.main:app --reload --port 8000
   ```

2. Reset to a healthy environment, then simulate the incident:

   ```bash
   curl -X POST http://localhost:8000/api/demo/reset
   curl -X POST http://localhost:8000/api/incidents/simulate
   ```

3. Open the incident: `curl http://localhost:8000/api/incidents/1`
4. Service health (DEGRADED, 37%, 2800 ms): `curl http://localhost:8000/api/services/payment-api/health`
5. Logs: `curl "http://localhost:8000/api/services/payment-api/logs?level=ERROR&level=WARN"`
6. Deployments: `curl http://localhost:8000/api/services/payment-api/deployments`
7. Observe the chain:

   ```text
   deployment v1.8.2  →  database connection errors  →  POST /payment 500s  →  degraded health
   ```

8. **Investigate** (Phase 5; one Gemini call):

   ```bash
   curl -X POST http://localhost:8000/api/incidents/1/investigate
   curl http://localhost:8000/api/incidents/1/events
   ```

   The Investigation Agent collects bounded evidence with read-only tools and returns findings
   that cite it (observations vs. hypotheses), with `next_step: "root_cause_analysis"`. The
   incident moves to `INVESTIGATING` and stays there; nothing is rolled back. Investigating
   again returns the stored result without another AI call.

9. **Analyze** (Phase 6; one Gemini call):

   ```bash
   curl -X POST http://localhost:8000/api/incidents/1/analyze
   curl http://localhost:8000/api/incidents/1/analysis
   ```

   The RCA Agent correlates the stored evidence into the most likely root cause (the v1.8.2
   database configuration change), with a time-ordered causal chain, ruled-out alternatives
   (database failure, resource exhaustion), confidence and missing evidence. Every claim cites
   evidence ids that the backend validates. The incident moves to `ANALYZING`; nothing is rolled
   back yet (that is Phase 7, with human approval).

10. **Propose remediation** (Phase 7; one Gemini call):

    ```bash
    curl -X POST http://localhost:8000/api/incidents/1/remediate
    curl http://localhost:8000/api/incidents/1/remediation
    ```

    The Remediation Agent proposes `ROLLBACK_DEPLOYMENT` of payment-api from v1.8.2 to v1.8.1
    (v1.8.1 comes from the deployment records, not from the model's imagination). The backend
    sets risk MEDIUM, requires human approval, stores a `PENDING` approval with the exact
    parameters, and moves the incident to `AWAITING_APPROVAL`. **Nothing is rolled back yet:**
    that only happens after a human approves (next step).

11. **Approve** (Phase 8; no AI call):

    ```bash
    curl -X POST http://localhost:8000/api/incidents/1/approve     # or .../reject
    curl http://localhost:8000/api/incidents/1/execution
    ```

    The backend re-validates the approved parameters and executes the simulated rollback:
    v1.8.2 becomes `ROLLED_BACK`, v1.8.1 is active again, payment-api goes from DEGRADED
    (37%, 2800 ms) to HEALTHY (0.8%, 180 ms), and the incident moves to `VERIFYING`.
    Rejecting instead escalates the incident and changes nothing. Verification (next step)
    confirms the recovery and resolves the incident.

12. **Verify** (Phase 9; one Gemini call for the explanation):

    ```bash
    curl -X POST http://localhost:8000/api/incidents/1/verify
    curl http://localhost:8000/api/incidents/1/verification
    ```

    The backend checks current telemetry: v1.8.1 active, payment-api HEALTHY, error rate 0.8%
    (< 5%), latency 180 ms (< 500 ms), telemetry recorded after the rollback. All six checks
    pass, so the incident is `RESOLVED`. If any check failed it would be `FAILED` instead:
    the AI explains the outcome but never decides it.

Or do all of it from Swagger UI at http://localhost:8000/docs.

Simulation and investigation are separate calls on purpose: the presenter controls when each
agent runs, and simulation never spends AI quota (CI/CD ingestion never calls AI either). The
dashboard has one button per step, shown only when the backend state allows it.

## Repeating the demo

- Calling **simulate** again while the incident is still active returns the same incident (HTTP
  200). It never creates duplicates.
- After an incident reaches `RESOLVED`, `FAILED` or `ESCALATED`, **simulate** starts a fresh
  incident. The simulated services' telemetry is rebuilt around the new time, and earlier
  incidents are kept as history.
- **Reset** returns everything to the healthy baseline and clears all incidents. The next incident
  is INC-001 again. It also deletes the demo repository's CI/CD events; events from a real
  configured repository are kept.

> The Supabase database is shared by the team: a reset clears everyone's demo incidents.
