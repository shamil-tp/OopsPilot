"""`POST /api/webhooks/github`: GitHub deliveries in, normalized CI/CD telemetry out.

Order of checks (nothing from the payload is trusted before the signature is verified):
    secret configured (503) -> size limit (413) -> HMAC SHA-256 over the raw body (401)
    -> event/delivery headers (400) -> JSON object (400) -> normalize + store (422 if malformed)

Responses: 201 recorded, 200 duplicate delivery (idempotent) or ping, 202 ignored (unsupported
event or repository). Processing is deterministic: no AI, no GitHub API, no commands. A push
to a monitored project's default branch schedules a background code review
(app.agents.code_review) that runs after the response.
"""

import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import code_review
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.github.normalize import SUPPORTED_EVENTS, PayloadError
from app.github.security import SignatureError, verify_signature
from app.schemas.cicd import CicdEventRead, WebhookResponse, WebhookStatus
from app.schemas.common import ErrorResponse
from app.services import cicd

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
_EVENT = re.compile(r"[a-z_]{1,32}")
_DELIVERY = re.compile(r"[0-9A-Za-z-]{1,64}")


async def _read_body(request: Request) -> bytes:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_PAYLOAD_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large")
    return bytes(body)


def _error(code: int, detail: str) -> dict[int | str, dict[str, object]]:
    return {code: {"model": ErrorResponse, "description": detail}}


@router.post(
    "/github",
    response_model=WebhookResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Receive a GitHub webhook delivery",
    responses={
        200: {"model": WebhookResponse, "description": "Duplicate delivery, or ping"},
        202: {"model": WebhookResponse, "description": "Ignored (unsupported event/repository)"},
        **_error(400, "Missing/invalid GitHub headers or body is not a JSON object"),
        **_error(401, "Missing, malformed or invalid X-Hub-Signature-256"),
        **_error(413, f"Body larger than {MAX_PAYLOAD_BYTES} bytes"),
        **_error(422, "Payload does not match the declared event"),
        **_error(503, "GITHUB_WEBHOOK_SECRET is not configured"),
    },
)
async def github_webhook(
    request: Request,
    response: Response,
    db: DbSession,
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> WebhookResponse:
    secret = get_settings().github_webhook_secret
    if secret is None or not secret.get_secret_value():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub webhook is not configured on this server"
        )
    body = await _read_body(request)
    try:
        verify_signature(secret.get_secret_value(), body, x_hub_signature_256)
    except SignatureError as exc:
        logger.warning(
            "github_signature_rejected",
            extra={"reason": str(exc), "delivery_id": (x_github_delivery or "")[:64]},
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None

    if not x_github_event or not _EVENT.fullmatch(x_github_event):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing or invalid X-GitHub-Event")
    if not x_github_delivery or not _DELIVERY.fullmatch(x_github_delivery):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing or invalid X-GitHub-Delivery")
    event_type, delivery_id = x_github_event, x_github_delivery
    logger.info("github_webhook_received", extra={"delivery_id": delivery_id, "event": event_type})

    if event_type == "ping":
        response.status_code = status.HTTP_200_OK
        return WebhookResponse(status="pong", delivery_id=delivery_id, event_type=event_type)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not valid JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must be a JSON object")

    try:
        result = await cicd.ingest(db, event_type, delivery_id, payload)
    except PayloadError as exc:
        logger.warning("github_payload_invalid", extra={"delivery_id": delivery_id})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None

    if (
        result.status == "recorded"
        and result.event is not None
        and code_review.eligible(result.event)
    ):
        # The AI review runs after this response: webhook processing itself stays deterministic.
        code_review.schedule(result.event.id)

    response.status_code = {
        "recorded": status.HTTP_201_CREATED,
        "duplicate": status.HTTP_200_OK,
        "ignored": status.HTTP_202_ACCEPTED,
    }[result.status]
    return WebhookResponse(
        status=result.status,
        delivery_id=delivery_id,
        event_type=event_type,
        reason=result.reason,
        event=CicdEventRead.model_validate(result.event) if result.event else None,
    )


@router.get(
    "/github/status",
    response_model=WebhookStatus,
    summary="GitHub webhook configuration (non-sensitive)",
)
async def github_webhook_status() -> WebhookStatus:
    settings = get_settings()
    secret = settings.github_webhook_secret
    return WebhookStatus(
        configured=bool(secret and secret.get_secret_value()),
        repository=settings.github_repository,
        service=settings.github_service or settings.monitored_service,
        supported_events=["ping", *SUPPORTED_EVENTS],
        max_payload_bytes=MAX_PAYLOAD_BYTES,
    )
