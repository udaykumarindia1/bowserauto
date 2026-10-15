"""Base adapter for chat assistants (ChatGPT, Gemini, Arena, ...).

Workflow: enter prompt → send → wait until the assistant stops streaming → extract the
answer → store it as a text file. The same executor pipeline is reused; the "download" is
the extracted answer written to the project folder.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, ClassVar

from app.browser.driver import DownloadResult
from app.core.errors import ElementNotFound, GenerationFailed, GenerationTimeout
from app.models import GenerationState
from app.platforms.base import AdapterContext, GenerationOutcome, PlatformAdapter, _exists


class ChatPlatformAdapter(PlatformAdapter):
    category = "chat"
    output_kind = "text"
    output_extension = ".md"
    supported_actions: ClassVar[list[str]] = ["ask", "upload_reference", "extract_answer"]
    capabilities: ClassVar[dict[str, Any]] = {"max_clip_seconds": 0, "reference_images": True, "negative_prompt": False, "text_output": True}
    stable_seconds: ClassVar[float] = 2.5  # answer unchanged for this long => complete
    status_patterns: ClassVar[dict[str, list[str]]] = {}
    error_patterns: ClassVar[list[str]] = [r"something went wrong", r"an error occurred", r"network error", r"too many requests", r"rate limit"]

    async def generate(self, ctx: AdapterContext, target_dir: Path) -> GenerationOutcome:  # type: ignore[override]
        started = time.monotonic()
        await self.ensure_ready(ctx)
        await ctx.control.checkpoint()
        await self.open_generation_tool(ctx)
        if ctx.scene.reference_files:
            await self.upload_references(ctx)
        previous = await self._last_answer(ctx)
        await self.enter_prompt(ctx)
        await ctx.screenshot("before_generation")
        await self.start_generation(ctx)
        await ctx.set_state(GenerationState.GENERATING, "waiting for the assistant")
        answer = await self.wait_for_answer(ctx, previous)
        await ctx.screenshot("after_generation")
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{ctx.scene.code}_answer{self.output_extension}"
        header = f"# {ctx.scene.title or ctx.scene.code}\n\n**Platform:** {self.name}\n\n**Prompt:**\n\n{ctx.scene.prompt}\n\n---\n\n"
        path.write_text(header + answer.strip() + "\n", encoding="utf-8")
        download = DownloadResult(path=path, suggested_filename=path.name, size_bytes=path.stat().st_size)
        await ctx.set_state(GenerationState.SUCCESS, "answer saved")
        return GenerationOutcome(download=download, output_kind="text", generation_seconds=time.monotonic() - started, details={"chars": len(answer)})

    async def start_generation(self, ctx: AdapterContext) -> None:
        spec = self.selector("send_button")
        await ctx.say("Sending the prompt")
        try:
            if spec and await _exists(ctx, spec, timeout_ms=2000):
                await ctx.driver.click(spec)
            else:
                await ctx.driver.press("Enter")
        except ElementNotFound:
            await ctx.driver.press("Enter")
        await asyncio.sleep(1.0)
        await self.check_blockers(ctx)

    async def _last_answer(self, ctx: AdapterContext) -> str:
        spec = self.selector("assistant_message")
        if not spec:
            return ""
        try:
            script = self.config.get("answer_script") or self.answer_script()
            if script:
                value = await ctx.driver.evaluate(script)
                return str(value or "")
            return await ctx.driver.extract_text(spec)
        except Exception:
            return ""

    def answer_script(self) -> str | None:
        return None

    async def wait_for_answer(self, ctx: AdapterContext, previous: str) -> str:
        deadline = time.monotonic() + ctx.generation_timeout_seconds
        stable_since: float | None = None
        last_text = ""
        while time.monotonic() < deadline:
            await ctx.control.checkpoint()
            text = await ctx.driver.page_text(20_000)
            if any(__import__("re").search(p, text, __import__("re").I) for p in self.error_patterns):
                raise GenerationFailed("The assistant reported an error")
            if await self.detect_human_check(ctx, text):
                from app.core.errors import HumanInterventionRequired

                raise HumanInterventionRequired("The assistant site is asking for human verification.")
            streaming = await _exists(ctx, self.selector("streaming_indicator"))
            answer = await self._last_answer(ctx)
            if answer and answer != previous and not streaming:
                if answer == last_text:
                    if stable_since is None:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= self.stable_seconds:
                        await ctx.say("Assistant finished responding")
                        return answer
                else:
                    stable_since = None
                    last_text = answer
            await ctx.control.sleep(1.0)
        raise GenerationTimeout("The assistant did not finish answering in time")
