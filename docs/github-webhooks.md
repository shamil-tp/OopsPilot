# GitHub Webhooks & CI/CD Telemetry

GitHub is an **event source**, never an execution authority. OpsPilot receives webhook
deliveries, verifies them, normalizes them into CI/CD telemetry, and lets the Investigation Agent
read that telemetry as evidence. Nothing in this path calls GitHub, runs commands, or uses AI.

```text
GitHub ──webhook──▶ POST /api/webhooks/github
                     1. GITHUB_WEBHOOK_SECRET configured?          (503 if not)
                     2. body ≤ 2 MiB                               (413)
                     3. HMAC SHA-256 of the RAW body == X-Hub-Signature-256 (constant time; 401)
                     4. X-GitHub-Event / X-GitHub-Delivery valid   (400)
                     5. JSON object, shape matches the event       (400 / 422)
                     6. normalize (deterministic) ─▶ cicd_events    (201; duplicate delivery → 200)
                     7. successful production deploy ─▶ deployments (link or create)
                     8. active incident on the service ─▶ agent_events ─▶ WS /ws/incidents/{id}
                                                   │
Investigation Agent ◀── get_recent_cicd_events ────┘  (read-only, bounded; evidence ids C1, C2, …)
        ▼
RCA Agent correlates deployment timing with failures ─▶ human-approved remediation ─▶ report
```

## Supported events

| `X-GitHub-Event` | Stored as | Notes |
| --- | --- | --- |
| `ping` | nothing | `200 {"status": "pong"}` (sent by GitHub when the webhook is created) |
| `push` | `COMMIT` | branch or tag, head commit SHA, first line of the message, author login, up to 20 changed paths; a push to a monitored project's default branch also starts an AI code review ([api.md](api.md#code-reviews)) |
| `workflow_run` | `DEPLOYMENT`, `TEST` or `BUILD` | name/path `deploy\|release\|rollout` → DEPLOYMENT; `test` → TEST; otherwise BUILD |
| `deployment_status` | `DEPLOYMENT` | GitHub Deployments API; `deployment.payload.service` / `.version` are honored |
| anything else | nothing | `202 {"status": "ignored"}` |

Status is normalized to `QUEUED` / `IN_PROGRESS` / `COMPLETED`; conclusions to `SUCCESS`,
`FAILURE` (incl. `startup_failure`), `CANCELLED`, `TIMED_OUT`, `NEUTRAL`, `SKIPPED`, `OTHER`.
Missing optional fields are stored as `null`.

## What is (not) stored

`cicd_events` keeps only normalized columns (repository, branch, commit SHA, clipped commit
message, actor login, workflow name/run id/number, status, conclusion, service, environment,
version, timestamps, a `github.com` run URL) plus a small sanitized `metadata` (tag, before SHA,
changed-file count and ≤ 20 paths, run attempt). Never stored: the raw payload, request headers
(e.g. `Authorization`), e-mail addresses, installation tokens, or anything else GitHub sends.
Control characters are stripped and every string is length-limited.

Payload values are data only: they are validated by Pydantic, stored through SQLAlchemy bound
parameters, rendered as escaped text by React, and passed to the model inside evidence lines that
the prompt marks as quoted data. They are never executed or used to build SQL, shell commands,
URLs or file paths.

## Deployment telemetry and version resolution

Only a **successful production deployment** becomes a row in `deployments` (a `workflow_run`
whose name/path says `prod`/`production`, e.g. `deploy-production`, or a `deployment_status` with
environment `production`). Builds, tests, failed or cancelled deploys, and staging/preview
deploys stay CI/CD evidence only. A CI/CD failure never creates an incident.

- **Correlation:** the event links to an existing deployment of the same service whose commit SHA
  matches (short or full) within ±1 h, otherwise it creates one (`SUCCEEDED`).
- **Version, never invented:** (1) `deployment.payload.version`, (2) a release tag
  (`refs/tags/v1.8.2`, a tag-triggered workflow, or a previously received tag push for the same
  commit), otherwise `version` stays `null` and the commit SHA is kept. A created deployment then
  uses the short SHA as its version label (the column is required); it is never a made-up semver.
- **Service:** `deployment.payload.service`, else a known service named in the workflow
  name/path (e.g. `deploy-auth-api`), else `GITHUB_SERVICE` for `GITHUB_REPOSITORY`.

## Idempotency and concurrency

- `delivery_id` (`X-GitHub-Delivery`) is **unique** in the database. GitHub redeliveries reuse it:
  the second delivery returns `200 {"status": "duplicate"}` with the stored event and writes
  nothing (no event, deployment or timeline entry). Concurrent duplicates are resolved by the
  constraint inside the transaction.
- `deployment_key` (one per workflow run attempt / GitHub deployment) is **unique**, so two
  different deliveries for the same run never create two deployments; the later one links to it.

## Configuration

| Variable | Purpose |
| --- | --- |
| `GITHUB_WEBHOOK_SECRET` | Required to accept deliveries. Unset → every delivery gets `503` |
| `GITHUB_REPOSITORY` | Optional `owner/repo`; other repositories are ignored (`202`) |
| `GITHUB_SERVICE` | Service that `GITHUB_REPOSITORY` deploys (default `payment-api`) |

`GET /api/webhooks/github/status` shows whether a secret is configured, the repository, service,
supported events and size limit — never the secret or anything derived from it. No GitHub token
is needed: the integration is webhook-only.

## GitHub setup

1. Repository → **Settings → Webhooks → Add webhook**.
2. Payload URL: `https://YOUR_DOMAIN/api/webhooks/github` (for local testing, a tunnel such as
   `ngrok http 8000`).
3. Content type: `application/json`.
4. Secret: the value of `GITHUB_WEBHOOK_SECRET` (generate with
   `python -c "import secrets; print(secrets.token_hex(32))"`; never commit it).
5. Events: **Let me select individual events** → *Pushes*, *Workflow runs* (optionally
   *Deployment statuses*).
6. Save. GitHub sends a `ping`; *Recent Deliveries* should show `200`.

Name the production deploy workflow so it contains `deploy`/`release` and `prod`/`production`
(e.g. `deploy-production`), and push a release tag (`v1.9.0`) for the deployed commit so the
version is resolved.

## Local testing (no GitHub needed)

```bash
# backend/.env or repo .env: GITHUB_WEBHOOK_SECRET=<any local value>
cd backend
uvicorn app.main:app --port 8000
python -m scripts.send_github_webhook                     # push, tag v1.8.2, deploy-production SUCCESS
python -m scripts.send_github_webhook --scenario failure  # failed deploy-production run
python -m scripts.send_github_webhook --scenario ping
curl "http://localhost:8000/api/cicd/events?service=payment-api"
curl "http://localhost:8000/api/services/payment-api/deployments"
```

The script signs each body exactly like GitHub (`X-Hub-Signature-256: sha256=<hmac>`) and sends
`X-GitHub-Event` and `X-GitHub-Delivery`. A manual equivalent:

```bash
BODY='{"zen":"hi","hook_id":1}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$GITHUB_WEBHOOK_SECRET" | sed 's/^.* //')"
curl -X POST http://localhost:8000/api/webhooks/github -H "Content-Type: application/json" \
  -H "X-GitHub-Event: ping" -H "X-GitHub-Delivery: local-1" -H "X-Hub-Signature-256: $SIG" -d "$BODY"
```

## Demo mode

The demo never depends on GitHub. **Simulate Incident** replays three deterministic deliveries of
the demo repository `opspilot-demo/payment-api` (`app/github/demo.py`) through the same ingestion
service as the endpoint: push to `main` (T-240 s), tag `v1.8.2` (T-210 s), and
`deploy-production #57` running T-180 s → T-135 s with `SUCCESS`, which links to the simulated
v1.8.2 deployment. **Reset** deletes the demo repository's events with the rest of the simulated
telemetry; events from a real configured repository are kept.
