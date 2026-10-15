"""Vidu adapter – vidu.com."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.base import PlatformAdapter


class ViduAdapter(PlatformAdapter):
    key = "vidu"
    name = "Vidu"
    category = "video"
    base_url = "https://www.vidu.com"
    generation_url = "https://www.vidu.com/create"
    description = "Vidu text/image/reference-to-video."
    notes = "Log in inside the browser session first."
    capabilities: ClassVar[dict[str, Any]] = {
        "max_clip_seconds": 8,
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "reference_images": True,
        "negative_prompt": False,
        "durations": [4, 8],
    }
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {"any": [{"role": "button", "name": "/log in|sign in/i"}]},
        "prompt_input": {"any": [{"placeholder": "/describe|prompt/i"}, {"css": "textarea"}]},
        "upload": {
            "any": [{"css": "input[type=file]"}, {"role": "button", "name": "/upload|reference|image/i"}]
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
        "error_indicator": {"text": "/failed|something went wrong/i"},
    }
