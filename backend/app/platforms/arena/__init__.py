"""Arena adapter – arena.ai / lmarena.ai chat interface."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.chat_base import ChatPlatformAdapter


class ArenaAdapter(ChatPlatformAdapter):
    key = "arena"
    name = "Arena"
    base_url = "https://arena.ai"
    generation_url = "https://arena.ai/"
    description = "Arena chat interface: send prompts to the selected model and save the answer."
    notes = "Open the browser session, pick the model/mode you want and sign in if required; the adapter reuses the current chat."
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {"any": [{"role": "button", "name": "/sign in|log in/i"}]},
        "prompt_input": {"any": [{"css": "textarea"}, {"role": "textbox"}, {"css": "[contenteditable='true']"}]},
        "upload": {"any": [{"css": "input[type=file]"}, {"role": "button", "name": "/attach|upload/i"}]},
        "send_button": {"any": [{"role": "button", "name": "/send/i"}, {"css": "button[type=submit]"}]},
        "streaming_indicator": {"role": "button", "name": "/stop/i"},
        "assistant_message": {"css": "[data-role='assistant'], [data-message-author-role='assistant'], .assistant-message, article"},
    }

    def answer_script(self) -> str:
        return "(() => { const nodes = document.querySelectorAll(\"[data-role='assistant'], [data-message-author-role='assistant'], .assistant-message\"); if (nodes.length) return nodes[nodes.length - 1].innerText; const arts = document.querySelectorAll('article'); return arts.length ? arts[arts.length - 1].innerText : ''; })()"
