"""Adapter for the built-in MockVid Studio platform (development & tests)."""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlencode

from app.core.config import get_settings
from app.platforms.base import AdapterContext, PlatformAdapter


class MockPlatformAdapter(PlatformAdapter):
    key = "mock"
    name = "Mock Video Generator"
    category = "video"
    description = (
        "Built-in demo platform that simulates generate → wait → download without external AI services."
    )
    requires_login = False
    verified = True
    notes = "Use it to test queues, the live browser view, takeover, retries and downloads for free."
    capabilities: ClassVar[dict[str, Any]] = {
        "max_clip_seconds": 10,
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "reference_images": True,
        "negative_prompt": False,
        "durations": [5, 8, 10],
    }
    selectors: ClassVar[dict[str, Any]] = {
        "prompt_input": {
            "any": [{"testid": "prompt"}, {"placeholder": "/describe the video/i"}, {"css": "textarea"}]
        },
        "upload": {"css": "input[type=file]"},
        "generate_button": {"any": [{"testid": "generate"}, {"role": "button", "name": "/^generate$/i"}]},
        "download_button": {"any": [{"role": "link", "name": "/download video/i"}, {"css": "a#download"}]},
        "result_video": {"css": "#result:not(.hidden) video"},
        "error_indicator": {"css": ".status-failed"},
        "human_check": {"css": "#humanCheck:not(.hidden)"},
        "login_indicator": {"css": "#loginView:not(.hidden)"},
        "logged_in_indicator": {"css": "#appView:not(.hidden)"},
        "duration_option": {"css": "#durations [data-duration='{value}']"},
        "aspect_ratio_option": {"css": "#ratios [data-ratio='{value}']"},
    }
    status_patterns: ClassVar[dict[str, list[str]]] = {
        "QUEUED": [r"status:\s*queued"],
        "GENERATING": [r"generating your video"],
        "PROCESSING": [r"status:\s*processing"],
        "COMPLETED": [r"generation complete"],
    }
    error_patterns: ClassVar[list[str]] = [r"generation failed[^\n]*"]
    human_check_patterns: ClassVar[list[str]] = [r"security check required", r"verify you are human"]
    poll_interval_seconds = 1.0

    def start_url(self) -> str:
        base = self.config.get("base_url") or get_settings().effective_mock_platform_url
        params = {}
        opts = self.config.get("mock_options") or {}
        if opts.get("speed"):
            params["speed"] = opts["speed"]
        if opts.get("fail_rate"):
            params["fail_rate"] = opts["fail_rate"]
        if opts.get("human_check_attempt"):
            params["human_check"] = opts["human_check_attempt"]
        if opts.get("login_required"):
            params["login"] = 1
        return f"{base}?{urlencode(params)}" if params else base

    @property
    def requires_login_now(self) -> bool:
        return bool((self.config.get("mock_options") or {}).get("login_required"))

    async def detect_login_required(
        self, ctx: AdapterContext, text: str | None = None, url: str | None = None
    ) -> bool:  # type: ignore[override]
        try:
            return await ctx.driver.exists(self.selectors["login_indicator"])
        except Exception:
            return False

    async def ensure_ready(self, ctx: AdapterContext) -> None:
        url = self.start_url()
        current = (await ctx.driver.url() or "").split("#", 1)[0]
        # The query string carries the simulation options (speed, fail_rate, login...), so the
        # page is reloaded whenever the effective URL differs - not just when the origin differs.
        if current.rstrip("/?") != url.rstrip("/?"):
            await ctx.say("Opening Mock Video Generator")
            await ctx.driver.goto(url)
        await ctx.driver.wait_for(
            {"any": [self.selectors["prompt_input"], self.selectors["login_indicator"]]}, timeout_ms=15_000
        )
        await self.check_blockers(ctx)

    async def check_blockers(self, ctx: AdapterContext) -> None:
        from app.core.errors import HumanInterventionRequired, LoginRequired

        if await ctx.driver.exists(self.selectors["login_indicator"]):
            raise LoginRequired(
                "Mock Video Generator is asking you to sign in. Take over the browser and sign in (any email/password works)."
            )
        if await ctx.driver.exists(self.selectors["human_check"]):
            raise HumanInterventionRequired(
                "Mock Video Generator is showing a 'Verify you are human' check. Take over the browser to complete it."
            )
