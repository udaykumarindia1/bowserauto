"""Luma Dream Machine adapter – dream-machine.lumalabs.ai."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.base import PlatformAdapter


class LumaAdapter(PlatformAdapter):
    key = "luma"
    name = "Luma Dream Machine"
    category = "video"
    base_url = "https://dream-machine.lumalabs.ai"
    generation_url = "https://dream-machine.lumalabs.ai/"
    description = "Luma's Dream Machine (Ray) video generator."
    notes = "Log in with Google/Apple inside the browser session. Reference images are attached through the + button."
    capabilities: ClassVar[dict[str, Any]] = {
        "max_clip_seconds": 9,
        "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
        "reference_images": True,
        "negative_prompt": False,
        "durations": [5, 9],
    }
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {"any": [{"role": "button", "name": "/sign in|log in|continue with google/i"}]},
        "prompt_input": {
            "any": [
                {"placeholder": "/describe|imagine|what do you want|prompt/i"},
                {"css": "textarea"},
                {"role": "textbox"},
            ]
        },
        "upload": {
            "any": [{"css": "input[type=file]"}, {"role": "button", "name": "/attach|add|image|upload|\\+/i"}]
        },
        "settings_menu": {"role": "button", "name": "/settings|options|aspect|duration/i"},
        "duration_option": {"role": "button", "name": "/^{value}\\s?s/i"},
        "aspect_ratio_option": {"role": "button", "name": "/{value}/"},
        "generate_button": {
            "any": [
                {"role": "button", "name": "/generate|create|send|submit/i"},
                {"css": "button[type=submit]"},
            ]
        },
        "download_button": {
            "any": [
                {"role": "button", "name": "/download/i"},
                {"role": "menuitem", "name": "/download/i"},
                {"css": "a[download]"},
            ]
        },
        "result_video": {"css": "video[src]"},
        "error_indicator": {"text": "/generation failed|something went wrong|moderat/i"},
    }
