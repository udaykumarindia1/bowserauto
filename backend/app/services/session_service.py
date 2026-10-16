"""Browser session management (API side).

Browser sessions are persistent Chromium profiles owned by an organization. The API never
touches the browser directly: it records the *desired* state and workers reconcile it.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import AuthContext
from app.core.errors import ConflictError, LimitExceededError, NotFoundError, ValidationFailed
from app.core.security import encrypt_secret
from app.core.url_safety import UnsafeDestination, require_safe_http_url, sanitize_url
from app.models import BrowserSession, BrowserSessionStatus, Task, TaskStatus
from app.platforms.registry import get_adapter_class
from app.realtime.bus import WORKER_WAKEUP, get_bus, input_channel
from app.realtime.events import emit
from app.services.org_settings import effective_limits


def _now() -> datetime:
    return datetime.now(UTC)


async def list_sessions(db: AsyncSession, org_id: uuid.UUID) -> list[BrowserSession]:
    rows = (
        (
            await db.execute(
                select(BrowserSession)
                .where(BrowserSession.organization_id == org_id)
                .order_by(BrowserSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    now = _now()
    stale = timedelta(seconds=get_settings().worker_stale_after_seconds)
    for s in rows:
        _apply_liveness(s, now, stale)
    return list(rows)


def _apply_liveness(session: BrowserSession, now: datetime, stale: timedelta) -> None:
    """A session hosted by a dead worker must not look 'ready' forever."""
    if session.status in (
        BrowserSessionStatus.ready.value,
        BrowserSessionStatus.in_use.value,
        BrowserSessionStatus.launching.value,
    ):
        hb = session.last_heartbeat_at
        if hb is not None and hb.tzinfo is None:
            hb = hb.replace(tzinfo=UTC)
        if hb is None or now - hb > stale * 2:
            session.status = BrowserSessionStatus.closed.value
            session.worker_id = None
            session.interactive = False
            session.locked_by_task_id = None
    if session.expires_at:
        exp = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=UTC)
        if exp < now and session.status == BrowserSessionStatus.closed.value:
            session.status = BrowserSessionStatus.expired.value


async def get_session(db: AsyncSession, org_id: uuid.UUID, session_id: uuid.UUID) -> BrowserSession:
    session = await db.get(BrowserSession, session_id)
    if session is None or session.organization_id != org_id:
        raise NotFoundError("Browser session not found")
    _apply_liveness(session, _now(), timedelta(seconds=get_settings().worker_stale_after_seconds))
    return session


async def create_session(db: AsyncSession, auth: AuthContext, data: dict[str, Any]) -> BrowserSession:
    settings = get_settings()
    limits = effective_limits(auth.organization)
    count = (
        await db.execute(
            select(func.count(BrowserSession.id)).where(BrowserSession.organization_id == auth.org_id)
        )
    ).scalar_one()
    if count >= limits["max_browser_sessions"]:
        raise LimitExceededError(
            f"Organizations are limited to {limits['max_browser_sessions']} browser sessions"
        )
    adapter_cls = get_adapter_class(data.get("platform_key") or "generic")
    if adapter_cls is None:
        raise ValidationFailed("Unknown platform")
    start_url = (data.get("start_url") or "").strip()
    if not start_url:
        start_url = adapter_cls(None).start_url() if adapter_cls.key != "generic" else ""
    encrypted_start_url = None
    if start_url:
        try:
            require_safe_http_url(start_url, settings=settings)
        except UnsafeDestination as exc:
            raise ValidationFailed(str(exc)) from exc
        sanitized_start_url = sanitize_url(start_url)
        if sanitized_start_url != start_url:
            encrypted_start_url = encrypt_secret(start_url)
        start_url = sanitized_start_url
    session_id = uuid.uuid4()
    profile_dir = settings.profiles_dir / str(session_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.chmod(0o700)
    session = BrowserSession(
        id=session_id,
        organization_id=auth.org_id,
        created_by=auth.user_id,
        name=data["name"],
        platform_key=adapter_cls.key,
        browser_type=data.get("browser_type", "chromium"),
        profile_dir=str(profile_dir),
        start_url=start_url,
        status=BrowserSessionStatus.closed.value,
        desired_state="closed",
        expires_at=_now() + timedelta(days=settings.session_expiry_days),
        metadata_={"start_url_encrypted": encrypted_start_url} if encrypted_start_url else {},
    )
    db.add(session)
    await db.flush()
    await emit(
        db,
        "session.updated",
        auth.org_id,
        f"Browser session '{session.name}' created",
        session_id=session.id,
        payload={"status": session.status},
    )
    await db.commit()
    return session


async def update_session(db: AsyncSession, session: BrowserSession, data: dict[str, Any]) -> BrowserSession:
    if "platform_key" in data and data["platform_key"] and get_adapter_class(data["platform_key"]) is None:
        raise ValidationFailed("Unknown platform")
    metadata = dict(session.metadata_ or {})
    if "start_url" in data and data["start_url"] is not None:
        raw_url = data["start_url"].strip()
        if raw_url:
            try:
                require_safe_http_url(raw_url)
            except UnsafeDestination as exc:
                raise ValidationFailed(str(exc)) from exc
            stored_url = sanitize_url(raw_url)
            metadata.pop("start_url_encrypted", None)
            if stored_url != raw_url:
                metadata["start_url_encrypted"] = encrypt_secret(raw_url)
            session.start_url = stored_url
        else:
            session.start_url = ""
            metadata.pop("start_url_encrypted", None)
        data = {key: value for key, value in data.items() if key != "start_url"}
    for key, value in data.items():
        if value is not None:
            setattr(session, key, value)
    session.metadata_ = metadata
    await db.commit()
    return session


async def request_open(
    db: AsyncSession, session: BrowserSession, *, interactive: bool = True, url: str | None = None
) -> BrowserSession:
    if session.status == BrowserSessionStatus.expired.value:
        session.expires_at = _now() + timedelta(days=get_settings().session_expiry_days)
        session.status = BrowserSessionStatus.closed.value
    session.desired_state = "open"
    session.interactive = interactive
    session.last_used_at = _now()
    if url:
        try:
            require_safe_http_url(url)
        except UnsafeDestination as exc:
            raise ValidationFailed(str(exc)) from exc
        session.metadata_ = {**(session.metadata_ or {}), "open_url_encrypted": encrypt_secret(url)}
    if session.status == BrowserSessionStatus.closed.value:
        session.status = BrowserSessionStatus.launching.value
        session.error = ""
    await emit(
        db,
        "session.updated",
        session.organization_id,
        f"Opening browser session '{session.name}'",
        session_id=session.id,
        payload={"status": session.status, "interactive": interactive},
    )
    await db.commit()
    await get_bus().publish(WORKER_WAKEUP, {"session_id": str(session.id), "action": "open"})
    await get_bus().publish(
        input_channel(session.id),
        {"type": "control", "action": "open", "interactive": interactive, "url": url},
    )
    return session


async def request_close(db: AsyncSession, session: BrowserSession) -> BrowserSession:
    if session.locked_by_task_id:
        task = await db.get(Task, session.locked_by_task_id)
        if task and task.status in (s.value for s in TaskStatus if s.is_active):
            raise ConflictError("A task is using this session; stop the task first")
    session.desired_state = "closed"
    session.interactive = False
    await emit(
        db,
        "session.updated",
        session.organization_id,
        f"Closing browser session '{session.name}'",
        session_id=session.id,
        payload={"status": session.status},
    )
    await db.commit()
    await get_bus().publish(input_channel(session.id), {"type": "control", "action": "close"})
    await get_bus().publish(WORKER_WAKEUP, {"session_id": str(session.id), "action": "close"})
    return session


async def set_interactive(db: AsyncSession, session: BrowserSession, interactive: bool) -> BrowserSession:
    session.interactive = interactive
    session.last_used_at = _now()
    await emit(
        db,
        "human.takeover" if interactive else "human.released",
        session.organization_id,
        "Human control active" if interactive else "Human control released",
        session_id=session.id,
        payload={"interactive": interactive},
    )
    await db.commit()
    await get_bus().publish(
        input_channel(session.id), {"type": "control", "action": "interactive", "interactive": interactive}
    )
    return session


async def navigate(db: AsyncSession, session: BrowserSession, url: str) -> None:
    try:
        require_safe_http_url(url)
    except UnsafeDestination as exc:
        raise ValidationFailed(str(exc)) from exc
    session.last_used_at = _now()
    await db.commit()
    await get_bus().publish(input_channel(session.id), {"type": "navigate", "url": url, "source": "api"})


async def logout_session(db: AsyncSession, session: BrowserSession) -> BrowserSession:
    """Forget the stored login state: wipe the profile directory and encrypted storage state."""
    if session.status in (
        BrowserSessionStatus.ready.value,
        BrowserSessionStatus.in_use.value,
        BrowserSessionStatus.launching.value,
    ):
        raise ConflictError("Close the session before logging out")
    _wipe_profile(Path(session.profile_dir))
    session.storage_state_encrypted = None
    session.login_status = "logged_out"
    session.current_url = ""
    session.current_title = ""
    await emit(
        db,
        "session.updated",
        session.organization_id,
        f"Browser session '{session.name}' logged out",
        session_id=session.id,
        payload={"login_status": "logged_out"},
    )
    await db.commit()
    return session


async def delete_session(db: AsyncSession, session: BrowserSession) -> None:
    if session.locked_by_task_id:
        task = await db.get(Task, session.locked_by_task_id)
        if task and task.status in (s.value for s in TaskStatus if s.is_active):
            raise ConflictError("A task is using this session; stop the task first")
    if session.status in (BrowserSessionStatus.ready.value, BrowserSessionStatus.in_use.value):
        await get_bus().publish(input_channel(session.id), {"type": "control", "action": "close"})
    profile = Path(session.profile_dir)
    org_id = session.organization_id
    await db.delete(session)
    await db.commit()
    _wipe_profile(profile)
    await emit(
        db, "session.updated", org_id, "Browser session deleted", payload={"deleted": True}, commit=True
    )


def _wipe_profile(profile: Path) -> None:
    settings = get_settings()
    try:
        profile.resolve().relative_to(settings.profiles_dir.resolve())
    except ValueError:
        return
    if profile.exists():
        shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True, exist_ok=True)
    profile.chmod(0o700)
