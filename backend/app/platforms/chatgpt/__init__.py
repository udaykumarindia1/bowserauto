"""ChatGPT adapter – chatgpt.com."""

from __future__ import annotations

from typing import Any, ClassVar

from app.platforms.chat_base import ChatPlatformAdapter


class ChatGPTAdapter(ChatPlatformAdapter):
    key = "chatgpt"
    name = "ChatGPT"
    base_url = "https://chatgpt.com"
    generation_url = "https://chatgpt.com/"
    description = "OpenAI ChatGPT web app: send prompts, wait for the answer and save it."
    notes = "Log in inside the browser session. Each scene prompt is sent as a new message; the answer is saved as Markdown."
    selectors: ClassVar[dict[str, Any]] = {
        "login_indicator": {
            "any": [{"role": "button", "name": "/log in|sign up/i"}, {"css": "[data-testid='login-button']"}]
        },
        "logged_in_indicator": {"css": "#prompt-textarea"},
        "prompt_input": {
            "any": [
                {"css": "#prompt-textarea"},
                {"role": "textbox", "name": "/message/i"},
                {"placeholder": "/ask anything|message/i"},
            ]
        },
        "upload": {"any": [{"css": "input[type=file]"}, {"role": "button", "name": "/attach|upload/i"}]},
        "send_button": {
            "any": [
                {"css": "[data-testid='send-button']"},
                {"role": "button", "name": "/send prompt|send message/i"},
            ]
        },
        "streaming_indicator": {
            "any": [
                {"css": "[data-testid='stop-button']"},
                {"role": "button", "name": "/stop streaming|stop generating/i"},
            ]
        },
        "assistant_message": {"css": "[data-message-author-role='assistant']"},
    }

    def answer_script(self) -> str:
        return "(() => { const nodes = document.querySelectorAll(\"[data-message-author-role='assistant']\"); return nodes.length ? nodes[nodes.length - 1].innerText : ''; })()"
