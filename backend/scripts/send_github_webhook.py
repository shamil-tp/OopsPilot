"""Send signed, GitHub-shaped webhook deliveries to a running OpsPilot backend.

    cd backend
    python -m scripts.send_github_webhook                    # demo story: push, tag, deploy
    python -m scripts.send_github_webhook --scenario failure # a failed deploy-production run
    python -m scripts.send_github_webhook --scenario ping

The secret is read from GITHUB_WEBHOOK_SECRET (environment or .env) and must match the backend's.
Each delivery is signed exactly like GitHub does (HMAC SHA-256 over the raw body, header
X-Hub-Signature-256) and carries X-GitHub-Event / X-GitHub-Delivery. The demo deliveries are
timed relative to now (the deploy completes 2 minutes ago). Sending the same scenario twice in the
same second reuses the delivery ids, so the second run shows the idempotent `duplicate` response.
Only local HTTP to --url; nothing talks to GitHub.
"""

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import get_settings
from app.github.demo import DEMO_REPOSITORY, RELEASE_SHA, Delivery, demo_deliveries
from app.github.security import sign


def _failure(now: datetime) -> list[Delivery]:
    run_id = 9100000058
    return [
        (
            "workflow_run",
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"opspilot-demo-failure:{now.isoformat()}")),
            {
                "action": "completed",
                "workflow_run": {
                    "id": run_id,
                    "name": "deploy-production",
                    "path": ".github/workflows/deploy-production.yml",
                    "run_number": 58,
                    "run_attempt": 1,
                    "head_branch": "main",
                    "head_sha": RELEASE_SHA,
                    "status": "completed",
                    "conclusion": "failure",
                    "run_started_at": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "html_url": f"https://github.com/{DEMO_REPOSITORY}/actions/runs/{run_id}",
                    "actor": {"login": "release-bot"},
                },
                "repository": {"full_name": DEMO_REPOSITORY},
            },
        )
    ]


def _ping(now: datetime) -> list[Delivery]:
    return [("ping", str(uuid.uuid4()), {"zen": "Design for failure.", "hook_id": 1})]


SCENARIOS = {
    # Anchored so the demo deploy completes two minutes ago (T0 = now + 15 s).
    "demo": lambda now: demo_deliveries(now + timedelta(seconds=15)),
    "failure": _failure,
    "ping": _ping,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000/api/webhooks/github")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="demo")
    args = parser.parse_args()

    secret = get_settings().github_webhook_secret
    if secret is None or not secret.get_secret_value():
        print("GITHUB_WEBHOOK_SECRET is not set (environment or .env).", file=sys.stderr)
        return 2

    now = datetime.now(UTC).replace(microsecond=0)
    with httpx.Client(timeout=10) as client:
        for event_type, delivery_id, payload in SCENARIOS[args.scenario](now):
            body = json.dumps(payload).encode()
            response = client.post(
                args.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "GitHub-Hookshot/opspilot-local",
                    "X-GitHub-Event": event_type,
                    "X-GitHub-Delivery": delivery_id,
                    "X-Hub-Signature-256": sign(secret.get_secret_value(), body),
                },
            )
            result: dict[str, Any] = response.json() if response.content else {}
            event = result.get("event") or {}
            print(
                f"{event_type:<13} {delivery_id}  HTTP {response.status_code}  "
                f"{result.get('status', result.get('detail'))}"
                + (
                    f"  -> {event.get('category')} {event.get('conclusion') or event.get('status')}"
                    f" version={event.get('version')} deployment_id={event.get('deployment_id')}"
                    if event
                    else ""
                )
            )
            if response.status_code >= 400:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
