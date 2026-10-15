"""Task endpoints: queue, control actions, details, steps, agent runs, screenshots, SSE events."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.deps import DB, CurrentAuth, require_role
from app.models import ActivityLog, AgentAction, AgentRun, OrgRole
from app.realtime.bus import get_bus, org_channel
from app.schemas import (
    ActivityLogOut,
    AgentActionOut,
    AgentRunOut,
    ReorderQueueRequest,
    SceneActionRequest,
    SceneOut,
    ScreenshotOut,
    TaskCreate,
    TaskDetail,
    TaskOut,
    TaskStepOut,
    TaskUpdate,
)
from app.services import file_service, task_service
from app.services.audit import audit

router = APIRouter(prefix="/tasks", tags=["tasks"])
Writer = Annotated[object, Depends(require_role(OrgRole.member.value))]


@router.get("", response_model=dict)
async def list_tasks(
    auth: CurrentAuth,
    db: DB,
    status_filter: str | None = Query(default=None, alias="status"),
    project_id: uuid.UUID | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> dict:
    tasks, total = await task_service.list_tasks(
        db, auth.org_id, status=status_filter, project_id=project_id, limit=limit, offset=offset
    )
    return {"items": [TaskOut.model_validate(t) for t in tasks], "total": total}


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> TaskOut:
    data = payload.model_dump()
    data["config"] = payload.config.model_dump()
    task = await task_service.create_task(db, auth, data)
    await audit(
        db,
        "task.created",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="task",
        resource_id=task.id,
        data={"kind": task.kind, "platform": task.platform_key},
    )
    await db.commit()
    return TaskOut.model_validate(task)


@router.post("/stop-all", response_model=dict)
async def stop_everything(request: Request, auth: CurrentAuth, db: DB, _: Writer) -> dict:
    count = await task_service.stop_everything(db, auth.org_id)
    await audit(
        db, "task.stop_all", request=request, user_id=auth.user_id, org_id=auth.org_id, data={"count": count}
    )
    await db.commit()
    return {"stopped": count}


@router.post("/reorder", response_model=list[TaskOut])
async def reorder_queue(payload: ReorderQueueRequest, auth: CurrentAuth, db: DB, _: Writer) -> list[TaskOut]:
    tasks = await task_service.reorder_queue(db, auth.org_id, payload.task_ids)
    return [TaskOut.model_validate(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(task_id: uuid.UUID, auth: CurrentAuth, db: DB) -> TaskDetail:
    task = await task_service.get_task(db, auth.org_id, task_id)
    extra = await task_service.task_detail(db, task)
    return TaskDetail(
        **TaskOut.model_validate(task).model_dump(),
        steps=[TaskStepOut.model_validate(s) for s in extra["steps"]],
        scenes=[SceneOut.model_validate(s) for s in extra["scenes"]],
        project_name=extra["project_name"],
        session_name=extra["session_name"],
    )


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID, payload: TaskUpdate, auth: CurrentAuth, db: DB, _: Writer
) -> TaskOut:
    task = await task_service.get_task(db, auth.org_id, task_id)
    data = payload.model_dump(exclude_unset=True)
    if "config" in data and payload.config is not None:
        data["config"] = payload.config.model_dump(exclude_unset=True)
    task = await task_service.update_task(db, task, data)
    return TaskOut.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> Response:
    task = await task_service.get_task(db, auth.org_id, task_id)
    await audit(
        db,
        "task.deleted",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="task",
        resource_id=task.id,
    )
    await task_service.delete_task(db, task)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------- controls
async def _control(
    action: str, task_id: uuid.UUID, request: Request, auth, db, scene_id: uuid.UUID | None = None
) -> TaskOut:  # noqa: ANN001
    task = await task_service.get_task(db, auth.org_id, task_id)
    handler = {
        "start": task_service.start_task,
        "pause": task_service.pause_task,
        "resume": task_service.resume_task,
        "stop": task_service.stop_task,
        "takeover": task_service.takeover_task,
        "release": task_service.release_task,
    }
    if action == "retry":
        task = await task_service.retry_task(db, task, scene_id=scene_id)
    elif action == "skip":
        task = await task_service.skip_scene(db, task, scene_id)
    else:
        task = await handler[action](db, task)
    await audit(
        db,
        f"task.{action}",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="task",
        resource_id=task.id,
    )
    await db.commit()
    await db.refresh(task)
    return TaskOut.model_validate(task)


@router.post("/{task_id}/start", response_model=TaskOut)
async def start_task(task_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> TaskOut:
    return await _control("start", task_id, request, auth, db)


@router.post("/{task_id}/pause", response_model=TaskOut)
async def pause_task(task_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> TaskOut:
    return await _control("pause", task_id, request, auth, db)


@router.post("/{task_id}/resume", response_model=TaskOut)
async def resume_task(task_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> TaskOut:
    return await _control("resume", task_id, request, auth, db)


@router.post("/{task_id}/stop", response_model=TaskOut)
async def stop_task(task_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> TaskOut:
    return await _control("stop", task_id, request, auth, db)


@router.post("/{task_id}/takeover", response_model=TaskOut)
async def takeover(task_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> TaskOut:
    return await _control("takeover", task_id, request, auth, db)


@router.post("/{task_id}/release", response_model=TaskOut)
async def release(task_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> TaskOut:
    return await _control("release", task_id, request, auth, db)


@router.post("/{task_id}/retry", response_model=TaskOut)
async def retry(
    task_id: uuid.UUID,
    request: Request,
    auth: CurrentAuth,
    db: DB,
    _: Writer,
    payload: SceneActionRequest | None = None,
) -> TaskOut:
    return await _control("retry", task_id, request, auth, db, scene_id=payload.scene_id if payload else None)


@router.post("/{task_id}/skip", response_model=TaskOut)
async def skip(
    task_id: uuid.UUID,
    request: Request,
    auth: CurrentAuth,
    db: DB,
    _: Writer,
    payload: SceneActionRequest | None = None,
) -> TaskOut:
    return await _control("skip", task_id, request, auth, db, scene_id=payload.scene_id if payload else None)


# ----------------------------------------------------------------------------- details
@router.get("/{task_id}/activity", response_model=list[ActivityLogOut])
async def task_activity(
    task_id: uuid.UUID,
    auth: CurrentAuth,
    db: DB,
    limit: int = Query(default=200, le=1000),
    before: str | None = None,
) -> list[ActivityLogOut]:
    task = await task_service.get_task(db, auth.org_id, task_id)
    query = select(ActivityLog).where(ActivityLog.task_id == task.id)
    if before:
        from datetime import datetime

        query = query.where(ActivityLog.created_at < datetime.fromisoformat(before))
    rows = (await db.execute(query.order_by(ActivityLog.created_at.desc()).limit(limit))).scalars().all()
    return [ActivityLogOut.model_validate(r) for r in reversed(rows)]


@router.get("/{task_id}/agent-runs", response_model=list[dict])
async def task_agent_runs(task_id: uuid.UUID, auth: CurrentAuth, db: DB) -> list[dict]:
    task = await task_service.get_task(db, auth.org_id, task_id)
    runs = (
        (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.task_id == task.id)
                .order_by(AgentRun.started_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for run in runs:
        actions = (
            (
                await db.execute(
                    select(AgentAction).where(AgentAction.agent_run_id == run.id).order_by(AgentAction.index)
                )
            )
            .scalars()
            .all()
        )
        out.append(
            {
                **AgentRunOut.model_validate(run).model_dump(),
                "actions": [AgentActionOut.model_validate(a).model_dump() for a in actions],
            }
        )
    return out


@router.get("/{task_id}/screenshots", response_model=list[dict])
async def task_screenshots(
    task_id: uuid.UUID, auth: CurrentAuth, db: DB, limit: int = Query(default=60, le=300)
) -> list[dict]:
    task = await task_service.get_task(db, auth.org_id, task_id)
    shots = await file_service.list_screenshots(db, auth.org_id, task_id=task.id, limit=limit)
    return [
        {
            **ScreenshotOut.model_validate(s).model_dump(),
            "image": file_service.signed_url("screenshot", auth.org_id, s.id, inline=True, ttl=3600),
        }
        for s in shots
    ]


@router.get("/{task_id}/events")
async def task_events(task_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB) -> StreamingResponse:
    """Server-Sent Events stream of everything that happens to one task."""
    task = await task_service.get_task(db, auth.org_id, task_id)
    org_id = auth.org_id
    tid = str(task.id)

    async def stream():
        bus = get_bus()
        sub = bus.subscribe(org_channel(org_id), maxsize=1000)
        try:
            yield f"event: snapshot\ndata: {json.dumps(TaskOut.model_validate(task).model_dump(mode='json'))}\n\n"
            queue = sub.__aiter__()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    _channel, message = await asyncio.wait_for(queue.__anext__(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                except StopAsyncIteration:
                    break
                if message.get("task_id") != tid:
                    continue
                yield f"event: {message.get('type', 'message')}\ndata: {json.dumps(message, default=str)}\n\n"
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
