"""Activity log + audit log queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.deps import DB, CurrentAuth, require_role
from app.models import ActivityLog, AuditLog, OrgRole
from app.schemas import ActivityLogOut, AuditLogOut

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=list[ActivityLogOut])
async def list_activity(
    auth: CurrentAuth,
    db: DB,
    task_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    level: str | None = None,
    event_type: str | None = None,
    search: str | None = Query(default=None, max_length=200),
    before: datetime | None = None,
    limit: int = Query(default=100, le=1000),
) -> list[ActivityLogOut]:
    query = select(ActivityLog).where(ActivityLog.organization_id == auth.org_id)
    if task_id:
        query = query.where(ActivityLog.task_id == task_id)
    if project_id:
        query = query.where(ActivityLog.project_id == project_id)
    if session_id:
        query = query.where(ActivityLog.browser_session_id == session_id)
    if level:
        query = query.where(ActivityLog.level == level)
    if event_type:
        query = query.where(ActivityLog.event_type.like(f"{event_type}%"))
    if search:
        query = query.where(ActivityLog.message.ilike(f"%{search}%"))
    if before:
        query = query.where(ActivityLog.created_at < before)
    rows = (await db.execute(query.order_by(ActivityLog.created_at.desc()).limit(limit))).scalars().all()
    return [ActivityLogOut.model_validate(r) for r in rows]


@router.get("/audit", response_model=list[AuditLogOut])
async def list_audit(auth: CurrentAuth, db: DB, _: Annotated[object, Depends(require_role(OrgRole.admin.value))], limit: int = Query(default=100, le=1000)) -> list[AuditLogOut]:
    rows = (await db.execute(select(AuditLog).where(AuditLog.organization_id == auth.org_id).order_by(AuditLog.created_at.desc()).limit(limit))).scalars().all()
    return [AuditLogOut.model_validate(r) for r in rows]
