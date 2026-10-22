"""CRUD and dispatch policy for scheduled, domain-allowlisted browser workflows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, LimitExceededError, NotFoundError, ValidationFailed
from app.models import BrowserSession, Organization, TaskSchedule
from app.models.base import utcnow
from app.platforms.registry import get_adapter_class
from app.realtime.events import emit
from app.schemas import TaskConfig, TaskScheduleCreate, TaskScheduleUpdate
from app.services.org_settings import effective_limits


async def list_schedules(db: AsyncSession, org_id: uuid.UUID) -> list[TaskSchedule]:
    rows = (
        (
            await db.execute(
                select(TaskSchedule)
                .where(TaskSchedule.organization_id == org_id)
                .order_by(
                    TaskSchedule.enabled.desc(),
                    TaskSchedule.next_run_at.asc(),
                    TaskSchedule.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_schedule(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> TaskSchedule:
    schedule = await db.get(TaskSchedule, schedule_id)
    if schedule is None or schedule.organization_id != org_id:
        raise NotFoundError("Schedule not found")
    return schedule


async def create_schedule(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, payload: TaskScheduleCreate
) -> TaskSchedule:
    settings = get_settings()
    org = (
        await db.execute(select(Organization).where(Organization.id == org_id).with_for_update())
    ).scalar_one_or_none()
    if org is None:
        raise NotFoundError("Organization not found")
    if not payload.title.strip() or not payload.instructions.strip():
        raise ValidationFailed("Workflow name and instructions must not be blank")
    active_count = (
        await db.execute(
            select(func.count(TaskSchedule.id)).where(
                TaskSchedule.organization_id == org_id, TaskSchedule.enabled.is_(True)
            )
        )
    ).scalar_one()
    if active_count >= settings.max_scheduled_workflows_per_org:
        raise LimitExceededError(
            f"This organization may have at most {settings.max_scheduled_workflows_per_org} enabled schedules"
        )
    if payload.run_at.astimezone(UTC) <= utcnow():
        raise ValidationFailed("Choose a future run time")
    if get_adapter_class(payload.platform_key) is None:
        raise ValidationFailed(f"Unknown platform '{payload.platform_key}'")
    if payload.browser_session_id:
        session = await db.get(BrowserSession, payload.browser_session_id)
        if session is None or session.organization_id != org_id:
            raise NotFoundError("Browser session not found")
        if session.status == "expired":
            raise ConflictError("The selected browser session has expired; reconnect it first")

    config = payload.config.model_dump()
    # A recurring workflow is always dispatched to the generic browser agent, whose tools enforce
    # the domain and action-approval policy stored with this schedule.
    config["agent_mode"] = "generic"
    schedule = TaskSchedule(
        organization_id=org_id,
        created_by=user_id,
        title=payload.title.strip(),
        kind=payload.kind,
        instructions=payload.instructions.strip(),
        platform_key=payload.platform_key,
        browser_session_id=payload.browser_session_id,
        priority=payload.priority,
        config=config,
        next_run_at=payload.run_at.astimezone(UTC),
        repeat_interval_minutes=payload.repeat_interval_minutes,
        enabled=True,
        last_error="",
    )
    db.add(schedule)
    await db.flush()
    await emit(
        db,
        "schedule.created",
        org_id,
        f"Scheduled workflow '{schedule.title}' created",
        payload={"schedule_id": str(schedule.id), "next_run_at": schedule.next_run_at.isoformat()},
    )
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def update_schedule(
    db: AsyncSession, schedule: TaskSchedule, payload: TaskScheduleUpdate
) -> TaskSchedule:
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ValidationFailed("Provide at least one schedule field to update")

    org = (
        await db.execute(
            select(Organization).where(Organization.id == schedule.organization_id).with_for_update()
        )
    ).scalar_one_or_none()
    if org is None:
        raise NotFoundError("Organization not found")
    current = (
        await db.execute(
            select(TaskSchedule)
            .where(TaskSchedule.id == schedule.id, TaskSchedule.organization_id == schedule.organization_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if current is None:
        raise NotFoundError("Schedule not found")
    schedule = current

    non_nullable = {
        "enabled",
        "title",
        "kind",
        "instructions",
        "platform_key",
        "priority",
        "run_at",
        "config",
    }
    if any(key in data and data[key] is None for key in non_nullable):
        raise ValidationFailed("Schedule fields cannot be cleared")

    if schedule.last_task_id and schedule.repeat_interval_minutes is None and not schedule.enabled:
        if any(key != "enabled" for key in data):
            raise ConflictError("A dispatched one-time schedule cannot be edited; create a new schedule")

    if "title" in data:
        data["title"] = data["title"].strip()
        if not data["title"]:
            raise ValidationFailed("Workflow name must not be blank")
    if "instructions" in data:
        data["instructions"] = data["instructions"].strip()
        if not data["instructions"]:
            raise ValidationFailed("Workflow instructions must not be blank")
    if "platform_key" in data and get_adapter_class(data["platform_key"]) is None:
        raise ValidationFailed(f"Unknown platform '{data['platform_key']}'")
    if data.get("browser_session_id"):
        session = await db.get(BrowserSession, data["browser_session_id"])
        if session is None or session.organization_id != schedule.organization_id:
            raise NotFoundError("Browser session not found")
        if session.status == "expired":
            raise ConflictError("The selected browser session has expired; reconnect it first")
    if "config" in data:
        config = TaskConfig.model_validate(data["config"])
        if not config.allowed_domains:
            raise ValidationFailed("Scheduled workflows require at least one allowed domain")
        if config.approval_mode == "never":
            raise ValidationFailed("Scheduled workflows cannot disable action approval")
        data["config"] = config.model_dump()
        data["config"]["agent_mode"] = "generic"
    if "run_at" in data:
        run_at = data.pop("run_at")
        next_run_at = _utc(run_at)
        if next_run_at <= utcnow():
            raise ValidationFailed("Choose a future run time")
        data["next_run_at"] = next_run_at

    if data.get("enabled") is True:
        settings = get_settings()
        active_count = (
            await db.execute(
                select(func.count(TaskSchedule.id)).where(
                    TaskSchedule.organization_id == schedule.organization_id,
                    TaskSchedule.enabled.is_(True),
                    TaskSchedule.id != schedule.id,
                )
            )
        ).scalar_one()
        if active_count >= settings.max_scheduled_workflows_per_org:
            raise LimitExceededError(
                f"This organization may have at most {settings.max_scheduled_workflows_per_org} enabled schedules"
            )
        if not schedule.repeat_interval_minutes and schedule.last_task_id:
            raise ConflictError("A completed one-time schedule cannot be re-enabled; create a new schedule")
        if "next_run_at" not in data and _utc(schedule.next_run_at) <= utcnow():
            data["next_run_at"] = utcnow() + timedelta(seconds=1)
        data["last_error"] = ""

    for key, value in data.items():
        setattr(schedule, key, value)
    await emit(
        db,
        "schedule.updated",
        schedule.organization_id,
        f"Scheduled workflow '{schedule.title}' updated",
        payload={"schedule_id": str(schedule.id), "enabled": schedule.enabled},
    )
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def delete_schedule(db: AsyncSession, schedule: TaskSchedule) -> None:
    schedule_id, org_id, title = schedule.id, schedule.organization_id, schedule.title
    await db.delete(schedule)
    await emit(
        db,
        "schedule.deleted",
        org_id,
        f"Scheduled workflow '{title}' deleted",
        payload={"schedule_id": str(schedule_id)},
    )
    await db.commit()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def schedule_config_for_dispatch(schedule: TaskSchedule, org: Any) -> dict[str, Any]:
    """Revalidate stored config and apply current organization/server ceilings."""
    try:
        config = TaskConfig.model_validate(schedule.config or {}).model_dump()
    except ValueError as exc:
        raise ValidationFailed(f"Schedule configuration is invalid: {exc}") from exc
    if not config["allowed_domains"]:
        raise ValidationFailed("Schedule has no allowed domains")
    if config["approval_mode"] == "never":
        raise ValidationFailed("Scheduled workflows cannot disable action approval")
    config["agent_mode"] = "generic"
    limits = effective_limits(org)
    settings = get_settings()
    config["max_retries"] = min(
        int(config.get("max_retries", settings.max_retries_default)), limits["max_retries"]
    )
    config["generation_timeout_seconds"] = min(
        int(config.get("generation_timeout_seconds", settings.generation_timeout_seconds)),
        limits["generation_timeout_seconds"],
    )
    config["max_task_duration_seconds"] = limits["max_task_duration_seconds"]
    config["max_file_size_mb"] = limits["max_file_size_mb"]
    return config
