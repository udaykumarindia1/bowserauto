"""Dashboard aggregates: counts, recent activity, estimated usage and worker health."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import ActivityLog, GeneratedFile, Organization, Project, Scene, Task, TaskStatus
from app.realtime.bus import get_bus
from app.services.file_service import storage_summary
from app.services.org_settings import effective_limits
from app.services.session_service import list_sessions


async def dashboard(db: AsyncSession, org: Organization) -> dict[str, Any]:
    org_id = org.id
    settings = get_settings()
    active_statuses = [s.value for s in TaskStatus if s.is_active]
    task_counts = dict(
        (
            await db.execute(
                select(Task.status, func.count(Task.id))
                .where(Task.organization_id == org_id)
                .group_by(Task.status)
            )
        ).all()
    )
    project_ids = (
        (
            await db.execute(
                select(Project.id).where(Project.organization_id == org_id, Project.status == "active")
            )
        )
        .scalars()
        .all()
    )
    storage_project_ids = (
        (await db.execute(select(Project.id).where(Project.organization_id == org_id))).scalars().all()
    )
    unscoped_task_ids = (
        (await db.execute(select(Task.id).where(Task.organization_id == org_id, Task.project_id.is_(None))))
        .scalars()
        .all()
    )
    scene_counts = dict(
        (
            await db.execute(
                select(Scene.status, func.count(Scene.id))
                .where(Scene.organization_id == org_id)
                .group_by(Scene.status)
            )
        ).all()
    )
    files_count, files_bytes = (
        await db.execute(
            select(func.count(GeneratedFile.id), func.coalesce(func.sum(GeneratedFile.size_bytes), 0)).where(
                GeneratedFile.organization_id == org_id
            )
        )
    ).one()
    sessions = await list_sessions(db, org_id)
    active_tasks = (
        (
            await db.execute(
                select(Task)
                .where(Task.organization_id == org_id, Task.status.in_(active_statuses))
                .order_by(Task.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    recent_tasks = (
        (
            await db.execute(
                select(Task).where(Task.organization_id == org_id).order_by(Task.created_at.desc()).limit(8)
            )
        )
        .scalars()
        .all()
    )
    recent_activity = (
        (
            await db.execute(
                select(ActivityLog)
                .where(ActivityLog.organization_id == org_id)
                .order_by(ActivityLog.created_at.desc())
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    recent_files = (
        (
            await db.execute(
                select(GeneratedFile)
                .where(GeneratedFile.organization_id == org_id)
                .order_by(GeneratedFile.created_at.desc())
                .limit(6)
            )
        )
        .scalars()
        .all()
    )
    since = datetime.now(UTC) - timedelta(days=7)
    gen_seconds = (
        await db.execute(
            select(func.coalesce(func.sum(GeneratedFile.generation_seconds), 0)).where(
                GeneratedFile.organization_id == org_id, GeneratedFile.created_at >= since
            )
        )
    ).scalar_one()
    completed_week = (
        await db.execute(
            select(func.count(GeneratedFile.id)).where(
                GeneratedFile.organization_id == org_id, GeneratedFile.created_at >= since
            )
        )
    ).scalar_one()
    failed_week = (
        await db.execute(
            select(func.count(Task.id)).where(
                Task.organization_id == org_id,
                Task.status == TaskStatus.failed.value,
                Task.created_at >= since,
            )
        )
    ).scalar_one()
    limits = effective_limits(org)
    storage = await asyncio.to_thread(
        storage_summary, list(storage_project_ids), list(unscoped_task_ids), limits["max_storage_mb"]
    )
    return {
        "tasks": {
            "active": sum(task_counts.get(s, 0) for s in active_statuses),
            "queued": task_counts.get(TaskStatus.queued.value, 0),
            "running": task_counts.get(TaskStatus.running.value, 0),
            "needs_attention": task_counts.get(TaskStatus.needs_attention.value, 0)
            + task_counts.get(TaskStatus.waiting_for_human.value, 0),
            "completed": task_counts.get(TaskStatus.completed.value, 0),
            "failed": task_counts.get(TaskStatus.failed.value, 0),
            "total": sum(task_counts.values()),
        },
        "projects": {"active": len(project_ids)},
        "scenes": {
            "total": sum(scene_counts.values()),
            "completed": scene_counts.get("completed", 0),
            "failed": scene_counts.get("failed", 0),
            "pending": sum(
                v
                for k, v in scene_counts.items()
                if k in ("queued", "generating", "processing", "downloading")
            ),
        },
        "files": {"count": int(files_count), "bytes": int(files_bytes)},
        "sessions": {
            "total": len(sessions),
            "ready": sum(1 for s in sessions if s.status in ("ready", "in_use")),
            "logged_in": sum(1 for s in sessions if s.login_status == "logged_in"),
            "items": [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "status": s.status,
                    "platform_key": s.platform_key,
                    "login_status": s.login_status,
                    "current_url": s.current_url,
                }
                for s in sessions[:8]
            ],
        },
        "usage_week": {
            "generation_seconds": float(gen_seconds or 0),
            "generation_minutes": round(float(gen_seconds or 0) / 60, 1),
            "clips_generated": int(completed_week),
            "tasks_failed": int(failed_week),
        },
        "limits": limits,
        "storage": storage,
        "active_tasks": active_tasks,
        "recent_tasks": recent_tasks,
        "recent_activity": recent_activity,
        "recent_files": recent_files,
        "system": {
            "bus": get_bus().kind,
            "environment": settings.app_env,
            "mock_platform": settings.app_env != "production",
        },
    }


def estimate_task(
    scene_count: int, clip_seconds: float, *, platform_key: str, limits: dict[str, Any]
) -> dict[str, Any]:
    """Rough usage estimate shown before starting a task (no provider pricing is assumed)."""
    per_scene = 60 if platform_key == "mock" else 240  # typical wall-clock seconds per clip
    if platform_key == "mock":
        per_scene = 20
    total = scene_count * per_scene
    warnings = []
    if scene_count > limits.get("max_scenes_per_task", 200):
        warnings.append(f"Exceeds the limit of {limits['max_scenes_per_task']} scenes per task")
    if total > limits.get("max_task_duration_seconds", 21600):
        warnings.append(
            "Estimated duration exceeds the maximum task duration; consider splitting the project"
        )
    return {
        "scenes": scene_count,
        "output_seconds": scene_count * clip_seconds,
        "estimated_wall_clock_seconds": total,
        "estimated_minutes": round(total / 60, 1),
        "platform_credits_note": "Platform credits are consumed per generation on the provider side; check your plan.",
        "warnings": warnings,
    }
