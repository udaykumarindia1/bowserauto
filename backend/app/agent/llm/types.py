"""Shared types for language-model clients.

The browser agent deliberately consumes plain text plus a constrained JSON response. Keeping
this contract small lets the same agent work with hosted APIs and local OpenAI-compatible or
Ollama deployments without vendor SDKs in the worker image.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(slots=True)
class LLMMessage:
    role: Literal["system", "user", "assistant"] | str
    content: str
    images: list[str] = field(default_factory=list)  # base64 image payloads, without a data URI
    image_mime_type: str = "image/jpeg"


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)

    def json(self) -> Any:
        return extract_json(self.text)


class LLMClient(Protocol):
    """Minimal client contract used by scene analysis and the browser agent."""

    name: str
    model: str

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse: ...


class LLMUnavailable(Exception):
    """A provider could not be reached or did not return a usable completion."""


def extract_json(text: str) -> Any:
    """Extract the first valid JSON object/array, tolerating surrounding prose and code fences."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate[3:]
        if candidate[:4].lower() == "json":
            candidate = candidate[4:]
        candidate = candidate.removesuffix("```").strip()
    try:
        value = json.loads(candidate)
        if isinstance(value, (dict, list)):
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(candidate):
        if char not in "{[":
            continue
        try:
            value, _end = decoder.raw_decode(candidate[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    raise ValueError("Model response did not contain valid JSON")
