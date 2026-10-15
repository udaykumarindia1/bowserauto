"""Generic adapter for platforms without a dedicated adapter.

Strategy:

1. Heuristic selectors (prompt-looking text boxes, generate-looking buttons, download links).
2. When an LLM is configured, each failing step is delegated to the generic browser agent
   which inspects the DOM/accessibility tree/screenshot and decides what to do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from app.browser.driver import DownloadResult
from app.core.errors import DownloadFailed, ElementNotFound
from app.platforms.base import AdapterContext, PlatformAdapter


class GenericAdapter(PlatformAdapter):
    key = "generic"
    name = "Generic AI Browser Agent"
    category = "generic"
    description = "Works on any web-based generator by inspecting the page (DOM, accessibility tree, screenshot) and reasoning about the next action."
    requires_login = False
    verified = True
    notes = "Best results with an LLM configured in Settings. Without one, a rule-based fallback handles common prompt → generate → download layouts."
    capabilities: ClassVar[dict[str, Any]] = {"max_clip_seconds": 10, "aspect_ratios": ["16:9", "9:16", "1:1"], "reference_images": True, "negative_prompt": False}
    selectors: ClassVar[dict[str, Any]] = {
        "prompt_input": {
            "any": [
                {"placeholder": "/prompt|describe|imagine|what do you want|type your/i"},
                {"css": "textarea:not([readonly])"},
                {"role": "textbox", "name": "/prompt|describe|message|input/i"},
                {"css": "[contenteditable='true']"},
            ]
        },
        "upload": {"any": [{"css": "input[type=file]"}, {"role": "button", "name": "/upload|add image|reference|attach/i"}]},
        "generate_button": {"any": [{"role": "button", "name": "/^(generate|create|run|render|make video|submit|go)\\b/i"}, {"css": "button[type=submit]"}]},
        "download_button": {"any": [{"role": "link", "name": "/download/i"}, {"role": "button", "name": "/download/i"}, {"css": "a[download]"}]},
        "result_video": {"css": "video[src], video source[src]"},
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.base_url = (config or {}).get("base_url", "")  # type: ignore[misc]

    def start_url(self) -> str:
        return self.config.get("generation_url") or self.config.get("base_url") or ""

    async def ensure_ready(self, ctx: AdapterContext) -> None:
        url = self.start_url()
        if url:
            current = await ctx.driver.url()
            if not current.startswith(url.split("?")[0].rstrip("/")):
                await ctx.say(f"Opening {url}")
                await ctx.driver.goto(url)
                await ctx.driver.wait_for_load(state="domcontentloaded")
        elif (await ctx.driver.url()) in ("", "about:blank"):
            if ctx.run_agent:
                await ctx.run_agent("Navigate to the web platform that should be used for generation as described in the task instructions.", {"instructions": ctx.options.get("instructions", "")})
            else:
                raise ElementNotFound("No platform URL configured for the generic adapter. Set a start URL on the browser session or project.", retryable=False)
        await self.check_blockers(ctx)

    async def generate(self, ctx: AdapterContext, target_dir: Path):  # type: ignore[override]
        if ctx.run_agent and ctx.options.get("agent_mode") == "generic":
            return await self._generate_with_agent(ctx, target_dir)
        try:
            return await super().generate(ctx, target_dir)
        except (ElementNotFound, DownloadFailed) as exc:
            if ctx.run_agent:
                await ctx.say(f"Rule-based workflow failed ({str(exc)[:80]}); switching to the AI agent")
                return await self._generate_with_agent(ctx, target_dir)
            raise

    async def _generate_with_agent(self, ctx: AdapterContext, target_dir: Path):
        import time

        from app.platforms.base import GenerationOutcome

        assert ctx.run_agent is not None
        started = time.monotonic()
        goal = (
            "You are operating a web-based AI video generator. Complete one generation end to end: "
            "(1) if reference files are provided, upload them with the page's upload control; "
            "(2) enter the prompt exactly; (3) apply the requested duration/aspect ratio if such settings exist; "
            "(4) start the generation; (5) wait until the output is complete by observing the page (never guess); "
            "(6) download the generated video with browser.download; (7) finish with task.complete and include the download path. "
            "If the site asks you to log in, solve a CAPTCHA or verify your identity, call task.needs_human instead of attempting it."
        )
        info = await ctx.run_agent(goal, {"prompt": ctx.scene.prompt, "duration_seconds": ctx.scene.duration_seconds, "aspect_ratio": ctx.scene.aspect_ratio, "files": [str(f) for f in ctx.scene.reference_files]})
        path = info.get("download_path")
        if not path or not Path(path).exists():
            raise DownloadFailed("The agent finished without producing a downloaded file")
        download = DownloadResult(path=Path(path), suggested_filename=Path(path).name, size_bytes=Path(path).stat().st_size)
        return GenerationOutcome(download=download, output_kind=self.output_kind, generation_seconds=time.monotonic() - started, details={"mode": "agent"})
