"""Generic tool-based browser agent.

Observe (DOM snapshot + optional screenshot) → decide (LLM or rule-based planner) → act
(tool) → repeat. Reasoning shown to the user is a short, high-level justification for the
chosen action – never raw chain-of-thought.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent.heuristic import HeuristicPlanner
from app.agent.llm import LLMClient, LLMMessage, LLMUnavailable
from app.agent.tools import ToolContext, ToolRegistry, ToolResult, build_default_registry
from app.browser.driver import PageSnapshot
from app.core.errors import HumanInterventionRequired
from app.core.logging import redact

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert browser automation agent operating a real web browser on behalf of a user.
You receive the current page state (URL, interactive elements with indexes, visible text) and must choose exactly one tool call per step.

Rules:
1. Work step by step. Prefer element indexes from the snapshot. After every action you will receive the new page state.
2. Never guess that something finished: wait for real evidence on the page (status text, download button, video element).
3. Never attempt to bypass, solve or automate CAPTCHAs, MFA/2FA, phone/email verification, consent walls, paywalls or any security control. If you encounter one, or if the site requires logging in, call task.needs_human with a clear reason.
4. Do not enter passwords or payment details. Do not navigate to unrelated sites.
5. Keep "reasoning" to one short sentence describing what you see and what you are doing (e.g. "Generation button detected, starting generation."). Do not reveal internal deliberation.
6. When the goal is achieved call task.complete (include download_path when you downloaded a file). If it is impossible call task.fail.
7. Reply with ONLY a JSON object: {"reasoning": "...", "tool": "tool.name", "args": {...}}.
"""


@dataclass
class AgentStep:
    index: int
    tool: str
    args: dict[str, Any]
    reasoning: str
    result: ToolResult
    duration_ms: int


@dataclass
class AgentResult:
    status: str  # completed | failed | needs_human | max_steps | stopped | retry
    message: str
    output: dict[str, Any] = field(default_factory=dict)
    steps: list[AgentStep] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class BrowserAgent:
    def __init__(
        self,
        ctx: ToolContext,
        *,
        llm: LLMClient | None,
        registry: ToolRegistry | None = None,
        max_steps: int = 40,
        use_vision: bool = True,
        on_step: Any = None,
    ) -> None:
        self.ctx = ctx
        self.llm = llm
        self.registry = registry or build_default_registry()
        self.max_steps = max_steps
        self.use_vision = use_vision and llm is not None
        self.on_step = on_step
        self.planner = HeuristicPlanner()

    async def run(self, goal: str, inputs: dict[str, Any] | None = None) -> AgentResult:
        inputs = inputs or {}
        history: list[AgentStep] = []
        consecutive_failures = 0
        last_signature: tuple[str, str] | None = None
        repeats = 0
        for step_index in range(1, self.max_steps + 1):
            await self.ctx.control.checkpoint()
            snapshot = await self.ctx.driver.snapshot(include_screenshot=self.use_vision)
            try:
                decision = await self._decide(goal, inputs, snapshot, history)
            except LLMUnavailable as exc:
                await self.ctx.log(
                    f"AI provider unavailable: {exc}", level="warning", event_type="agent.thought"
                )
                decision = self.planner.plan(goal, inputs, snapshot, history)
            tool_name = decision.get("tool", "")
            args = decision.get("args") or {}
            reasoning = str(redact(str(decision.get("reasoning", ""))))[:300]
            signature = (tool_name, json.dumps(args, sort_keys=True, default=str))
            repeats = repeats + 1 if signature == last_signature else 0
            last_signature = signature
            if repeats >= 3:
                reasoning = "Same action repeated without progress; stopping."
                result = ToolResult(
                    False, "The agent is stuck repeating the same action", done=True, status="failed"
                )
                tool_name = "task.fail"
            else:
                started = time.perf_counter()
                result = await self.registry.execute(self.ctx, tool_name, args)
                result.data.setdefault("duration_ms", int((time.perf_counter() - started) * 1000))
            step = AgentStep(
                step_index, tool_name, redact(args), reasoning, result, int(result.data.get("duration_ms", 0))
            )
            history.append(step)
            if self.on_step:
                await self.on_step(step)
            consecutive_failures = 0 if result.ok else consecutive_failures + 1
            if result.done:
                status = result.status or ("completed" if result.ok else "failed")
                if status == "needs_human":
                    raise HumanInterventionRequired(result.summary)
                return AgentResult(status, result.summary, {**result.data, **self.ctx.state}, history)
            if consecutive_failures >= 6:
                return AgentResult(
                    "failed", "Too many consecutive failed actions", dict(self.ctx.state), history
                )
        return AgentResult(
            "max_steps",
            f"Stopped after {self.max_steps} steps without completing the goal",
            dict(self.ctx.state),
            history,
        )

    async def _decide(
        self, goal: str, inputs: dict[str, Any], snapshot: PageSnapshot, history: list[AgentStep]
    ) -> dict[str, Any]:
        if self.llm is None:
            return self.planner.plan(goal, inputs, snapshot, history)
        history_lines = []
        for step in history[-12:]:
            outcome = "ok" if step.result.ok else "FAILED"
            history_lines.append(
                f"{step.index}. {step.tool}({json.dumps(step.args, default=str)[:200]}) -> {outcome}: {step.result.summary[:200]}"
            )
        user_prompt = (
            f"GOAL:\n{goal}\n\nINPUTS (use exactly when relevant):\n{json.dumps(inputs, default=str)[:4000]}\n\n"
            f"AVAILABLE TOOLS:\n{self.registry.describe()}\n\n"
            f"PREVIOUS STEPS:\n" + ("\n".join(history_lines) if history_lines else "(none)") + "\n\n"
            f"CURRENT PAGE STATE:\n{snapshot.as_prompt()}\n\nRespond with the JSON for the next single tool call."
        )
        images = [snapshot.screenshot_b64] if (self.use_vision and snapshot.screenshot_b64) else []
        response = await self.llm.complete(
            [LLMMessage("system", SYSTEM_PROMPT), LLMMessage("user", user_prompt, images=images)],
            json_mode=True,
            max_tokens=800,
            temperature=0.1,
        )
        try:
            decision = response.json()
        except ValueError as exc:
            logger.warning("LLM returned non-JSON decision: provider model=%s", response.model)
            # The caller falls back to the deterministic planner on LLMUnavailable. Do not spend
            # browser time on a blind delay when the model response cannot be interpreted.
            raise LLMUnavailable("The model did not return a valid tool-call JSON object") from exc
        if (
            not isinstance(decision, dict)
            or not isinstance(decision.get("tool"), str)
            or not decision["tool"].strip()
            or not isinstance(decision.get("args", {}), dict)
            or not isinstance(decision.get("reasoning", ""), str)
        ):
            logger.warning("LLM returned an invalid tool-call object: provider model=%s", response.model)
            raise LLMUnavailable("The model returned an invalid tool-call object")
        return decision
