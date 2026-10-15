"""Real-time endpoints: organisation SSE stream and the multiplexed WebSocket.

WebSocket protocol (JSON text frames)
-------------------------------------
server → client
  {"type": "hello", ...}                      after a successful handshake
  {"type": "<domain event>", ...}             every org event (task.progress, agent.action, …)
  {"type": "frame", "session_id", "data"…}    live browser frames for subscribed sessions
  {"type": "status"|"action", "session_id"…}  browser status/action indicator updates
  {"type": "pong"}

client → server
  {"type": "subscribe_session", "session_id": "…"}
  {"type": "unsubscribe_session", "session_id": "…"}
  {"type": "input", "session_id": "…", "event": {...}}   human takeover input (members only)
  {"type": "ping"}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.core.database import get_session_factory
from app.core.deps import CurrentAuth, get_ws_auth
from app.models import BrowserSession
from app.realtime.bus import frames_channel, get_bus, input_channel, org_channel, status_channel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["realtime"])

ALLOWED_INPUT_TYPES = {"mousemove", "mousedown", "mouseup", "click", "wheel", "keydown", "keyup", "text", "paste", "navigate", "back", "forward", "reload", "switch_tab", "new_tab", "close_tab", "release_keys"}


@router.get("/events")
async def org_events(request: Request, auth: CurrentAuth) -> StreamingResponse:
    org_id = auth.org_id

    async def stream():
        bus = get_bus()
        sub = bus.subscribe(org_channel(org_id), maxsize=1000)
        try:
            yield ": connected\n\n"
            it = sub.__aiter__()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    _channel, message = await asyncio.wait_for(it.__anext__(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                except StopAsyncIteration:
                    break
                yield f"event: {message.get('type', 'message')}\ndata: {json.dumps(message, default=str)}\n\n"
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    factory = get_session_factory()
    async with factory() as db:
        auth = await get_ws_auth(websocket, db)
    if auth is None:
        await websocket.close(code=4401, reason="unauthorized")
        return
    await websocket.accept()
    org_id = auth.org_id
    can_control = auth.can_write
    bus = get_bus()
    org_sub = bus.subscribe(org_channel(org_id), maxsize=2000)
    session_subs: dict[str, list] = {}
    send_lock = asyncio.Lock()

    async def send(message: dict) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps(message, default=str))

    async def pump(sub) -> None:  # noqa: ANN001
        try:
            async for _channel, message in sub:
                await send(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # socket closed
            logger.debug("ws pump ended: %s", exc)

    pumps: list[asyncio.Task] = [asyncio.create_task(pump(org_sub))]

    async def subscribe_session(session_id: str) -> None:
        if session_id in session_subs:
            await bus.publish(input_channel(session_id), {"type": "control", "action": "viewer_connected"})
            return
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            await send({"type": "error", "message": "invalid session id"})
            return
        async with factory() as db:
            session = await db.get(BrowserSession, sid)
            if session is None or session.organization_id != org_id:
                await send({"type": "error", "message": "session not found"})
                return
        frames = bus.subscribe(frames_channel(sid), maxsize=3, drop_oldest=True)  # slow viewers skip frames
        status = bus.subscribe(status_channel(sid), maxsize=100)
        session_subs[session_id] = [frames, status]
        pumps.append(asyncio.create_task(pump(frames)))
        pumps.append(asyncio.create_task(pump(status)))
        await send({"type": "subscribed", "session_id": session_id})
        await bus.publish(input_channel(sid), {"type": "control", "action": "viewer_connected"})

    def unsubscribe_session(session_id: str) -> None:
        for sub in session_subs.pop(session_id, []):
            bus.unsubscribe(sub)

    try:
        await send({"type": "hello", "user_id": str(auth.user_id), "organization_id": str(org_id), "role": auth.role})
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 64_000:
                await send({"type": "error", "message": "message too large"})
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await send({"type": "error", "message": "invalid json"})
                continue
            kind = message.get("type")
            if kind == "ping":
                await send({"type": "pong", "ts": message.get("ts")})
            elif kind == "subscribe_session":
                await subscribe_session(str(message.get("session_id", "")))
            elif kind == "unsubscribe_session":
                unsubscribe_session(str(message.get("session_id", "")))
            elif kind == "input":
                if not can_control:
                    await send({"type": "error", "message": "viewers cannot control the browser"})
                    continue
                session_id = str(message.get("session_id", ""))
                event = message.get("event") or {}
                if session_id not in session_subs or not isinstance(event, dict) or event.get("type") not in ALLOWED_INPUT_TYPES:
                    continue
                await bus.publish(input_channel(session_id), {**event, "user_id": str(auth.user_id)})
            else:
                await send({"type": "error", "message": f"unknown message type {kind!r}"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("websocket closed: %s", exc)
    finally:
        for task in pumps:
            task.cancel()
        for task in pumps:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        bus.unsubscribe(org_sub)
        for session_id in list(session_subs):
            unsubscribe_session(session_id)
