"""API router aggregation."""

from fastapi import APIRouter

from app.api import (
    activity,
    assistant,
    auth,
    dashboard,
    files,
    notifications,
    platforms,
    projects,
    realtime,
    sessions,
    settings,
    tasks,
    templates,
)

api_router = APIRouter(prefix="/api")
for module in (auth, dashboard, projects, tasks, sessions, files, platforms, templates, activity, notifications, settings, assistant, realtime):
    api_router.include_router(module.router)

__all__ = ["api_router"]
