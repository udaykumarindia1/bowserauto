"""Generic browser-agent guardrails tested with in-memory model responses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent.agent import BrowserAgent
from app.agent.llm import LLMMessage, LLMResponse, LLMUnavailable
from app.agent.tools import ToolContext, ToolRegistry
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


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _context() -> ToolContext:
    return ToolContext(
        driver=None,  # _decide only builds a prompt; no browser is launched in this test.
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
