"""Idempotent scheduler for domain-allowlisted generic-agent tasks.

All schedule claims and queued-task creation commit together. An organization row lock serializes
schedule dispatch with task claims on PostgreSQL; a conditional update prevents duplicate dispatch
on databases without row-level locks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.core.database import session_scope
from app.models import BrowserSession, Organization, Task, TaskSchedule, TaskStatus
from app.realtime.bus import WORKER_WAKEUP, get_bus
from app.realtime.events import emit
from app.services.org_settings import effective_limits
from app.services.schedule_service import schedule_config_for_dispatch

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = [status.value for status in TaskStatus if status.is_active]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def dispatch_due_schedules(*, batch_size: int = 20) -> int:
    """Queue due schedules once; missed repeating intervals are not backfilled in a burst."""
    now = datetime.now(UTC)
    dispatched = 0
    async with session_scope() as db:
        schedules = (
            (
                await db.execute(
                    select(TaskSchedule)
                    .where(TaskSchedule.enabled.is_(True), TaskSchedule.next_run_at <= now)
                    .order_by(TaskSchedule.next_run_at.asc(), TaskSchedule.created_at.asc())
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

        for schedule in schedules:
            # Serialize organization capacity checks and dispatch across workers. On SQLite this
            # clause is ignored, but the conditional schedule update still gives one-shot claims.
            org = (
                await db.execute(
                    select(Organization).where(Organization.id == schedule.organization_id).with_for_update()
                )
            ).scalar_one_or_none()
            if org is None:
                await _disable_invalid_schedule(db, schedule, "Organization no longer exists")
                continue

            # Re-read after acquiring the organization lock: an API edit/pause may have committed
            # while this worker waited for that lock. Use the current policy and next-run time.
            current = (
                await db.execute(
                    select(TaskSchedule)
                    .where(TaskSchedule.id == schedule.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if current is None or not current.enabled or _utc(current.next_run_at) > now:
                continue
            schedule = current

            config_error = ""
            try:
                config = await schedule_config_for_dispatch(schedule, org)
            except Exception as exc:
                config_error = str(exc)[:500]
                config = {}
            if config_error:
                await _disable_invalid_schedule(db, schedule, config_error)
                continue

            if schedule.browser_session_id:
                session = await db.get(BrowserSession, schedule.browser_session_id)
                if session is None or session.status == "expired":
                    await _disable_invalid_schedule(
                        db, schedule, "Browser session is missing or expired; reconnect it before resuming"
                    )
                    continue

            if schedule.repeat_interval_minutes and schedule.last_task_id:
                previous = await db.get(Task, schedule.last_task_id)
                if previous is not None and previous.status in ACTIVE_STATUSES:
                    await _advance_schedule(
                        db,
                        schedule,
                        now + timedelta(minutes=schedule.repeat_interval_minutes),
                        error="Skipped this interval because the previous scheduled task is still active",
                    )
                    await emit(
                        db,
                        "schedule.updated",
                        schedule.organization_id,
                        f"Skipped overlapping run for '{schedule.title}'",
                        payload={"schedule_id": str(schedule.id), "last_task_id": str(previous.id)},
                    )
                    continue

            limits = effective_limits(org)
            queue_cap = max(limits["max_concurrent_tasks"] * 5, 10)
            active_count = (
                await db.execute(
                    select(func.count(Task.id)).where(
                        Task.organization_id == schedule.organization_id,
                        Task.status.in_(ACTIVE_STATUSES),
                    )
                )
            ).scalar_one()
            if active_count >= queue_cap:
                await _advance_schedule(
                    db,
                    schedule,
                    now + timedelta(minutes=1),
                    error="Deferred because the organization task queue is at capacity",
                )
                continue

            expected_run_at = schedule.next_run_at
            interval = schedule.repeat_interval_minutes
            next_run_at = now + timedelta(minutes=interval) if interval else expected_run_at
            claimed = await db.execute(
                update(TaskSchedule)
                .where(
                    TaskSchedule.id == schedule.id,
                    TaskSchedule.enabled.is_(True),
                    TaskSchedule.next_run_at == expected_run_at,
                )
                .values(
                    enabled=bool(interval),
                    next_run_at=next_run_at,
                    last_run_at=now,
                    last_error="",
                )
            )
            if claimed.rowcount != 1:
                continue

            task_title = f"{schedule.title[:180]} · scheduled"
            task = Task(
                organization_id=schedule.organization_id,
                project_id=None,
                created_by=schedule.created_by,
                title=task_title[:200],
                kind=schedule.kind,
                instructions=schedule.instructions,
                status=TaskStatus.queued.value,
                platform_key=schedule.platform_key,
                browser_session_id=schedule.browser_session_id,
                config=config,
                priority=schedule.priority,
                progress={
                    "total": 0,
                    "completed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "current_scene_id": None,
                    "current_scene_code": None,
                    "percent": 0,
                },
                queued_at=now,
            )
            db.add(task)
            await db.flush()
            await db.execute(
                update(TaskSchedule).where(TaskSchedule.id == schedule.id).values(last_task_id=task.id)
            )
            await emit(
                db,
                "task.created",
                schedule.organization_id,
                f"Scheduled task '{task.title}' created",
                task_id=task.id,
                session_id=schedule.browser_session_id,
                payload={"status": task.status, "schedule_id": str(schedule.id)},
            )
            await emit(
                db,
                "task.queued",
                schedule.organization_id,
                f"Scheduled task '{task.title}' queued",
                task_id=task.id,
                session_id=schedule.browser_session_id,
                payload={"status": task.status, "schedule_id": str(schedule.id)},
            )
            await emit(
                db,
                "schedule.dispatched",
                schedule.organization_id,
                f"Scheduled workflow '{schedule.title}' dispatched",
                task_id=task.id,
                session_id=schedule.browser_session_id,
                payload={"schedule_id": str(schedule.id)},
            )
            dispatched += 1

    if dispatched:
        await get_bus().publish(WORKER_WAKEUP, {"task_id": None, "control": None})
    return dispatched


async def _advance_schedule(db, schedule: TaskSchedule, next_run_at: datetime, *, error: str) -> None:  # noqa: ANN001
    await db.execute(
        update(TaskSchedule)
        .where(
            TaskSchedule.id == schedule.id,
            TaskSchedule.enabled.is_(True),
            TaskSchedule.next_run_at == schedule.next_run_at,
        )
        .values(next_run_at=next_run_at, last_error=error)
    )


async def _disable_invalid_schedule(db, schedule: TaskSchedule, error: str) -> None:  # noqa: ANN001
    result = await db.execute(
        update(TaskSchedule)
        .where(
            TaskSchedule.id == schedule.id,
            TaskSchedule.enabled.is_(True),
            TaskSchedule.next_run_at == schedule.next_run_at,
        )
        .values(enabled=False, last_run_at=datetime.now(UTC), last_error=error)
    )
    if result.rowcount:
        await emit(
            db,
            "schedule.updated",
            schedule.organization_id,
            f"Disabled invalid scheduled workflow '{schedule.title}'",
            level="warning",
            payload={"schedule_id": str(schedule.id), "error": error},
        )
