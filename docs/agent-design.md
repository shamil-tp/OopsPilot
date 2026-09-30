# OpsPilot Agent Design

> Implemented: **Investigation Agent** (Phase 5), **Root Cause Analysis Agent** (Phase 6),
> **Remediation Agent** (Phase 7), **human approval gate + simulated execution** (Phase 8),
> **Verification Agent** (Phase 9). Planned: incident report and live dashboard (Phase 10).

```text
DETECTED --investigate--> INVESTIGATING --analyze--> ANALYZING --remediate--> AWAITING_APPROVAL
            Investigation Agent            RCA Agent             Remediation Agent
            collects evidence              most likely cause     proposes; approval PENDING

AWAITING_APPROVAL --approve--> REMEDIATING --(backend executes, simulated)--> VERIFYING --verify--> RESOLVED
                  --reject---> ESCALATED   (nothing executed)                          --verify--> FAILED (not recovered)
```

## Principles

- **The AI proposes, the backend decides.** Agents read evidence and produce structured output.
  They never run actions: tools are allowlisted per agent and gated by permission.
- **The backend picks the evidence.** Telemetry is filtered deterministically before any model
  call, so the prompt is small, cheap and identical for identical data.
- **Findings must cite evidence.** Every evidence item has an id; findings that cite ids the
  backend did not supply are dropped before anything is stored.
- **One structured call per agent step.** No model-driven tool loops; bounded tool calls
  (`MAX_AGENT_STEPS=8`), tool retries (`TOOL_MAX_RETRIES=2`) and LLM retries (`LLM_MAX_RETRIES=2`).
- **Provider-independent.** Agents use `app.ai` (`AIProvider.generate_structured`), never an SDK.

## Tools and permissions (`backend/app/tools/`)

| Tool | Permission | Investigation agent |
| --- | --- | --- |
| `get_application_logs` | READ_ONLY | allowed (window ≤ 60 min, ≤ 50 entries) |
| `get_service_health` | READ_ONLY | allowed (latest, optionally as of a time / with a status) |
| `get_recent_deployments` | READ_ONLY | allowed (≤ 5) |
| `get_previous_incidents` | READ_ONLY | allowed (≤ 5) |
| `get_recent_cicd_events` | READ_ONLY | allowed (known service, window ≤ 24 h, ≤ 10); normalized `cicd_events` only, never GitHub |
| `rollback_deployment` | REQUIRES_HUMAN_APPROVAL | refused; executed only by the backend approval gate after a human approves |
| `restart_service` | REQUIRES_HUMAN_APPROVAL | refused (same) |

No agent's allowlist contains an action tool, and `ToolExecutor` refuses REQUIRES_HUMAN_APPROVAL
tools even if one were added. Proposing an action (Phase 7) and executing it (Phase 8, after
approval, by the backend) are separate code paths.

`ToolExecutor` enforces, in order: the tool exists, it is on the agent's allowlist, it is
READ_ONLY with a handler, and its arguments validate against a bounded Pydantic model (unknown
arguments are rejected). It caps the number of calls, caches identical calls, and retries
transient database errors. There is no SQL, shell, HTTP or filesystem tool.

## Investigation Agent (`backend/app/agents/investigation.py`)

```text
POST /api/incidents/{id}/investigate
  start_investigation   incident DETECTED -> INVESTIGATING (atomic), agent_run RUNNING, agent_started
  run_investigation
    EvidenceCollector   7 read-only tool calls -> 17 evidence items          tool_started/completed
                                                                           evidence_found (x4)
    build_prompt        ~700 tokens: rules + evidence lines                investigation_analysis_started
    AIProvider          1 x generate_structured(InvestigationAnalysis)
    guardrails          drop findings with unknown evidence ids
    store               agent_run COMPLETED + output JSON                 investigation_completed
  incident stays INVESTIGATING (next step: POST /api/incidents/{id}/analyze)
```

### Evidence collection (`backend/app/agents/evidence.py`)

For an incident detected at T0 on service S:

| Source | What is collected | Bound |
| --- | --- | --- |
| Logs | S's logs in T0 ± 10 min (one query); ERROR/WARN collapsed into (level, message) groups with counts; plus INFO lines in the first 60 s after an in-window deployment (e.g. config reloads) | ≤ 15 groups + ≤ 5 change lines |
| Health | S now, S's last HEALTHY snapshot before T0 (baseline), and S's dependencies now | 2 + dependencies |
| Deployments | S's deployments at or before T0 | ≤ 3 |
| Previous incidents | S's earlier incidents (with recorded root cause, if any) | ≤ 3 |
| CI/CD (`C<n>`) | S's GitHub pushes, workflow runs and deployments in the 2 h before T0 (commit, workflow, status/conclusion, version and its source, whether it became a deployment); commit messages are marked as quoted data | ≤ 5 |

Each item is rendered as one line with a citation id and a time relative to detection. For the
demo incident the model receives:

```text
L2 16:00:25 (T-180s) INFO Deployment v1.8.2 started [version=v1.8.2, previous_version=v1.8.1, ...]
L3 16:00:45 (T-160s) INFO Application configuration reloaded [changed_keys=['database.host', 'database.pool_size']]
L5 16:01:25 (T-120s) ERROR Database connection failed (x2, last at T-40s) [host=payments-db.internal, port=5433]
L8 16:01:37 (T-108s) ERROR POST /payment 500 (x6, last at T-10s) [status=500, duration_ms=5012]
H1 payment-api DEGRADED at 16:03:25 (T+0s, current): error_rate 37%, latency 2800 ms, cpu 43%, memory 68%
H3 database HEALTHY at 16:03:25 (T+0s, dependency of payment-api): error_rate 0%, latency 4 ms, ...
D1 payment-api v1.8.2 deployed 2026-09-30 16:00:25 (T-180s), SUCCEEDED, commit e4a7c52
...
```

### Output

The model returns `InvestigationAnalysis` (`summary`, `findings[{kind, statement, evidence_ids}]`,
`confidence`, `next_step`), validated by the provider. `kind` separates **observations** (shown
by the evidence) from **hypotheses** (to be tested by RCA). The stored `InvestigationResult`
adds the deterministic evidence, related deployments and previous incidents, the model id, and
`status: "investigation_complete"`; `next_step` is `root_cause_analysis` or
`collect_more_evidence`, never a remediation.

Example (live run, `gemini-3.1-flash-lite`, confidence 0.9):

- observation: Deployment v1.8.2 was completed shortly before the onset of database connection errors. `[L2, L4, D1]`
- observation: The configuration was reloaded with changes to database host and pool size. `[L3]`
- observation: The service is experiencing database connection failures and pool exhaustion. `[L5, L6, L7]`
- hypothesis: The database configuration changes in v1.8.2 are causing the connectivity issues. `[L3, L5, L7]`
- observation: The database itself is healthy, suggesting the issue is specific to payment-api. `[H3]`

### Failure handling (investigation)

| Failure | HTTP | Effect |
| --- | --- | --- |
| Incident not found | 404 | nothing changes |
| Investigation running, or incident not DETECTED | 409 | nothing changes |
| Already completed | 200 | existing result returned, no AI call |
| AI error (rate limit, timeout, invalid/unsupported output) | 502 | run FAILED, `error` event, incident back to DETECTED (retryable) |
| AI not configured | 503 | same |
| Telemetry/database failure | 503 | same |
| Unexpected error | 500 | same; generic message only |

The agent holds a plain `IncidentContext` snapshot instead of ORM objects, because a retried tool
rolls the session back (expiring ORM objects). The run executes under `asyncio.shield` in its own
session, so a client disconnect cannot leave it stuck in RUNNING.

## Root Cause Analysis Agent (`backend/app/agents/root_cause.py`)

Investigation **collects and describes** evidence; RCA **correlates** it: it orders events in
time, relates the change right before the first error to what failed, checks dependency health
and resource metrics, weighs competing explanations, and names the single most likely cause
with its uncertainty. It never re-collects telemetry and has no tools at all.

```text
POST /api/incidents/{id}/analyze          (requires a COMPLETED investigation)
  start_analysis   incident INVESTIGATING -> ANALYZING (atomic), agent_run RUNNING    agent_started
  run_analysis
    load the stored InvestigationResult (17 evidence items + findings)              evidence_evaluated
    build_prompt   ~800 tokens: rules + dependencies + findings + ONE time-ordered  root_cause_analysis_started
                   evidence timeline (merged across logs/health/deployments)
    AIProvider     1 x generate_structured(RootCauseAnalysis), temperature 0
    guardrails     validate every citation against the stored evidence
    store          agent_run COMPLETED + RootCauseResult                              root_cause_identified
  incident stays ANALYZING (remediation + approval is the next phase)
```

### Result (`RootCauseResult`)

| Field | Meaning |
| --- | --- |
| `root_cause` | One sentence: the most likely technical cause |
| `category` | `deployment_regression`, `configuration_error`, `database_failure`, `network_connectivity`, `resource_exhaustion`, `external_dependency`, `transient`, `unknown` |
| `confidence` | 0–1 (validated) |
| `supporting_evidence` | The cited evidence items, resolved to the stored facts |
| `causal_chain` | Key events in time order, each with citations |
| `contributing_factors` | Secondary conditions, with citations |
| `alternative_explanations` | Competing explanations: `less_likely`, `ruled_out` (only with evidence) or `not_assessable` |
| `missing_evidence` | What would raise confidence |
| `reasoning_summary` | How the evidence connects (≤ 80 words) |
| `recommended_next_step` | `propose_remediation`, `collect_more_evidence` or `escalate_to_human`, a phase choice, never an action |

### Guardrails (backend-enforced)

- Citations are checked against the evidence stored by the investigation. Unknown ids are removed;
  a causal-chain step or contributing factor left without citations is dropped; an alternative
  that cites only unknown ids is dropped.
- An alternative can be `ruled_out` only with real evidence; otherwise it becomes `less_likely`.
- A root cause with no valid supporting evidence fails the run (502) instead of being stored.
- The output schema has no action/tool/command field: the model can only name the next phase.

Live example (`gemini-3.1-flash-lite`, confidence 0.9, all 18 citations valid):

- root cause: *The deployment of v1.8.2 introduced incorrect database configuration settings,
  specifically the host and pool size, leading to connection failures and pool exhaustion.*
- causal chain: v1.8.2 applied new database config `[L2, L3, L4]` → connection failures and
  timeouts `[L5, L6]` → pool exhausted, error rate and latency up `[L7, L8, L9, L10]`
- ruled out: database service failure (database healthy) `[H3]`; resource exhaustion (CPU and
  memory unchanged from the healthy baseline) `[H1, H2]`
- missing evidence: the applied config values; database-side logs of the rejected connections

### Failure handling

Same pattern as the investigation (`app/agents/common.py`): 404 unknown incident; 409 no
completed investigation, analysis already running, or incident not `INVESTIGATING`; 200 with the
stored result if already completed (no AI call); 502 AI failure or unsupported output; 503 AI not
configured or database unavailable; 500 unexpected (generic message). On failure the run is
FAILED, an `error` event is stored and the incident returns to `INVESTIGATING` for a retry.

## Remediation Agent (`backend/app/agents/remediation.py`)

The model **proposes** one action; the **backend decides** everything else and executes nothing.

```text
POST /api/incidents/{id}/remediate        (requires a COMPLETED RCA; incident ANALYZING)
  start_remediation  row-lock the incident, re-check for a running/completed proposal,
                     agent_run RUNNING                                          agent_started
  run_remediation
    load the stored RCA + only the evidence it cited + deployment records       remediation_analysis_started
    rollback candidates from the deployments table (older SUCCEEDED versions)
    AIProvider       1 x generate_structured(RemediationProposal), temperature 0
    backend policy   action / target / version / citations / risk / approval
    one transaction  approval PENDING + incident AWAITING_APPROVAL             remediation_recommended
                     (or ESCALATED, or unchanged for NO_ACTION)                  approval_required
  nothing is executed here; see "Human approval and execution" below
```

### Supported actions and backend policy (`app/services/remediation_policy.py`)

| Action | Risk (backend) | Approval (backend) | Executes | Incident after proposal |
| --- | --- | --- | --- | --- |
| `ROLLBACK_DEPLOYMENT` | MEDIUM (reversible, CLAUDE.md §7) | required: `rollback_deployment` is REQUIRES_HUMAN_APPROVAL | Phase 8, after approval | `AWAITING_APPROVAL` |
| `RESTART_SERVICE` | MEDIUM | required: `restart_service` is REQUIRES_HUMAN_APPROVAL | Phase 8, after approval | `AWAITING_APPROVAL` |
| `NO_ACTION` | LOW | none | never | stays `ANALYZING` |
| `ESCALATE_TO_HUMAN` | LOW | none | never | `ESCALATED` |

- The model's schema (`RemediationProposal`) has `action` (the four types only), `target_service`,
  `rollback_to_version`, `reason`, `supporting_evidence`, `confidence`, and **no** risk,
  approval or execution field. Risk and approval come from the table above; approval is derived
  from the tool permission registry, so it cannot drift from the executor's rules.
- **Rollback validation** against real deployment data: the target is the affected service; the
  version is not the active one; it is one of the service's older SUCCEEDED deployments (so an
  invented, failed, rolled-back or other-service version is rejected).
- **Restart validation**: the affected service or one of its catalogued dependencies only.
- **Citations**: checked against the evidence sent to the model; ROLLBACK/RESTART/NO_ACTION need
  at least one valid citation.
- A rejected proposal fails the run (502 "Proposal rejected by backend policy: ..."), creates no
  approval, and leaves the incident `ANALYZING` for a retry. It is never "fixed up" silently.

### Approval record

`approvals` stores `action_type`, `target` (the version being rolled back, as in CLAUDE.md, or the
service to restart), backend `risk`, `reason`, `status = PENDING` and **`parameters`** (migration
`0003`): the exact validated values a human will approve, e.g.
`{"service": "payment-api", "from_version": "v1.8.2", "to_version": "v1.8.1", "remediation_run_id": 8}`.
Phase 8 executes these stored parameters (re-validated), never values from a client or the model.
`POST /remediate` takes no body, and no approve/reject/execute endpoint exists yet.

Live example (`gemini-3.1-flash-lite`): `ROLLBACK_DEPLOYMENT` payment-api v1.8.2 → v1.8.1,
confidence 0.9, citing `[L3, L5, L7, L9, H1]`: *"Deployment v1.8.2 introduced incorrect database
configuration parameters, causing connection failures and pool exhaustion. Rolling back to v1.8.1
restores the last known stable configuration."* Approval PENDING (MEDIUM); nothing executed.

### Concurrency and failures

Start takes a row lock on the incident (a no-op conditional UPDATE) and re-checks for an existing
run, so two simultaneous requests produce one run, one AI call and one approval (the other gets
409). A test forces this race and fails if the lock is removed. Completed proposals are returned
as is (200, no AI call). Failures (AI, policy, database, unexpected) mark the run FAILED, store an
`error` event, create no approval, and leave the incident `ANALYZING`.

## Human approval and execution (`backend/app/agents/approval.py`, Phase 8)

Deterministic backend logic in the orchestrator role (CLAUDE.md §9, §18). **No AI call**: the
action, target and parameters are the ones Phase 7 validated and stored on the approval.

```text
POST /api/incidents/{id}/approve           (no request body)
  decide     approval PENDING -> APPROVED  (conditional UPDATE: one decision wins)
             incident AWAITING_APPROVAL -> REMEDIATING; execution run RUNNING  approval_received
  execute    lock the incident row (REMEDIATING); re-check the run is still RUNNING
             re-validate the stored parameters against live deployment data    remediation_started
             simulated rollback/restart; incident -> VERIFYING; run COMPLETED  remediation_completed
             (one transaction: all or nothing)
POST /api/incidents/{id}/reject            (no request body)
  decide     approval PENDING -> REJECTED; incident -> ESCALATED              approval_received,
             nothing executed                                                  incident_escalated
```

### Re-validation at execution time

The approval must be `APPROVED`, belong to the incident, and be an executable action
(`ROLLBACK_DEPLOYMENT`, `RESTART_SERVICE`). For a rollback the stored `from_version` must still be
the active deployment (otherwise the approval is **stale**), and `to_version` must still be an
older SUCCEEDED deployment of the same, affected service. Any violation: nothing is changed, the
execution run is FAILED, an `error` event is stored, the incident becomes `FAILED`, HTTP 409
"Execution refused: ...".

### Simulated effects (`app/services/simulator.py`, values in `app/services/scenario.py`)

| Action | Deployments | Logs | Health |
| --- | --- | --- | --- |
| Rollback v1.8.2 → v1.8.1 | v1.8.2 → `ROLLED_BACK`; v1.8.1 redeployed as a new SUCCEEDED record (same commit) | rollback started/completed, config reload, pool initialized, `POST /payment 200` | HEALTHY, 0.8% errors, 180 ms (CLAUDE.md §8/§26) |
| Restart | unchanged | restart started/restarted | unchanged: a restart does not fix a configuration, so no recovery is invented |

Only database writes on the simulated services: no shell, no network, no real infrastructure.

### Idempotency and concurrency

- Repeating the same decision returns the current state (200) and never executes again; the
  opposite decision is 409.
- Two simultaneous decisions: the conditional `UPDATE ... WHERE status = 'PENDING'` lets exactly one
  win; the other gets 409 "decided concurrently". Tested with the race window forced open.
- Two simultaneous executions of one run: the incident row lock serializes them; the second sees
  the incident is no longer `REMEDIATING` and changes nothing. Tested with the race forced, and
  the test fails if the lock is removed.
- Execution runs under `asyncio.shield` in its own session; a client disconnect cannot leave a
  half-applied rollback (all changes commit together or not at all).

## Verification Agent (`backend/app/agents/verification.py`, Phase 9)

The **backend decides** whether the service recovered; the model only explains that decision.

```text
POST /api/incidents/{id}/verify            (no body; incident VERIFYING, approved execution COMPLETED)
  start_verification  row-lock the incident, re-check for a running/completed run  verification_started
  run_verification
    read current telemetry: active deployment + latest health of the service
    6 deterministic checks (below)                                               recovery_check (x6)
    recovered = every check passed; confidence = share of checks passed
    AIProvider   1 x generate_structured(VerificationExplanation): summary + cited ids only
    guardrail    citations must be supplied ids (V1..V4); none valid -> run FAILED
    one transaction: incident RESOLVED (recovered) or FAILED (not), run COMPLETED  verification_completed
                                                                                  (+ incident_resolved)
```

| Check | Passes when | Evidence |
| --- | --- | --- |
| `remediation_executed` | the approval is APPROVED and its execution COMPLETED | V1 |
| `target_deployment_active` | (rollback only) the approved `to_version` is the active deployment | V4 |
| `service_healthy` | latest health is HEALTHY | V3 |
| `error_rate_recovered` | latest error rate < `ERROR_RATE_THRESHOLD` (5%, the scenario's alert threshold) | V2, V3 |
| `latency_recovered` | latest latency < `LATENCY_SLO_MS` (500 ms, the scenario's SLO) | V2, V3 |
| `telemetry_fresh` | the latest health was recorded after the remediation (no stale data) | V1, V3 |

Evidence is built by the backend: V1 the executed remediation, V2 the before snapshot (from the
execution record), V3 current health, V4 the active deployment. `VerificationExplanation` has no
`recovered`, confidence, metric or next-step field, so the model cannot change the outcome; a
test feeds it a contradicting explanation and the incident is still RESOLVED.

Not recovered (any check fails: still degraded, error rate or latency above threshold, wrong or
missing deployment, missing or stale telemetry): the run COMPLETES with `recovered: false`,
`failed_checks`, `next_step: human_investigation`, and the incident becomes `FAILED`. Nothing is
retried, rolled back or restarted. Example: an approved *restart* does not fix the v1.8.2
configuration, so verification honestly reports it as not recovered.

Failures of the check itself (AI error, invalid/unsupported output, database) mark the run
FAILED, store an `error` event and leave the incident `VERIFYING` so verification can be retried.
A completed verification is returned as is (200, no AI call). Two simultaneous requests produce
one run and one AI call (race forced in a test that fails without the lock).

Live example: 6/6 checks passed; before v1.8.2 DEGRADED 37% / 2800 ms, after v1.8.1 HEALTHY
0.8% / 180 ms; *"The payment-api service recovered following the rollback to v1.8.1 (V1, V4).
The error rate dropped from 37% to 0.8% (V2, V3), and latency improved from 2800 ms to 180 ms
(V2, V3). The service is now confirmed healthy (V3)."* Incident RESOLVED.
