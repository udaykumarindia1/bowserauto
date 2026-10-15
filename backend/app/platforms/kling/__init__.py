"""Kling AI adapter – klingai.com."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.base import PlatformAdapter


class KlingAdapter(PlatformAdapter):
    key = "kling"
    name = "Kling AI"
    category = "video"
    base_url = "https://klingai.com"
    generation_url = "https://klingai.com/global/text-to-video/new"
    description = "Kuaishou's Kling text/image-to-video generator."
    notes = "Supports standard and professional modes; generation can take several minutes. Log in inside the browser session first."
    capabilities: ClassVar[dict[str, Any]] = {"max_clip_seconds": 10, "aspect_ratios": ["16:9", "9:16", "1:1"], "reference_images": True, "negative_prompt": True, "durations": [5, 10]}
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {"any": [{"role": "button", "name": "/sign in|log in/i"}, {"text": "/sign in to continue/i"}]},
        "prompt_input": {"any": [{"placeholder": "/prompt|describe|creative/i"}, {"css": "textarea"}]},
        "negative_prompt_input": {"placeholder": "/negative prompt|don't want/i"},
        "upload": {"any": [{"css": "input[type=file]"}, {"role": "button", "name": "/upload|image to video|add image/i"}]},
        "duration_option": {"any": [{"role": "radio", "name": "/{value}\\s?s/i"}, {"text": "/^{value}\\s?s$/i"}]},
        "aspect_ratio_option": {"any": [{"role": "radio", "name": "/{value}/"}, {"text": "/^{value}$/"}]},
        "generate_button": {"any": [{"role": "button", "name": "/^generate/i"}, {"css": "button.generate-btn"}]},
        "download_button": {"any": [{"role": "button", "name": "/download/i"}, {"css": "[class*='download']"}, {"css": "a[download]"}]},
        "result_video": {"css": "video[src]"},
        "error_indicator": {"text": "/generation failed|failed to generate|content moderation|sensitive/i"},
    }
    status_patterns: ClassVar[dict[str, list[str]]] = {
        "QUEUED": [r"in queue", r"queuing", r"waiting"],
        "GENERATING": [r"generating", r"\b\d{1,3}%"],
        "PROCESSING": [r"processing"],
        "COMPLETED": [r"download", r"extend"],
    }
