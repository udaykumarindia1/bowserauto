"""Runway adapter – app.runwayml.com (Gen-3 / Gen-4 video)."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.base import PlatformAdapter


class RunwayAdapter(PlatformAdapter):
    key = "runway"
    name = "Runway"
    category = "video"
    base_url = "https://app.runwayml.com"
    generation_url = "https://app.runwayml.com/video-tools/teams"
    description = "Runway Gen-3/Gen-4 text and image to video."
    notes = "Log in inside the browser session. The adapter uses the Generate Video tool; adjust the generation URL in platform config if your workspace differs."
    capabilities: ClassVar[dict[str, Any]] = {
        "max_clip_seconds": 10,
        "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
        "reference_images": True,
        "negative_prompt": False,
        "durations": [5, 10],
    }
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {
            "any": [{"role": "button", "name": "/log in|sign in/i"}, {"css": "input[name=email]"}]
        },
        "open_tool": {
            "any": [
                {"role": "link", "name": "/generate video|gen-4|gen-3/i"},
                {"role": "button", "name": "/generate video/i"},
            ]
        },
        "prompt_input": {
            "any": [
                {"placeholder": "/describe your shot|describe|prompt/i"},
                {"css": "textarea"},
                {"role": "textbox"},
            ]
        },
        "upload": {
            "any": [
                {"css": "input[type=file]"},
                {"role": "button", "name": "/upload|drop an image|select asset/i"},
            ]
        },
        "settings_menu": {"role": "button", "name": "/settings|duration|aspect/i"},
        "duration_option": {"role": "button", "name": "/^{value}\\s?s(ec)?/i"},
        "aspect_ratio_option": {"role": "button", "name": "/{value}/"},
        "generate_button": {
            "any": [{"role": "button", "name": "/^generate/i"}, {"css": "button[type=submit]"}]
        },
        "download_button": {
            "any": [
                {"role": "button", "name": "/download/i"},
                {"role": "menuitem", "name": "/download/i"},
                {"css": "a[download]"},
            ]
        },
        "result_video": {"css": "video[src]"},
        "error_indicator": {"text": "/generation failed|something went wrong|insufficient credits/i"},
    }
