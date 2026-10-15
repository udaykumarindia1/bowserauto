"""In-app notifications."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.core.deps import DB, CurrentAuth
from app.notifications import configured_channels
from app.schemas import NotificationOut
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=dict)
async def list_notifications(auth: CurrentAuth, db: DB, unread_only: bool = False, limit: int = Query(default=50, le=200)) -> dict:
    rows, unread = await notification_service.list_notifications(db, auth.org_id, auth.user_id, unread_only=unread_only, limit=limit)
    return {"items": [NotificationOut.model_validate(r) for r in rows], "unread": unread, "channels": ["in_app", "browser", *configured_channels()]}


@router.post("/read", response_model=dict)
async def mark_all_read(auth: CurrentAuth, db: DB) -> dict:
    return {"updated": await notification_service.mark_read(db, auth.org_id, auth.user_id)}


@router.post("/{notification_id}/read", response_model=dict)
async def mark_one_read(notification_id: uuid.UUID, auth: CurrentAuth, db: DB) -> dict:
    return {"updated": await notification_service.mark_read(db, auth.org_id, auth.user_id, notification_id)}
