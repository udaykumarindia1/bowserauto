"""Google Flow (Veo) adapter – labs.google/fx/tools/flow."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.base import AdapterContext, PlatformAdapter, _exists


class GoogleFlowAdapter(PlatformAdapter):
    key = "google_flow"
    name = "Google Flow (Veo)"
    category = "video"
    base_url = "https://labs.google/fx/tools/flow"
    generation_url = "https://labs.google/fx/tools/flow"
    description = "Google's filmmaking tool powered by Veo. Supports text-to-video, frames-to-video and ingredients (reference images)."
    requires_login = True
    notes = "Sign in with your Google account inside the browser session first. Flow organises generations in projects; the adapter opens the most recent project or creates one."
    capabilities: ClassVar[dict[str, Any]] = {"max_clip_seconds": 8, "aspect_ratios": ["16:9", "9:16"], "reference_images": True, "negative_prompt": False, "resolutions": ["720p", "1080p"]}
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {"any": [{"role": "button", "name": "/sign in/i"}, {"role": "link", "name": "/sign in/i"}]},
        "logged_in_indicator": {"any": [{"role": "button", "name": "/new project/i"}, {"placeholder": "/generate a video/i"}]},
        "new_project": {"role": "button", "name": "/new project/i"},
        "recent_project": {"css": "a[href*='/project/']"},
        "prompt_input": {"any": [{"placeholder": "/generate a video|describe your/i"}, {"role": "textbox"}, {"css": "textarea"}]},
        "upload": {"any": [{"css": "input[type=file]"}, {"role": "button", "name": "/ingredients|add image|upload|frames to video/i"}]},
        "settings_menu": {"role": "button", "name": "/settings|outputs|model/i"},
        "aspect_ratio_option": {"role": "menuitem", "name": "/{value}/"},
        "generate_button": {"any": [{"role": "button", "name": "/^(generate|create)$/i"}, {"css": "button[aria-label*='Generate' i]"}, {"css": "button[type=submit]"}]},
        "download_button": {"any": [{"role": "menuitem", "name": "/download/i"}, {"role": "button", "name": "/download/i"}]},
        "result_menu": {"role": "button", "name": "/more options|options|menu/i"},
        "result_video": {"css": "video[src]"},
        "error_indicator": {"text": "/couldn't generate|generation failed|something went wrong/i"},
    }
    status_patterns: ClassVar[dict[str, list[str]]] = {
        "QUEUED": [r"in queue", r"queued"],
        "GENERATING": [r"generating", r"\b\d{1,3}%", r"creating"],
        "PROCESSING": [r"processing", r"finishing"],
        "COMPLETED": [r"download", r"add to scene"],
    }

    async def open_generation_tool(self, ctx: AdapterContext) -> None:
        if await _exists(ctx, self.selectors["prompt_input"]):
            return
        if await _exists(ctx, self.selectors["recent_project"]):
            await ctx.say("Opening the most recent Flow project")
            await ctx.driver.click(self.selectors["recent_project"])
        elif await _exists(ctx, self.selectors["new_project"]):
            await ctx.say("Creating a new Flow project")
            await ctx.driver.click(self.selectors["new_project"])
        await ctx.driver.wait_for(self.selectors["prompt_input"], timeout_ms=20_000)

    async def download_result(self, ctx: AdapterContext, target_dir):  # type: ignore[override]
        # Flow hides the download action behind a per-clip menu.
        if not await _exists(ctx, self.selectors["download_button"]) and await _exists(ctx, self.selectors["result_menu"]):
            try:
                await ctx.driver.click(self.selectors["result_menu"], timeout_ms=5000)
            except Exception:
                pass
        return await super().download_result(ctx, target_dir)
