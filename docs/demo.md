# OpsPilot Demo

## The scenario

A deterministic payment-api incident. Every run tells the same story, anchored to the moment you
trigger it (shown here as if the incident is detected at 11:44):

```text
6 days ago   payment-api v1.8.0 deployed
2 days ago   payment-api v1.8.1 deployed            (healthy since)
11:34-11:40  normal traffic: payments succeed, /health 200, a few unrelated warnings
11:41        payment-api v1.8.2 deployment starts
             configuration reloaded (changed keys: database.host, database.pool_size)
             deployment completes
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
deployments, and health snapshots. The remediation (Phase 7) will be a rollback v1.8.2 → v1.8.1.

## Running it (backend API)

1. Start the backend with Supabase configured (`DATABASE_URL` in `.env`, schema at revision 0003):

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

Simulation and investigation are separate calls on purpose: the demo controls when the agent
runs, and simulation never spends AI quota. The dashboard (Phase 11) will chain them for the
"Simulate Incident" button.

## Repeating the demo

- Calling **simulate** again while the incident is still active returns the same incident (HTTP
  200). It never creates duplicates.
- After an incident reaches `RESOLVED`, `FAILED` or `ESCALATED`, **simulate** starts a fresh
  incident. The simulated services' telemetry is rebuilt around the new time, and earlier
  incidents are kept as history.
- **Reset** returns everything to the healthy baseline and clears all incidents. The next incident
  is INC-001 again.

> The Supabase database is shared by the team: a reset clears everyone's demo incidents.
