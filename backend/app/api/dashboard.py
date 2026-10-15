"""Dashboard + health endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.database import check_database
from app.core.deps import DB, CurrentAuth
from app.realtime.bus import get_bus
from app.schemas import ActivityLogOut, GeneratedFileOut, TaskOut
from app.services.dashboard_service import dashboard as build_dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=dict)
async def dashboard(auth: CurrentAuth, db: DB) -> dict:
    data = await build_dashboard(db, auth.organization)
    data["active_tasks"] = [TaskOut.model_validate(t) for t in data["active_tasks"]]
    data["recent_tasks"] = [TaskOut.model_validate(t) for t in data["recent_tasks"]]
    data["recent_activity"] = [ActivityLogOut.model_validate(a) for a in data["recent_activity"]]
    data["recent_files"] = [GeneratedFileOut.model_validate(f).model_dump(by_alias=True) for f in data["recent_files"]]
    return data


@router.get("/health", include_in_schema=True)
async def health() -> dict:
    settings = get_settings()
    db_ok = await check_database()
    from app.workers.main import get_worker

    worker = get_worker()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "bus": get_bus().kind,
        "environment": settings.app_env,
        "embedded_worker": worker.stats() if worker else None,
        "version": "1.0.0",
    }
