"""Deterministic GitHub deliveries that tell the demo incident's CI/CD story.

With T0 = incident detection (see app.services.scenario):

    T-240  push to main: commit e4a7c52 "Release payment-api v1.8.2"      (11:40)
    T-210  push of tag v1.8.2 for the same commit
    T-180  workflow "deploy-production" #57 starts                         (11:41)
    T-135  ... completes: SUCCESS (version v1.8.2 resolved from the tag)
    T-120  (telemetry) database connection failures begin                  (11:42)

They are real GitHub-shaped payloads, used two ways:
- `simulate_incident` replays them through the same ingestion service as the webhook endpoint,
  so the demo shows CI/CD evidence without any GitHub setup;
- `scripts/send_github_webhook.py` signs and POSTs them to /api/webhooks/github, exercising the
  real HTTP + signature path (and a real repository's webhook uses the same endpoint).
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from app.schemas.common import as_utc

DEMO_REPOSITORY = "opspilot-demo/payment-api"
# The v1.8.2 commit of the simulated scenario (its deployments store the short form, e4a7c52).
RELEASE_SHA = "e4a7c52d9b1f3a6c8e0f2b4d6a8c1e3f5b7d9a0c"
PREVIOUS_SHA = "8f2d6b1c3e5a7092b4d6f8a0c2e4b6d8f0a2c4e6"
DEPLOY_RUN_ID = 9100000057

Delivery = tuple[str, str, dict[str, Any]]  # (event type, delivery id, payload)


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def demo_deliveries(anchor: datetime) -> list[Delivery]:
    anchor = as_utc(anchor).replace(microsecond=0)  # same delivery ids however it was loaded

    def at(offset: int) -> datetime:
        return anchor + timedelta(seconds=offset)

    def delivery_id(n: int) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opspilot-demo:{anchor.isoformat()}:{n}"))

    repository = {"full_name": DEMO_REPOSITORY, "name": "payment-api", "private": True}
    head_commit = {
        "id": RELEASE_SHA,
        "message": "Release payment-api v1.8.2\n\nConnection pool and configuration updates.",
        "timestamp": _iso(at(-250)),
        "author": {"name": "Dev Alice", "username": "dev-alice"},
        "added": [],
        "removed": [],
        "modified": ["config/production.yaml", "src/payments/db.py"],
    }
    push_main = {
        "ref": "refs/heads/main",
        "before": PREVIOUS_SHA,
        "after": RELEASE_SHA,
        "created": False,
        "deleted": False,
        "repository": repository | {"pushed_at": int(at(-240).timestamp())},
        "pusher": {"name": "dev-alice"},
        "sender": {"login": "dev-alice"},
        "head_commit": head_commit,
        "commits": [head_commit],
    }
    push_tag = {
        "ref": "refs/tags/v1.8.2",
        "before": "0" * 40,
        "after": RELEASE_SHA,
        "created": True,
        "deleted": False,
        "repository": repository | {"pushed_at": int(at(-210).timestamp())},
        "pusher": {"name": "dev-alice"},
        "sender": {"login": "dev-alice"},
        "head_commit": head_commit,
        "commits": [],
    }
    workflow_run = {
        "action": "completed",
        "workflow_run": {
            "id": DEPLOY_RUN_ID,
            "name": "deploy-production",
            "path": ".github/workflows/deploy-production.yml",
            "run_number": 57,
            "run_attempt": 1,
            "event": "push",
            "head_branch": "main",
            "head_sha": RELEASE_SHA,
            "display_title": "Release payment-api v1.8.2",
            "status": "completed",
            "conclusion": "success",
            "created_at": _iso(at(-190)),
            "run_started_at": _iso(at(-180)),
            "updated_at": _iso(at(-135)),
            "html_url": f"https://github.com/{DEMO_REPOSITORY}/actions/runs/{DEPLOY_RUN_ID}",
            "actor": {"login": "release-bot"},
        },
        "repository": repository,
        "sender": {"login": "release-bot"},
    }
    return [
        ("push", delivery_id(1), push_main),
        ("push", delivery_id(2), push_tag),
        ("workflow_run", delivery_id(3), workflow_run),
    ]
