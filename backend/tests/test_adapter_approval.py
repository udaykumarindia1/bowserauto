"""Adapter-native BrowserDriver actions obey the task approval policy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.tools import sanitize_action_args
from app.browser.policy_driver import ApprovalGuardDriver
from app.core.errors import TaskStopped
from tests.conftest import ApiClient

pytestmark = pytest.mark.asyncio


async def test_confirm_writes_gates_adapter_mutations_but_not_navigation() -> None:
    driver = SimpleNamespace(
        goto=AsyncMock(),
        type=AsyncMock(),
        url=AsyncMock(return_value="https://example.test/"),
    )
    reviewed: list[tuple[str, dict]] = []

    async def approve(name: str, args: dict) -> bool:
        reviewed.append((name, sanitize_action_args(args)))
        return True

    guarded = ApprovalGuardDriver(driver, approval_mode="confirm_writes", approval_handler=approve)
    await guarded.goto("https://example.test/path?token=secret")
    assert reviewed == []

    await guarded.type({"placeholder": "Prompt"}, "a private prompt")
    assert reviewed[0][0] == "adapter.browser.type"
    assert reviewed[0][1]["text_to_type"].startswith("[hidden for privacy")
    assert reviewed[0][1]["character_count"] == len("a private prompt")
    driver.type.assert_awaited_once()


async def test_confirm_all_gates_navigation_and_rejection_prevents_browser_action() -> None:
    driver = SimpleNamespace(goto=AsyncMock(), click=AsyncMock(), scroll=AsyncMock())
    reviewed: list[str] = []

    async def approve(name: str, _args: dict) -> bool:
        reviewed.append(name)
        return name != "adapter.browser.click"

    guarded = ApprovalGuardDriver(driver, approval_mode="confirm_all", approval_handler=approve)
    await guarded.goto("https://example.test/?session=private")
    await guarded.scroll("down", 100)
    assert reviewed == ["adapter.browser.goto", "adapter.browser.scroll"]
    with pytest.raises(TaskStopped):
        await guarded.click({"role": "button", "name": "Delete"})
    driver.goto.assert_awaited_once()
    driver.click.assert_not_awaited()


async def test_never_mode_does_not_require_an_approval_handler() -> None:
    driver = SimpleNamespace(click=AsyncMock())
    guarded = ApprovalGuardDriver(driver, approval_mode="never", approval_handler=None)
    await guarded.click("button.run")
    driver.click.assert_awaited_once_with("button.run")


async def test_authenticated_skill_catalog_exposes_real_tools_and_policy(client: ApiClient) -> None:
    catalog = await client.get("/api/assistant/skills")
    assert catalog.status_code == 200, catalog.text
    tools = {item["name"]: item for item in catalog.json()}
    assert "browser.click" in tools
    assert "filesystem.list" in tools
    assert "task.undo" in tools
    assert tools["filesystem.list"]["risk"] == "read-only"
    assert tools["browser.click"]["approval"] == "confirm_writes or confirm_all"
    assert (await client.get("/api/assistant/skills", csrf=False)).status_code == 200


async def test_adapter_task_waits_for_approval_before_entering_prompt(client: ApiClient) -> None:
    project = await client.create_project(analyze=True)
    task = await client.create_task(
        project["id"], config={"approval_mode": "confirm_writes", "agent_mode": "adapter"}
    )
    pending = await client.wait_until(
        task["id"],
        lambda value: (
            value["status"] == "waiting_for_human" and value.get("attention", {}).get("kind") == "approval"
        ),
    )
    action = pending["attention"]["pending_action"]
    assert action["tool"] == "adapter.browser.type"
    assert action["args"]["text_to_type"].startswith("[hidden for privacy")

    rejected = await client.post(f"/api/tasks/{task['id']}/reject")
    assert rejected.status_code == 200, rejected.text
    terminal = await client.wait_task(task["id"], {"cancelled"})
    assert terminal["status"] == "cancelled"
