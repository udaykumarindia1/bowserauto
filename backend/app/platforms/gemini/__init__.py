"""Google Gemini adapter – gemini.google.com."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.chat_base import ChatPlatformAdapter


class GeminiAdapter(ChatPlatformAdapter):
    key = "gemini"
    name = "Google Gemini"
    base_url = "https://gemini.google.com"
    generation_url = "https://gemini.google.com/app"
    description = "Google Gemini web app: send prompts, wait for the answer and save it."
    notes = "Sign in with your Google account inside the browser session."
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {
            "any": [{"role": "link", "name": "/sign in/i"}, {"role": "button", "name": "/sign in/i"}]
        },
        "logged_in_indicator": {"css": ".ql-editor, rich-textarea"},
        "prompt_input": {
            "any": [{"css": "rich-textarea .ql-editor"}, {"css": ".ql-editor"}, {"role": "textbox"}]
        },
        "upload": {
            "any": [{"css": "input[type=file]"}, {"role": "button", "name": "/upload|add files|image/i"}]
        },
        "send_button": {
            "any": [{"css": "button[aria-label*='Send' i]"}, {"role": "button", "name": "/send/i"}]
        },
        "streaming_indicator": {
            "any": [{"css": "button[aria-label*='Stop' i]"}, {"role": "button", "name": "/stop/i"}]
        },
        "assistant_message": {"css": "model-response, .model-response-text"},
    }

    def answer_script(self) -> str:
        return "(() => { const nodes = document.querySelectorAll('model-response, .model-response-text'); return nodes.length ? nodes[nodes.length - 1].innerText : ''; })()"
