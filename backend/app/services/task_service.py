"""Task lifecycle: creation, queueing, control signals and queue management (API side).

The database is the source of truth for the queue. Workers claim queued tasks atomically
(``app.workers.main``) and own the ``status`` column while a task runs; the API only writes
``control`` for running tasks and publishes a wake-up on the bus so the worker reacts
immediately instead of waiting for its next poll.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import AuthContext
from app.core.errors import ConflictError, LimitExceededError, NotFoundError, ValidationFailed
from app.models import BrowserSession, Project, Scene, SceneStatus, Task, TaskControl, TaskStatus
from app.platforms.registry import get_adapter_class
from app.realtime.bus import WORKER_WAKEUP, get_bus
from app.realtime.events import emit
from app.services.org_settings import effective_limits

ACTIVE_STATUSES = [s.value for s in TaskStatus if s.is_active]
RUNNING_LIKE = (
    TaskStatus.running.value,
    TaskStatus.paused.value,
    TaskStatus.waiting_for_human.value,
    TaskStatus.needs_attention.value,
)


def _now() -> datetime:
    return datetime.now(UTC)


async def _wake_workers(task_id: uuid.UUID | None = None, control: str | None = None) -> None:
    await get_bus().publish(WORKER_WAKEUP, {"task_id": str(task_id) if task_id else None, "control": control})


async def get_task(db: AsyncSession, org_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = await db.get(Task, task_id)
    if task is None or task.organization_id != org_id:
        raise NotFoundError("Task not found")
    return task


async def list_tasks(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    status: str | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Task], int]:
    from sqlalchemy import func

    query = select(Task).where(Task.organization_id == org_id)
    if status == "active":
        query = query.where(Task.status.in_(ACTIVE_STATUSES))
    elif status == "attention":
        query = query.where(
            Task.status.in_([TaskStatus.needs_attention.value, TaskStatus.waiting_for_human.value])
        )
    elif status == "finished":
        query = query.where(
            Task.status.in_([TaskStatus.completed.value, TaskStatus.failed.value, TaskStatus.cancelled.value])
        )
    elif status:
        query = query.where(Task.status == status)
    if project_id:
        query = query.where(Task.project_id == project_id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        (await db.execute(query.order_by(Task.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    )
    return list(rows), int(total)


async def create_task(db: AsyncSession, auth: AuthContext, data: dict[str, Any]) -> Task:
    settings = get_settings()
    limits = effective_limits(auth.organization)
    config = dict(data.get("config") or {})
    config["max_retries"] = min(
        int(config.get("max_retries", settings.max_retries_default)), limits["max_retries"]
    )
    config["generation_timeout_seconds"] = min(
        int(config.get("generation_timeout_seconds", settings.generation_timeout_seconds)),
        limits["generation_timeout_seconds"],
    )
    config["max_task_duration_seconds"] = limits["max_task_duration_seconds"]
    config["max_file_size_mb"] = limits["max_file_size_mb"]
    if config.get("scene_ids") is not None:
        config["scene_ids"] = [str(s) for s in config["scene_ids"]]

    project: Project | None = None
    if data.get("project_id"):
        project = await db.get(Project, data["project_id"])
        if project is None or project.organization_id != auth.org_id:
            raise NotFoundError("Project not found")
    platform_key = data.get("platform_key") or (project.platform_key if project else None) or "mock"
    if get_adapter_class(platform_key) is None:
        raise ValidationFailed(f"Unknown platform '{platform_key}'")
    session_id = data.get("browser_session_id") or (project.browser_session_id if project else None)
    if session_id:
        session = await db.get(BrowserSession, session_id)
        if session is None or session.organization_id != auth.org_id:
            raise NotFoundError("Browser session not found")
        if session.status == "expired":
            raise ConflictError("The selected browser session has expired; reconnect it first")
    kind = data.get("kind", "video_generation")
    scene_count = 0
    if kind == "video_generation":
        if project is None:
            raise ValidationFailed("Video generation tasks require a project")
        scenes = (
            (await db.execute(select(Scene).where(Scene.project_id == project.id).order_by(Scene.index)))
            .scalars()
            .all()
        )
        if not scenes:
            raise ValidationFailed("Analyse the script and review the scenes before starting generation")
        selected = _select_scenes(scenes, config)
        if not selected:
            raise ValidationFailed("No scenes are pending generation")
        if len(selected) > limits["max_scenes_per_task"]:
            raise LimitExceededError(f"A task may contain at most {limits['max_scenes_per_task']} scenes")
        scene_count = len(selected)
        active = (
            await db.execute(
                select(Task.id).where(Task.project_id == project.id, Task.status.in_(ACTIVE_STATUSES))
            )
        ).scalar_one_or_none()
        if active is not None:
            raise ConflictError("This project already has an active task")
        for scene in selected:
            scene.status = SceneStatus.queued.value
            scene.last_error = ""
    else:
        if not (data.get("instructions") or "").strip():
            raise ValidationFailed("Describe what the agent should do")
    active_count = (
        (
            await db.execute(
                select(Task.id).where(Task.organization_id == auth.org_id, Task.status.in_(ACTIVE_STATUSES))
            )
        )
        .scalars()
        .all()
    )
    if len(active_count) >= max(limits["max_concurrent_tasks"] * 5, 10):
        raise LimitExceededError("Too many active tasks; wait for running tasks to finish")
    title = data.get("title") or (
        f"{project.name} – video generation" if project else kind.replace("_", " ").title()
    )
    task = Task(
        organization_id=auth.org_id,
        project_id=project.id if project else None,
        created_by=auth.user_id,
        title=title[:200],
        kind=kind,
        instructions=data.get("instructions", ""),
        status=TaskStatus.queued.value if data.get("start", True) else TaskStatus.draft.value,
        platform_key=platform_key,
        browser_session_id=session_id,
        config=config,
        priority=int(data.get("priority", 5)),
        progress={
            "total": scene_count,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "current_scene_id": None,
            "current_scene_code": None,
            "percent": 0,
        },
        queued_at=_now() if data.get("start", True) else None,
    )
    db.add(task)
    await db.flush()
    await emit(
        db,
        "task.created",
        auth.org_id,
        f"Task '{task.title}' created",
        task_id=task.id,
        project_id=task.project_id,
        payload={"status": task.status, "scenes": scene_count},
    )
    if task.status == TaskStatus.queued.value:
        await emit(
            db,
            "task.queued",
            auth.org_id,
            f"Task '{task.title}' queued",
            task_id=task.id,
            project_id=task.project_id,
            payload={"status": task.status},
        )
    await db.commit()
    await _wake_workers(task.id)
    return task


def _select_scenes(scenes: list[Scene], config: dict[str, Any]) -> list[Scene]:
    ids = config.get("scene_ids")
    if ids:
        wanted = set(ids)
        return [s for s in scenes if str(s.id) in wanted and s.status != SceneStatus.completed.value]
    if config.get("retry_failed_only"):
        return [s for s in scenes if s.status in (SceneStatus.failed.value, SceneStatus.skipped.value)]
    return [s for s in scenes if s.status != SceneStatus.completed.value]


async def start_task(db: AsyncSession, task: Task) -> Task:
    if task.status not in (
        TaskStatus.draft.value,
        TaskStatus.failed.value,
        TaskStatus.cancelled.value,
        TaskStatus.completed.value,
    ):
        raise ConflictError(f"Task cannot be started from status {task.status}")
    if task.project_id:
        active = (
            await db.execute(
                select(Task.id).where(
                    Task.project_id == task.project_id, Task.status.in_(ACTIVE_STATUSES), Task.id != task.id
                )
            )
        ).scalar_one_or_none()
        if active is not None:
            raise ConflictError("This project already has an active task")
        scenes = (
            (await db.execute(select(Scene).where(Scene.project_id == task.project_id).order_by(Scene.index)))
            .scalars()
            .all()
        )
        selected = _select_scenes(scenes, task.config)
        if task.kind == "video_generation" and not selected:
            raise ValidationFailed("No scenes are pending generation")
        for scene in selected:
            scene.status = SceneStatus.queued.value
            scene.last_error = ""
        task.progress = {
            **task.progress,
            "total": len(selected),
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "percent": 0,
            "current_scene_id": None,
        }
    task.status = TaskStatus.queued.value
    task.control = TaskControl.none.value
    task.queued_at = _now()
    task.finished_at = None
    task.error = ""
    task.attention = {}
    task.worker_id = None
    task.heartbeat_at = None
    task.retry_count += 1 if task.started_at else 0
    await emit(
        db,
        "task.queued",
        task.organization_id,
        f"Task '{task.title}' queued",
        task_id=task.id,
        project_id=task.project_id,
        payload={"status": task.status},
    )
    await db.commit()
    await _wake_workers(task.id)
    return task


async def _signal(db: AsyncSession, task: Task, control: TaskControl, event: str, message: str) -> Task:
    task.control = control.value
    await emit(
        db,
        event,
        task.organization_id,
        message,
        task_id=task.id,
        project_id=task.project_id,
        payload={"control": control.value, "status": task.status},
    )
    await db.commit()
    await _wake_workers(task.id, control.value)
    return task


async def pause_task(db: AsyncSession, task: Task) -> Task:
    if task.status == TaskStatus.queued.value and task.worker_id is None:
        result = await db.execute(
            update(Task)
            .where(Task.id == task.id, Task.worker_id.is_(None), Task.status == TaskStatus.queued.value)
            .values(status=TaskStatus.paused.value, paused_at=_now())
        )
        if result.rowcount:
            await db.refresh(task)
            await emit(
                db,
                "task.paused",
                task.organization_id,
                f"Task '{task.title}' paused",
                task_id=task.id,
                project_id=task.project_id,
                payload={"status": task.status},
            )
            await db.commit()
            return task
    if task.status not in (TaskStatus.running.value, TaskStatus.queued.value):
        raise ConflictError(f"Task cannot be paused from status {task.status}")
    return await _signal(db, task, TaskControl.pause, "task.paused", "Pause requested")


async def resume_task(db: AsyncSession, task: Task) -> Task:
    if task.status == TaskStatus.paused.value and task.worker_id is None:
        task.status = TaskStatus.queued.value
        task.control = TaskControl.none.value
        task.queued_at = _now()
        await emit(
            db,
            "task.resumed",
            task.organization_id,
            f"Task '{task.title}' re-queued",
            task_id=task.id,
            project_id=task.project_id,
            payload={"status": task.status},
        )
        await db.commit()
        await _wake_workers(task.id)
        return task
    if task.status not in RUNNING_LIKE:
        raise ConflictError(f"Task cannot be resumed from status {task.status}")
    return await _signal(db, task, TaskControl.resume, "task.resumed", "Resume requested")


async def stop_task(db: AsyncSession, task: Task, *, reason: str = "stopped by user") -> Task:
    if task.status in (TaskStatus.completed.value, TaskStatus.failed.value, TaskStatus.cancelled.value):
        raise ConflictError("Task has already finished")
    if task.worker_id is None:
        result = await db.execute(
            update(Task)
            .where(Task.id == task.id, Task.worker_id.is_(None))
            .values(
                status=TaskStatus.cancelled.value,
                control=TaskControl.none.value,
                finished_at=_now(),
                error=reason,
            )
        )
        if result.rowcount:
            await db.execute(
                update(Scene)
                .where(Scene.project_id == task.project_id, Scene.status == SceneStatus.queued.value)
                .values(status=SceneStatus.draft.value)
            ) if task.project_id else None
            await db.refresh(task)
            await emit(
                db,
                "task.cancelled",
                task.organization_id,
                f"Task '{task.title}' cancelled",
                task_id=task.id,
                project_id=task.project_id,
                payload={"status": task.status},
            )
            await db.commit()
            return task
    return await _signal(db, task, TaskControl.stop, "task.updated", "Stop requested")


async def takeover_task(db: AsyncSession, task: Task) -> Task:
    if task.status not in RUNNING_LIKE:
        raise ConflictError("Take over is only available while a task is running")
    if not task.browser_session_id:
        raise ConflictError("This task has no browser session to take over")
    return await _signal(db, task, TaskControl.takeover, "human.takeover", "Human takeover requested")


async def release_task(db: AsyncSession, task: Task) -> Task:
    if task.status not in RUNNING_LIKE:
        raise ConflictError("Task is not running")
    return await _signal(db, task, TaskControl.release, "human.released", "Returning control to the agent")


async def retry_task(db: AsyncSession, task: Task, *, scene_id: uuid.UUID | None = None) -> Task:
    if (
        task.status
        in (TaskStatus.needs_attention.value, TaskStatus.waiting_for_human.value, TaskStatus.paused.value)
        and task.worker_id
    ):
        return await _signal(db, task, TaskControl.retry_scene, "task.updated", "Retry requested")
    if task.status in (TaskStatus.failed.value, TaskStatus.cancelled.value, TaskStatus.completed.value):
        if task.kind == "video_generation":
            task.config = {
                **task.config,
                "retry_failed_only": True,
                "scene_ids": [str(scene_id)] if scene_id else None,
            }
        return await start_task(db, task)
    raise ConflictError(f"Task cannot be retried from status {task.status}")


async def skip_scene(db: AsyncSession, task: Task, scene_id: uuid.UUID | None = None) -> Task:
    if task.status not in RUNNING_LIKE:
        raise ConflictError("Scenes can only be skipped while the task is running")
    if scene_id:
        scene = await db.get(Scene, scene_id)
        if scene is None or scene.project_id != task.project_id:
            raise NotFoundError("Scene not found")
        current = (task.progress or {}).get("current_scene_id")
        if str(scene.id) != str(current):
            # Not the active scene: mark it skipped directly so the executor passes over it.
            if scene.status in (SceneStatus.queued.value, SceneStatus.failed.value, SceneStatus.draft.value):
                scene.status = SceneStatus.skipped.value
                await emit(
                    db,
                    "scene.skipped",
                    task.organization_id,
                    f"{scene.code} skipped",
                    task_id=task.id,
                    project_id=task.project_id,
                    scene_id=scene.id,
                )
                await db.commit()
                return task
    return await _signal(db, task, TaskControl.skip_scene, "task.updated", "Skip scene requested")


async def update_task(db: AsyncSession, task: Task, data: dict[str, Any]) -> Task:
    if task.status in (TaskStatus.running.value,) and ("config" in data):
        raise ConflictError("Stop or pause the task before changing its configuration")
    for key, value in data.items():
        if key == "config" and value is not None:
            cfg = {**task.config, **value}
            if cfg.get("scene_ids") is not None:
                cfg["scene_ids"] = [str(s) for s in cfg["scene_ids"]]
            task.config = cfg
        elif value is not None:
            setattr(task, key, value)
    await db.commit()
    await _wake_workers(task.id)
    return task


async def reorder_queue(db: AsyncSession, org_id: uuid.UUID, task_ids: list[uuid.UUID]) -> list[Task]:
    """Assign priorities so that queued tasks run in the requested order."""
    tasks = (
        (
            await db.execute(
                select(Task).where(
                    Task.organization_id == org_id,
                    Task.id.in_(task_ids),
                    Task.status == TaskStatus.queued.value,
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {t.id: t for t in tasks}
    base = _now()
    for position, tid in enumerate(task_ids):
        task = by_id.get(tid)
        if task is None:
            continue
        task.priority = max(1, min(10, 1 + position // 2))
        task.queued_at = base.replace(microsecond=0) + __import__("datetime").timedelta(milliseconds=position)
    await db.commit()
    return [by_id[t] for t in task_ids if t in by_id]


async def delete_task(db: AsyncSession, task: Task) -> None:
    if task.status in ACTIVE_STATUSES:
        raise ConflictError("Stop the task before deleting it")
    await db.delete(task)
    await db.commit()


async def stop_everything(db: AsyncSession, org_id: uuid.UUID) -> int:
    tasks = (
        (
            await db.execute(
                select(Task).where(Task.organization_id == org_id, Task.status.in_(ACTIVE_STATUSES))
            )
        )
        .scalars()
        .all()
    )
    count = 0
    for task in tasks:
        try:
            await stop_task(db, task, reason="emergency stop")
            count += 1
        except ConflictError:
            continue
    await emit(
        db,
        "task.updated",
        org_id,
        f"Emergency stop: {count} task(s) stopping",
        payload={"count": count},
        level="warning",
        commit=True,
    )
    return count


async def task_detail(db: AsyncSession, task: Task) -> dict[str, Any]:
    from app.models import TaskStep

    steps = (
        (
            await db.execute(
                select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.index.desc()).limit(300)
            )
        )
        .scalars()
        .all()
    )
    scenes: list[Scene] = []
    if task.project_id:
        scenes = list(
            (await db.execute(select(Scene).where(Scene.project_id == task.project_id).order_by(Scene.index)))
            .scalars()
            .all()
        )
    project_name = None
    if task.project_id:
        project = await db.get(Project, task.project_id)
        project_name = project.name if project else None
    session_name = None
    if task.browser_session_id:
        session = await db.get(BrowserSession, task.browser_session_id)
        session_name = session.name if session else None
    return {
        "steps": list(reversed(steps)),
        "scenes": scenes,
        "project_name": project_name,
        "session_name": session_name,
    }
