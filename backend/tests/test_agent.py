"""Generic browser-agent guardrails tested with in-memory model responses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent.agent import BrowserAgent
from app.agent.llm import LLMMessage, LLMResponse, LLMUnavailable
from app.agent.tools import ToolContext, ToolRegistry, build_default_registry
from app.browser.driver import PageSnapshot
from app.queue.control import ControlToken


class CaptureLLM:
    name = "mock"
    model = "mock-model"

    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[LLMMessage] = []

    async def complete(self, messages: list[LLMMessage], **_kwargs: Any) -> LLMResponse:
        self.messages = messages
        return LLMResponse(self.response, self.model)


class HistoryDriver:
    def __init__(self) -> None:
        self.current_url = "https://start.example.test/"
        self.history: list[str] = []

    async def url(self) -> str:
        return self.current_url

    async def goto(self, url: str) -> None:
        self.history.append(self.current_url)
        self.current_url = url

    async def back(self) -> None:
        if self.history:
            self.current_url = self.history.pop()

    async def title(self) -> str:
        return "Test page"


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _context(driver=None) -> ToolContext:  # noqa: ANN001
    return ToolContext(
        driver=driver,  # _decide-only tests do not launch a browser.
        control=ControlToken(),
        project_dir=Path("/tmp"),
        downloads_dir=Path("/tmp"),
        log=_noop,
        screenshot=_noop,
    )


@pytest.mark.parametrize(("use_vision", "expected_images"), [(False, []), (True, ["c2NyZWVuc2hvdA=="])])
async def test_agent_vision_setting_controls_screenshot_payload(
    use_vision: bool, expected_images: list[str]
) -> None:
    llm = CaptureLLM('{"tool":"task.complete","args":{},"reasoning":"done"}')
    agent = BrowserAgent(_context(), llm=llm, registry=ToolRegistry(), use_vision=use_vision)
    snapshot = PageSnapshot(
        url="https://example.com",
        title="Example",
        text="Visible page text",
        screenshot_b64="c2NyZWVuc2hvdA==",
    )

    decision = await agent._decide("Read the page", {}, snapshot, [])

    assert decision["tool"] == "task.complete"
    assert llm.messages[-1].images == expected_images


@pytest.mark.parametrize(
    "response",
    [
        '{"tool":"browser.type","args":"not-an-object"}',
        '{"tool":42,"args":{}}',
        '{"tool":"browser.click","args":{},"reasoning":["not", "text"]}',
    ],
)
async def test_agent_rejects_malformed_tool_decisions(response: str) -> None:
    llm = CaptureLLM(response)
    agent = BrowserAgent(_context(), llm=llm, registry=ToolRegistry())

    with pytest.raises(LLMUnavailable, match="invalid tool-call object"):
        await agent._decide("Read the page", {}, PageSnapshot("https://example.com", "Example", ""), [])


async def test_undo_only_compensates_an_unchanged_explicit_navigation() -> None:
    driver = HistoryDriver()
    ctx = _context(driver)
    registry = build_default_registry()

    opened = await registry.execute(ctx, "browser.open", {"url": "next.example.test"})
    assert opened.ok and driver.current_url == "https://next.example.test"
    undone = await registry.execute(ctx, "task.undo", {})
    assert undone.ok and driver.current_url == "https://start.example.test/"

    await registry.execute(ctx, "browser.open", {"url": "another.example.test"})
    driver.current_url = "https://changed-after-open.example.test"
    refused = await registry.execute(ctx, "task.undo", {})
    assert not refused.ok
    assert "Page state changed" in refused.summary
    assert driver.current_url == "https://changed-after-open.example.test"
