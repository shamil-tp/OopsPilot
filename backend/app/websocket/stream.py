"""Live incident event stream: `WS /ws/incidents/{incident_id}` (CLAUDE.md §21).

The stream tails the committed rows of `agent_events` (and the incident's status) instead of
hooking into the agents, so:
- only committed events are ever sent (a rolled-back step never appears in the UI);
- the workflow is completely independent of any connection: agents run from REST requests, and a
  client connecting, lagging or disconnecting cannot affect them;
- it works the same with several backend processes.

Messages (JSON):
    {"type": "<event_type>", "id", "incident_id", "agent", "event_type", "message", "metadata",
     "timestamp"}                                    one per agent event, oldest first
    {"type": "incident_status", "incident_id", "incident_status"}   on connect and on change
    {"type": "error", "message"}                     safe, generic; the stream keeps retrying

On connect the client receives the full history, then new events as they are committed. A client
may reconnect with `?after=<last event id>` to resume without duplicates.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import AgentEvent, Incident
from app.schemas.events import AgentEventRead

logger = get_logger(__name__)
router = APIRouter()

POLL_INTERVAL_SECONDS = 1.0
MAX_BATCH = 200
NOT_FOUND_CLOSE_CODE = 4404


async def _read(
    session_factory: async_sessionmaker[AsyncSession], incident_id: int, after: int
) -> tuple[str | None, list[AgentEvent]]:
    async with session_factory() as db:
        status = await db.scalar(select(Incident.status).where(Incident.id == incident_id))
        rows = await db.scalars(
            select(AgentEvent)
            .where(AgentEvent.incident_id == incident_id, AgentEvent.id > after)
            .order_by(AgentEvent.id)
            .limit(MAX_BATCH)
        )
        return (status.value if status else None), list(rows)


def event_message(event: AgentEvent) -> dict[str, Any]:
    body = AgentEventRead.model_validate(event).model_dump(mode="json")
    return {"type": event.event_type, **body}


async def incident_messages(
    incident_id: int,
    *,
    after: int = 0,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield stream messages forever (until the consumer stops). Raises LookupError if the
    incident does not exist when the stream starts."""
    session_factory = session_factory or SessionLocal  # looked up at call time
    last_status: str | None = None
    last_id = after
    first = True
    while True:
        try:
            status, events = await _read(session_factory, incident_id, last_id)
        except (SQLAlchemyError, OSError) as exc:
            logger.warning("event_stream_read_failed", extra={"error": type(exc).__name__})
            yield {"type": "error", "message": "Event stream temporarily unavailable; retrying"}
            await asyncio.sleep(poll_interval * 3)
            continue
        if status is None:
            if first:
                raise LookupError(incident_id)
            yield {"type": "error", "message": "The incident no longer exists"}
            return
        first = False
        for event in events:
            last_id = event.id
            yield event_message(event)
        if status != last_status:
            last_status = status
            yield {"type": "incident_status", "incident_id": incident_id, "incident_status": status}
        if len(events) < MAX_BATCH:
            await asyncio.sleep(poll_interval)


@router.websocket("/ws/incidents/{incident_id}")
async def incident_stream(websocket: WebSocket, incident_id: int, after: int = 0) -> None:
    await websocket.accept()

    async def pump() -> None:
        try:
            async for message in incident_messages(incident_id, after=after):
                await websocket.send_json(message)
                if message["type"] == "error" and "no longer exists" in message["message"]:
                    await websocket.close()
                    return
        except LookupError:
            await websocket.send_json({"type": "error", "message": "Incident not found"})
            await websocket.close(code=NOT_FOUND_CLOSE_CODE)
        except (WebSocketDisconnect, RuntimeError):
            pass  # the client went away mid-send; nothing to clean up

    sender = asyncio.create_task(pump())
    try:
        # Watch for the client closing; incoming messages are ignored (the stream is read-only).
        while not sender.done():
            receive = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait({receive, sender}, return_when=asyncio.FIRST_COMPLETED)
            if receive in done and receive.result()["type"] == "websocket.disconnect":
                break
            if receive not in done:
                receive.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        logger.info("event_stream_closed", extra={"incident_id": incident_id})
