"""In-app notifications (+ fan-out to external channels)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_scope
from app.models import Notification, OrganizationMember, User
from app.notifications import OutboundNotification, dispatch_external
from app.realtime.bus import get_bus, org_channel
from app.realtime.events import build_event

logger = logging.getLogger(__name__)

EXTERNAL_KINDS = {"task_completed", "task_failed", "human_required", "task_attention", "session_expired"}


async def notify(
    org_id: uuid.UUID,
    kind: str,
    title: str,
    body: str = "",
    *,
    task_id: uuid.UUID | None = None,
    severity: str = "info",
    user_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Create an org-wide (or per-user) notification and broadcast it. Never raises."""
    payload = {**(data or {}), "task_id": str(task_id) if task_id else None}
    try:
        async with session_scope() as db:
            note = Notification(
                organization_id=org_id,
                user_id=user_id,
                type=kind,
                severity=severity,
                title=title[:300],
                body=body,
                data=payload,
            )
            db.add(note)
            await db.flush()
            note_id = note.id
            created = note.created_at
            recipients: list[str] = []
            if kind in EXTERNAL_KINDS:
                rows = await db.execute(
                    select(User.email)
                    .join(OrganizationMember, OrganizationMember.user_id == User.id)
                    .where(OrganizationMember.organization_id == org_id, User.is_active.is_(True))
                )
                recipients = [r[0] for r in rows]
        event = build_event(
            "notification",
            org_id,
            {
                "id": str(note_id),
                "type": kind,
                "severity": severity,
                "title": title,
                "body": body,
                "data": payload,
                "created_at": created.isoformat() if created else None,
                "read_at": None,
            },
            task_id=task_id,
        )
        await get_bus().publish(org_channel(org_id), event)
        if kind in EXTERNAL_KINDS:
            asyncio.create_task(
                dispatch_external(
                    OutboundNotification(
                        kind=kind,
                        title=title,
                        body=body,
                        severity=severity,
                        data=payload,
                        recipients=recipients,
                    )
                )
            )
    except Exception as exc:
        logger.warning("notification failed: %s", exc)


async def list_notifications(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = 50
) -> tuple[list[Notification], int]:
    query = select(Notification).where(
        Notification.organization_id == org_id,
        (Notification.user_id.is_(None)) | (Notification.user_id == user_id),
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    rows = (await db.execute(query.order_by(Notification.created_at.desc()).limit(limit))).scalars().all()
    unread = (
        await db.execute(
            select(func.count(Notification.id)).where(
                Notification.organization_id == org_id,
                (Notification.user_id.is_(None)) | (Notification.user_id == user_id),
                Notification.read_at.is_(None),
            )
        )
    ).scalar_one()
    return list(rows), int(unread)


async def mark_read(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, notification_id: uuid.UUID | None = None
) -> int:
    query = update(Notification).where(
        Notification.organization_id == org_id,
        (Notification.user_id.is_(None)) | (Notification.user_id == user_id),
        Notification.read_at.is_(None),
    )
    if notification_id:
        query = query.where(Notification.id == notification_id)
    result = await db.execute(query.values(read_at=datetime.now(UTC)))
    await db.commit()
    return int(result.rowcount or 0)
