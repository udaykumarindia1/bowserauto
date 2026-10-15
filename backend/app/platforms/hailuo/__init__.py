"""Hailuo AI (MiniMax) adapter – hailuoai.video."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.base import PlatformAdapter


class HailuoAdapter(PlatformAdapter):
    key = "hailuo"
    name = "Hailuo AI (MiniMax)"
    category = "video"
    base_url = "https://hailuoai.video"
    generation_url = "https://hailuoai.video/create"
    description = "MiniMax Hailuo text/image-to-video."
    notes = (
        "Log in inside the browser session. Generations appear in a feed; the newest result is downloaded."
    )
    capabilities: ClassVar[dict[str, Any]] = {
        "max_clip_seconds": 6,
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "reference_images": True,
        "negative_prompt": False,
        "durations": [6],
    }
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {"any": [{"role": "button", "name": "/log in|sign in/i"}]},
        "prompt_input": {"any": [{"placeholder": "/describe|prompt|idea/i"}, {"css": "textarea"}]},
        "upload": {
            "any": [
                {"css": "input[type=file]"},
                {"role": "button", "name": "/upload|image|subject reference/i"},
            ]
        },
        "generate_button": {
            "any": [{"role": "button", "name": "/create|generate/i"}, {"css": "button[type=submit]"}]
        },
        "download_button": {
            "any": [
                {"role": "button", "name": "/download/i"},
                {"css": "[class*='download']"},
                {"css": "a[download]"},
            ]
        },
        "result_video": {"css": "video[src]"},
        "error_indicator": {"text": "/generation failed|failed|sensitive content|try again/i"},
    }
