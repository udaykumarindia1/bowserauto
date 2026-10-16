"""Scheduled browser workflows require a domain scope and dispatch idempotently."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.database import session_scope
from app.models import Task, TaskSchedule
from app.workers.main import Worker
from app.workers.schedules import dispatch_due_schedules
from tests.conftest import ApiClient

pytestmark = pytest.mark.asyncio


def _payload(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "title": "Daily public status check",
        "kind": "research",
        "instructions": "Read the public status page and summarize current incidents.",
        "platform_key": "generic",
        "priority": 5,
        "run_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "repeat_interval_minutes": 60,
        "config": {
            "agent_mode": "generic",
            "approval_mode": "confirm_writes",
            "allowed_domains": ["status.example.com"],
        },
    }
    result.update(overrides)
    return result


async def test_scheduled_workflow_requires_domains_and_keeps_approval(client: ApiClient) -> None:
    no_domains = await client.post("/api/schedules", json=_payload(config={"allowed_domains": []}))
    assert no_domains.status_code == 422

    no_approval = await client.post(
        "/api/schedules",
        json=_payload(config={"allowed_domains": ["example.com"], "approval_mode": "never"}),
    )
    assert no_approval.status_code == 422

    valid = await client.post("/api/schedules", json=_payload())
    assert valid.status_code == 201, valid.text
    data = valid.json()
    assert data["config"]["agent_mode"] == "generic"
    assert data["config"]["allowed_domains"] == ["status.example.com"]
    assert data["config"]["approval_mode"] == "confirm_writes"
    assert data["enabled"] is True


async def test_schedule_crud_is_tenant_scoped_and_respects_roles(
    client: ApiClient, other_client: ApiClient
) -> None:
    created = await client.post("/api/schedules", json=_payload(repeat_interval_minutes=None))
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]

    listing = await client.get("/api/schedules")
    assert listing.status_code == 200
    assert schedule_id in {row["id"] for row in listing.json()}
    assert (await other_client.get("/api/schedules")).json() == []

    paused = await client.patch(f"/api/schedules/{schedule_id}", json={"enabled": False})
    assert paused.status_code == 200 and paused.json()["enabled"] is False
    resumed = await client.patch(f"/api/schedules/{schedule_id}", json={"enabled": True})
    assert resumed.status_code == 200 and resumed.json()["enabled"] is True
    assert (await other_client.delete(f"/api/schedules/{schedule_id}")).status_code == 404
    assert (await client.delete(f"/api/schedules/{schedule_id}")).status_code == 204


async def test_due_schedule_dispatches_once_with_allowlist_and_interval(
    client: ApiClient, worker: Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_background_dispatch() -> int:
        return 0

    monkeypatch.setattr("app.workers.main.dispatch_due_schedules", no_background_dispatch)
    prior_capabilities = worker.capabilities
    worker.capabilities = []  # Keep the queued test task from launching a browser.
    created = await client.post("/api/schedules", json=_payload())
    assert created.status_code == 201, created.text
    schedule_id = uuid.UUID(created.json()["id"])
    async with session_scope() as db:
        schedule = await db.get(TaskSchedule, schedule_id)
        assert schedule is not None
        schedule.next_run_at = datetime.now(UTC) - timedelta(seconds=1)

    try:
        assert await dispatch_due_schedules() == 1
        assert await dispatch_due_schedules() == 0
        async with session_scope() as db:
            schedule = await db.get(TaskSchedule, schedule_id)
            assert schedule is not None
            assert schedule.enabled is True
            next_run = schedule.next_run_at
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=UTC)
            assert next_run > datetime.now(UTC)
            assert schedule.last_task_id is not None
            task_id = str(schedule.last_task_id)
            task = await db.get(Task, schedule.last_task_id)
            assert task is not None
            assert task.status == "queued"
            assert task.config["agent_mode"] == "generic"
            assert task.config["approval_mode"] == "confirm_writes"
            assert task.config["allowed_domains"] == ["status.example.com"]
        stopped = await client.post(f"/api/tasks/{task_id}/stop")
        assert stopped.status_code == 200
        await client.delete(f"/api/schedules/{schedule_id}")
    finally:
        worker.capabilities = prior_capabilities
