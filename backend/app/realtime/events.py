"""Domain event publishing + persistence of the activity stream.

``emit`` both stores an ActivityLog row (durable history) and publishes the event on the
organization channel so connected UIs update instantly.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import redact
from app.models import ActivityLog
from app.realtime.bus import get_bus, org_channel

logger = logging.getLogger(__name__)

EVENT_TYPES = {
    "task.created",
    "task.queued",
    "task.started",
    "task.paused",
    "task.resumed",
    "task.progress",
    "task.attention",
    "task.completed",
    "task.failed",
    "task.cancelled",
    "task.updated",
    "agent.action",
    "agent.thought",
    "browser.navigation",
    "browser.screenshot",
    "browser.status",
    "session.updated",
    "scene.started",
    "scene.updated",
    "scene.completed",
    "scene.failed",
    "scene.skipped",
    "download.started",
    "download.completed",
    "download.failed",
    "human.takeover",
    "human.released",
    "human.required",
    "notification",
    "file.created",
    "file.deleted",
    "project.updated",
    "worker.status",
}


def _serialize(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def build_event(
    event_type: str, org_id: Any, payload: dict[str, Any] | None = None, **ids: Any
) -> dict[str, Any]:
    return {
        "type": event_type,
        "organization_id": str(org_id),
        "ts": datetime.now(UTC).isoformat(),
        "task_id": str(ids["task_id"]) if ids.get("task_id") else None,
        "project_id": str(ids["project_id"]) if ids.get("project_id") else None,
        "session_id": str(ids["session_id"]) if ids.get("session_id") else None,
        "scene_id": str(ids["scene_id"]) if ids.get("scene_id") else None,
        "payload": _serialize(redact(payload or {})),
    }


async def publish_event(
    event_type: str, org_id: Any, payload: dict[str, Any] | None = None, **ids: Any
) -> dict[str, Any]:
    """Publish a transient event (not persisted)."""
    event = build_event(event_type, org_id, payload, **ids)
    await get_bus().publish(org_channel(org_id), event)
    return event


async def emit(
    db: AsyncSession,
    event_type: str,
    org_id: Any,
    message: str,
    *,
    payload: dict[str, Any] | None = None,
    level: str = "info",
    task_id: Any = None,
    project_id: Any = None,
    session_id: Any = None,
    scene_id: Any = None,
    duration_ms: int | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Persist an activity log entry and broadcast it."""
    if event_type not in EVENT_TYPES:
        logger.debug("Unregistered event type %s", event_type)
    log = ActivityLog(
        organization_id=_as_uuid(org_id),
        task_id=_as_uuid(task_id),
        project_id=_as_uuid(project_id),
        browser_session_id=_as_uuid(session_id),
        scene_id=_as_uuid(scene_id),
        level=level,
        event_type=event_type,
        message=message,
        data=_serialize(redact(payload or {})),
        duration_ms=duration_ms,
    )
    db.add(log)
    if commit:
        await db.commit()
    else:
        await db.flush()
    event = build_event(
        event_type,
        org_id,
        {"message": message, "level": level, "log_id": str(log.id), **(payload or {})},
        task_id=task_id,
        project_id=project_id,
        session_id=session_id,
        scene_id=scene_id,
    )
    await get_bus().publish(org_channel(org_id), event)
    return event


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
