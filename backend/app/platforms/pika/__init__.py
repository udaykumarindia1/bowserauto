"""Pika adapter – pika.art."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.base import PlatformAdapter


class PikaAdapter(PlatformAdapter):
    key = "pika"
    name = "Pika"
    category = "video"
    base_url = "https://pika.art"
    generation_url = "https://pika.art/create"
    description = "Pika text/image-to-video with effects."
    notes = "Log in inside the browser session; downloads are offered on each generated clip."
    capabilities: ClassVar[dict[str, Any]] = {"max_clip_seconds": 10, "aspect_ratios": ["16:9", "9:16", "1:1", "4:5", "5:4"], "reference_images": True, "negative_prompt": True, "durations": [5, 10]}
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {"any": [{"role": "button", "name": "/log in|sign in|continue with google/i"}]},
        "prompt_input": {"any": [{"placeholder": "/describe|what do you want|prompt/i"}, {"css": "textarea"}, {"role": "textbox"}]},
        "negative_prompt_input": {"placeholder": "/negative/i"},
        "upload": {"any": [{"css": "input[type=file]"}, {"role": "button", "name": "/upload|image|ingredient|\\+/i"}]},
        "generate_button": {"any": [{"role": "button", "name": "/generate|create/i"}, {"css": "button[type=submit]"}]},
        "download_button": {"any": [{"role": "button", "name": "/download/i"}, {"role": "menuitem", "name": "/download/i"}, {"css": "a[download]"}]},
        "result_video": {"css": "video[src]"},
        "error_indicator": {"text": "/generation failed|something went wrong|moderat/i"},
    }
