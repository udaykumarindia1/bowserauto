"""Approval controls, privacy-safe action previews, and per-task domain policy."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.tools import (
    ToolContext,
    _requires_action_approval,
    build_default_registry,
    sanitize_action_args,
)
from app.core.config import Settings
from app.core.database import session_scope
from app.core.errors import AutomationError, TaskStopped
from app.core.url_safety import BrowserURLPolicy, UnsafeDestination
from app.models import Task, TaskControl, TaskStatus
from app.queue.control import ControlToken
from app.workers.executor import TaskExecutor
from app.workers.main import TaskRun, Worker
from tests.conftest import ApiClient

pytestmark = pytest.mark.asyncio


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _tool_context(tmp_path: Path, *, approval_mode: str, approval_handler=None) -> ToolContext:  # noqa: ANN001
    return ToolContext(
        driver=None,  # filesystem tools do not use the browser driver
        control=ControlToken(),
        project_dir=tmp_path,
        downloads_dir=tmp_path / "downloads",
        log=_noop,
        screenshot=_noop,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
    )


async def _draft_browser_task(client: ApiClient, config: dict[str, Any] | None = None) -> dict[str, Any]:
    response = await client.post(
        "/api/tasks",
        json={
            "kind": "browser_workflow",
            "instructions": "Read the title of the allowed page.",
            "config": config or {},
            "start": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _set_pending_approval(task_id: str, worker_id: str = "test-worker") -> None:
    async with session_scope() as db:
        task = await db.get(Task, uuid.UUID(task_id))
        assert task is not None
        task.status = TaskStatus.waiting_for_human.value
        task.worker_id = worker_id
        task.attention = {
            "kind": "approval",
            "reason": "Review the proposed browser action.",
            "pending_action": {"tool": "browser.click", "args": {"text": "Save"}},
            "options": ["approve", "reject", "stop"],
        }


async def _cleanup_test_task(task_id: str) -> None:
    async with session_scope() as db:
        task = await db.get(Task, uuid.UUID(task_id))
        if task is not None:
            task.status = TaskStatus.cancelled.value
            task.worker_id = None
            task.control = TaskControl.none.value
            task.attention = {}


async def test_task_config_defaults_and_normalizes_domain_allowlist(client: ApiClient) -> None:
    task = await _draft_browser_task(
        client,
        {"allowed_domains": ["Example.COM.", "example.com", "bücher.example"]},
    )
    assert task["config"]["approval_mode"] == "confirm_writes"
    assert task["config"]["allowed_domains"] == ["example.com", "xn--bcher-kva.example"]

    invalid = await client.post(
        "/api/tasks",
        json={
            "kind": "browser_workflow",
            "instructions": "Read a page.",
            "config": {"allowed_domains": ["https://example.com/path", "127.0.0.1"]},
            "start": False,
        },
    )
    assert invalid.status_code == 422


async def test_browser_url_policy_enforces_task_domain_scope() -> None:
    policy = BrowserURLPolicy(Settings(app_env="test"))
    policy.set_allowed_domains(["example.com"])
    await policy.check("https://example.com/path")
    await policy.check("https://docs.example.com/path")
    with pytest.raises(UnsafeDestination, match="outside this task"):
        await policy.check("https://unrelated.example/path")


async def test_write_tool_is_gated_before_it_mutates_filesystem(tmp_path: Path) -> None:
    registry = build_default_registry()
    content = "private-form-value"
    context = _tool_context(tmp_path, approval_mode="confirm_writes")

    with pytest.raises(AutomationError, match="no approval controller"):
        await registry.execute(context, "filesystem.save_file", {"path": "draft.txt", "content": content})
    assert not (tmp_path / "draft.txt").exists()

    seen: list[tuple[str, dict[str, Any]]] = []

    async def approve(name: str, args: dict[str, Any]) -> bool:
        seen.append((name, args))
        return True

    context.approval_handler = approve
    result = await registry.execute(
        context, "filesystem.save_file", {"path": "draft.txt", "content": content}
    )
    assert result.ok
    assert (tmp_path / "draft.txt").read_text(encoding="utf-8") == content
    assert seen == [("filesystem.save_file", {"path": "draft.txt", "content": content})]

    preview = sanitize_action_args({"path": "draft.txt", "content": content, "text_to_type": content})
    assert content not in str(preview)
    assert "hidden for privacy" in preview["content"]
    assert "hidden for privacy" in preview["text_to_type"]


async def test_approval_modes_cover_registered_tool_names() -> None:
    assert _requires_action_approval("browser.click", "confirm_writes")
    assert _requires_action_approval("filesystem.save_file", "confirm_writes")
    assert not _requires_action_approval("filesystem.list", "confirm_writes")
    assert not _requires_action_approval("browser.get_page_text", "confirm_all")
    assert not _requires_action_approval("browser.get_accessibility_tree", "confirm_all")
    assert not _requires_action_approval("filesystem.list", "confirm_all")
    assert _requires_action_approval("browser.open", "confirm_all")
    assert _requires_action_approval("filesystem.save_file", "confirm_all")
    assert _requires_action_approval("task.undo", "confirm_all")
    assert not _requires_action_approval("task.undo", "confirm_writes")
    assert not _requires_action_approval("browser.click", "never")


async def test_control_token_resolves_approval_once_and_stop_wakes_waiter() -> None:
    token = ControlToken()
    token.begin_approval()
    approved = asyncio.create_task(token.wait_for_approval())
    token.resolve_approval(True)
    assert await approved is True

    token.begin_approval()
    rejected = asyncio.create_task(token.wait_for_approval())
    token.resolve_approval(False)
    assert await rejected is False

    token.begin_approval()
    stopped = asyncio.create_task(token.wait_for_approval())
    token.request_stop()
    with pytest.raises(TaskStopped):
        await stopped


async def test_executor_publishes_a_private_approval_preview_and_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid.uuid4()
    executor = TaskExecutor(SimpleNamespace(id="approval-test-worker"), task_id, ControlToken())
    executor.task = SimpleNamespace(
        id=task_id,
        organization_id=uuid.uuid4(),
        title="Sensitive form submission",
        config={"approval_mode": "confirm_writes"},
    )
    pending_attention: dict[str, Any] = {}
    waiting = asyncio.Event()

    async def set_task(**values: Any) -> None:
        nonlocal pending_attention
        pending_attention = values.get("attention", {})
        if values.get("status") == TaskStatus.waiting_for_human.value:
            waiting.set()

    async def no_log(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(executor, "_set_task", set_task)
    monkeypatch.setattr(executor, "_log", no_log)
    monkeypatch.setattr("app.workers.executor.notify", no_log)

    request = asyncio.create_task(
        executor._request_tool_approval(
            "browser.type", {"selector": "input[name=password]", "text_to_type": "very-private-value"}
        )
    )
    await waiting.wait()
    assert pending_attention["kind"] == "approval"
    assert "very-private-value" not in str(pending_attention)
    assert pending_attention["pending_action"]["args"]["text_to_type"].startswith("[hidden for privacy;")
    assert not request.done()

    executor.control.resolve_approval(True)
    assert await request is True


async def test_approve_and_reject_api_controls_are_one_shot(client: ApiClient) -> None:
    for decision in ("approve", "reject"):
        task = await _draft_browser_task(client)
        await _set_pending_approval(task["id"])
        config_change = await client.patch(
            f"/api/tasks/{task['id']}", json={"config": {"approval_mode": "never"}}
        )
        assert config_change.status_code == 409

        response = await client.post(f"/api/tasks/{task['id']}/{decision}")
        assert response.status_code == 200, response.text
        assert response.json()["control"] == f"{decision}_action"
        duplicate = await client.post(f"/api/tasks/{task['id']}/{decision}")
        assert duplicate.status_code == 409, duplicate.text
        await _cleanup_test_task(task["id"])


async def test_approval_control_reaches_worker_and_unblocks_waiter(client: ApiClient, worker: Worker) -> None:
    task = await _draft_browser_task(client)
    await _set_pending_approval(task["id"], worker.id)
    token = ControlToken()
    token.begin_approval()
    waiter = asyncio.create_task(token.wait_for_approval())
    keepalive = asyncio.create_task(asyncio.Event().wait())
    task_id = uuid.UUID(task["id"])
    run = TaskRun(
        task_id=task_id,
        org_id=uuid.UUID(client.organization["id"]),
        token=token,
        executor=SimpleNamespace(host=None),
        future=keepalive,
    )
    worker.runs[task_id] = run
    try:
        response = await client.post(f"/api/tasks/{task['id']}/approve")
        assert response.status_code == 200, response.text
        assert await asyncio.wait_for(waiter, timeout=5) is True
        resumed = await client.wait_until(task["id"], lambda row: row["status"] == "running", timeout=5)
        assert resumed["attention"] == {}
    finally:
        worker.runs.pop(task_id, None)
        keepalive.cancel()
        waiter.cancel()
        await asyncio.gather(keepalive, waiter, return_exceptions=True)
        await _cleanup_test_task(task["id"])


async def test_approval_api_rejects_tasks_without_pending_approval(client: ApiClient) -> None:
    task = await _draft_browser_task(client)
    response = await client.post(f"/api/tasks/{task['id']}/approve")
    assert response.status_code == 409
    await _cleanup_test_task(task["id"])
