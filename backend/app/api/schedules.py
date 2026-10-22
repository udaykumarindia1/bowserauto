"""Tenant-scoped CRUD for allowlisted scheduled browser workflows."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.deps import DB, CurrentAuth, require_role
from app.models import OrgRole
from app.schemas import TaskScheduleCreate, TaskScheduleOut, TaskScheduleUpdate
from app.services import schedule_service
from app.services.audit import audit

router = APIRouter(prefix="/schedules", tags=["schedules"])
Writer = Annotated[object, Depends(require_role(OrgRole.member.value))]


@router.get("", response_model=list[TaskScheduleOut])
async def list_schedules(auth: CurrentAuth, db: DB) -> list[TaskScheduleOut]:
    schedules = await schedule_service.list_schedules(db, auth.org_id)
    return [TaskScheduleOut.model_validate(schedule) for schedule in schedules]


@router.post("", response_model=TaskScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: TaskScheduleCreate, request: Request, auth: CurrentAuth, db: DB, _: Writer
) -> TaskScheduleOut:
    schedule = await schedule_service.create_schedule(db, auth.org_id, auth.user_id, payload)
    await audit(
        db,
        "schedule.created",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="task_schedule",
        resource_id=schedule.id,
        data={"kind": schedule.kind, "repeat_interval_minutes": schedule.repeat_interval_minutes},
    )
    await db.commit()
    return TaskScheduleOut.model_validate(schedule)


@router.patch("/{schedule_id}", response_model=TaskScheduleOut)
async def update_schedule(
    schedule_id: uuid.UUID,
    payload: TaskScheduleUpdate,
    request: Request,
    auth: CurrentAuth,
    db: DB,
    _: Writer,
) -> TaskScheduleOut:
    schedule = await schedule_service.get_schedule(db, auth.org_id, schedule_id)
    schedule = await schedule_service.update_schedule(db, schedule, payload)
    await audit(
        db,
        "schedule.updated",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="task_schedule",
        resource_id=schedule.id,
        data={"enabled": schedule.enabled, "changed_fields": sorted(payload.model_fields_set)},
    )
    await db.commit()
    return TaskScheduleOut.model_validate(schedule)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer
) -> Response:
    schedule = await schedule_service.get_schedule(db, auth.org_id, schedule_id)
    await audit(
        db,
        "schedule.deleted",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="task_schedule",
        resource_id=schedule.id,
    )
    await schedule_service.delete_schedule(db, schedule)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
