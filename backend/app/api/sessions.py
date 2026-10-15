"""Browser session endpoints (persistent login profiles)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from fastapi.responses import Response as RawResponse

from app.core.config import get_settings
from app.core.deps import DB, CurrentAuth, require_role
from app.core.errors import ConflictError, ValidationFailed
from app.models import OrgRole
from app.realtime.bus import get_bus, input_channel
from app.schemas import (
    BrowserSessionCreate,
    BrowserSessionOut,
    BrowserSessionUpdate,
    NavigateRequest,
    ScreenshotOut,
)
from app.services import file_service, session_service
from app.services.audit import audit
from app.storage.paths import safe_filename, unique_path

router = APIRouter(prefix="/sessions", tags=["browser-sessions"])
Writer = Annotated[object, Depends(require_role(OrgRole.member.value))]


def _out(session) -> BrowserSessionOut:  # noqa: ANN001
    data = BrowserSessionOut.model_validate(session).model_dump()
    data["has_saved_state"] = bool(session.storage_state_encrypted)
    return BrowserSessionOut(**data)


@router.get("", response_model=list[BrowserSessionOut])
async def list_sessions(auth: CurrentAuth, db: DB) -> list[BrowserSessionOut]:
    sessions = await session_service.list_sessions(db, auth.org_id)
    await db.commit()  # persist liveness corrections
    return [_out(s) for s in sessions]


@router.post("", response_model=BrowserSessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: BrowserSessionCreate, request: Request, auth: CurrentAuth, db: DB, _: Writer
) -> BrowserSessionOut:
    session = await session_service.create_session(db, auth, payload.model_dump())
    await audit(
        db,
        "session.created",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="browser_session",
        resource_id=session.id,
    )
    await db.commit()
    return _out(session)


@router.get("/{session_id}", response_model=BrowserSessionOut)
async def get_session(session_id: uuid.UUID, auth: CurrentAuth, db: DB) -> BrowserSessionOut:
    session = await session_service.get_session(db, auth.org_id, session_id)
    return _out(session)


@router.patch("/{session_id}", response_model=BrowserSessionOut)
async def update_session(
    session_id: uuid.UUID, payload: BrowserSessionUpdate, auth: CurrentAuth, db: DB, _: Writer
) -> BrowserSessionOut:
    session = await session_service.get_session(db, auth.org_id, session_id)
    session = await session_service.update_session(db, session, payload.model_dump(exclude_unset=True))
    return _out(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer
) -> Response:
    session = await session_service.get_session(db, auth.org_id, session_id)
    await audit(
        db,
        "session.deleted",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="browser_session",
        resource_id=session.id,
    )
    await session_service.delete_session(db, session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{session_id}/open", response_model=BrowserSessionOut)
async def open_session(
    session_id: uuid.UUID,
    request: Request,
    auth: CurrentAuth,
    db: DB,
    _: Writer,
    payload: NavigateRequest | None = None,
) -> BrowserSessionOut:
    session = await session_service.get_session(db, auth.org_id, session_id)
    session = await session_service.request_open(
        db, session, interactive=True, url=payload.url if payload else None
    )
    await audit(
        db,
        "session.opened",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="browser_session",
        resource_id=session.id,
    )
    await db.commit()
    return _out(session)


@router.post("/{session_id}/close", response_model=BrowserSessionOut)
async def close_session(session_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer) -> BrowserSessionOut:
    session = await session_service.get_session(db, auth.org_id, session_id)
    session = await session_service.request_close(db, session)
    return _out(session)


@router.post("/{session_id}/takeover", response_model=BrowserSessionOut)
async def takeover_session(
    session_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer
) -> BrowserSessionOut:
    """Give the human direct control of an open session (outside of a task)."""
    session = await session_service.get_session(db, auth.org_id, session_id)
    if session.status not in ("ready", "in_use", "launching"):
        session = await session_service.request_open(db, session, interactive=True)
    else:
        session = await session_service.set_interactive(db, session, True)
    await audit(
        db,
        "session.takeover",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="browser_session",
        resource_id=session.id,
    )
    await db.commit()
    return _out(session)


@router.post("/{session_id}/release", response_model=BrowserSessionOut)
async def release_session(session_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer) -> BrowserSessionOut:
    session = await session_service.get_session(db, auth.org_id, session_id)
    session = await session_service.set_interactive(db, session, False)
    return _out(session)


@router.post("/{session_id}/navigate", response_model=BrowserSessionOut)
async def navigate(
    session_id: uuid.UUID, payload: NavigateRequest, auth: CurrentAuth, db: DB, _: Writer
) -> BrowserSessionOut:
    session = await session_service.get_session(db, auth.org_id, session_id)
    if session.status not in ("ready", "in_use"):
        raise ConflictError("Open the session first")
    await session_service.navigate(db, session, payload.url)
    return _out(session)


@router.post("/{session_id}/logout", response_model=BrowserSessionOut)
async def logout_session(
    session_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer
) -> BrowserSessionOut:
    session = await session_service.get_session(db, auth.org_id, session_id)
    session = await session_service.logout_session(db, session)
    await audit(
        db,
        "session.logout",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="browser_session",
        resource_id=session.id,
    )
    await db.commit()
    return _out(session)


@router.post("/{session_id}/mark-login", response_model=BrowserSessionOut)
async def mark_login(
    session_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer, logged_in: bool = True
) -> BrowserSessionOut:
    """The user confirms they finished logging in manually."""
    session = await session_service.get_session(db, auth.org_id, session_id)
    session.login_status = "logged_in" if logged_in else "logged_out"
    await db.commit()
    return _out(session)


@router.post("/{session_id}/upload", response_model=dict)
async def upload_during_takeover(
    session_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer, file: UploadFile = File(...)
) -> dict:
    """Upload a file into the live browser while the human has control (file chooser or file input)."""
    session = await session_service.get_session(db, auth.org_id, session_id)
    if not session.interactive:
        raise ConflictError("Take over the browser before uploading files")
    settings = get_settings()
    suffix = (file.filename or "").lower().rsplit(".", 1)
    ext = f".{suffix[1]}" if len(suffix) == 2 else ""
    if ext not in settings.allowed_upload_extensions:
        raise ValidationFailed(f"File type {ext or '(none)'} is not allowed")
    folder = settings.tmp_dir / "takeover-uploads" / str(session.id)
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder / safe_filename(file.filename or "upload", f"upload{ext}"))
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    written = 0
    with open(target, "wb") as fh:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                fh.close()
                target.unlink(missing_ok=True)
                raise ValidationFailed(f"File exceeds the {settings.max_upload_size_mb} MB upload limit")
            fh.write(chunk)
    await get_bus().publish(
        input_channel(session.id), {"type": "upload_file", "path": str(target), "filename": target.name}
    )
    return {"ok": True, "filename": target.name}


@router.get("/{session_id}/screenshots", response_model=list[dict])
async def session_screenshots(session_id: uuid.UUID, auth: CurrentAuth, db: DB) -> list[dict]:
    session = await session_service.get_session(db, auth.org_id, session_id)
    shots = await file_service.list_screenshots(db, auth.org_id, session_id=session.id, limit=60)
    return [
        {
            **ScreenshotOut.model_validate(s).model_dump(),
            "image": file_service.signed_url("screenshot", auth.org_id, s.id, inline=True, ttl=3600),
        }
        for s in shots
    ]


@router.get("/{session_id}/preview.jpg", include_in_schema=False)
async def preview(session_id: uuid.UUID, auth: CurrentAuth, db: DB) -> RawResponse:
    """Latest known frame (fallback for clients without WebSocket support)."""
    session = await session_service.get_session(db, auth.org_id, session_id)
    from app.realtime.bus import frames_channel

    bus = get_bus()
    sub = bus.subscribe(frames_channel(session.id), maxsize=2)
    try:
        await bus.publish(input_channel(session.id), {"type": "control", "action": "viewer_connected"})
        import asyncio

        it = sub.__aiter__()
        try:
            _ch, msg = await asyncio.wait_for(it.__anext__(), timeout=3)
        except (TimeoutError, StopAsyncIteration):
            return RawResponse(status_code=204)
        import base64

        data = msg.get("data")
        if not data:
            return RawResponse(status_code=204)
        return RawResponse(
            content=base64.b64decode(data), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )
    finally:
        bus.unsubscribe(sub)
