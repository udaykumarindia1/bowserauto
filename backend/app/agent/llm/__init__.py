"""LLM provider abstraction used by the scene engine and the generic browser agent."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class LLMMessage:
    role: str  # system | user | assistant
    content: str
    images: list[str] = field(default_factory=list)  # base64 JPEG payloads for vision-capable models


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)

    def json(self) -> Any:
        return extract_json(self.text)


class LLMClient(Protocol):
    name: str
    model: str

    async def complete(self, messages: list[LLMMessage], *, json_mode: bool = False, max_tokens: int = 2048, temperature: float = 0.2) -> LLMResponse: ...


class LLMUnavailable(Exception):
    pass


def extract_json(text: str) -> Any:
    """Extract the first JSON object/array from a model response (tolerates code fences)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("Model response did not contain valid JSON")


class OpenAICompatibleClient:
    """Works with OpenAI, Azure-style gateways, OpenRouter, Ollama, vLLM, LM Studio..."""

    name = "openai"

    def __init__(self, api_key: str | None, model: str, base_url: str | None = None, timeout: int = 90) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout

    async def complete(self, messages: list[LLMMessage], *, json_mode: bool = False, max_tokens: int = 2048, temperature: float = 0.2) -> LLMResponse:
        payload_messages = []
        for m in messages:
            if m.images:
                content: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
                for img in m.images:
                    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}", "detail": "low"}})
                payload_messages.append({"role": m.role, "content": content})
            else:
                payload_messages.append({"role": m.role, "content": m.content})
        body: dict[str, Any] = {"model": self.model, "messages": payload_messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        if response.status_code >= 400:
            raise LLMUnavailable(f"LLM request failed ({response.status_code}): {response.text[:300]}")
        data = response.json()
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        return LLMResponse(text=text, model=data.get("model", self.model), usage={k: v for k, v in usage.items() if isinstance(v, int)})


class AnthropicClient:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str | None = None, timeout: int = 90) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.timeout = timeout

    async def complete(self, messages: list[LLMMessage], *, json_mode: bool = False, max_tokens: int = 2048, temperature: float = 0.2) -> LLMResponse:
        system_parts = [m.content for m in messages if m.role == "system"]
        converted = []
        for m in messages:
            if m.role == "system":
                continue
            if m.images:
                content: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
                for img in m.images:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}})
                converted.append({"role": m.role, "content": content})
            else:
                converted.append({"role": m.role, "content": m.content})
        if json_mode:
            system_parts.append("Respond with a single valid JSON document and nothing else.")
        body: dict[str, Any] = {"model": self.model, "messages": converted, "max_tokens": max_tokens, "temperature": temperature}
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
        if response.status_code >= 400:
            raise LLMUnavailable(f"LLM request failed ({response.status_code}): {response.text[:300]}")
        data = response.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage") or {}
        return LLMResponse(text=text, model=data.get("model", self.model), usage={k: v for k, v in usage.items() if isinstance(v, int)})


def build_llm_client(overrides: dict[str, Any] | None = None) -> LLMClient | None:
    """Create an LLM client from settings (optionally overridden per organization)."""
    settings = get_settings()
    overrides = overrides or {}
    provider = overrides.get("provider") or settings.llm_provider
    if provider in (None, "none", "inherit"):
        provider = settings.llm_provider if provider == "inherit" else provider
    if provider in (None, "none"):
        return None
    api_key = overrides.get("api_key") or settings.llm_api_key
    model = overrides.get("model") or settings.llm_model
    base_url = overrides.get("base_url") or settings.llm_base_url
    if provider == "openai":
        return OpenAICompatibleClient(api_key, model or "gpt-4o-mini", base_url, settings.llm_timeout_seconds)
    if provider == "anthropic":
        if not api_key:
            return None
        return AnthropicClient(api_key, model or "claude-3-5-sonnet-latest", base_url, settings.llm_timeout_seconds)
    return None


def llm_status(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    client = build_llm_client(overrides)
    settings = get_settings()
    return {
        "configured": client is not None,
        "provider": client.name if client else "none",
        "model": client.model if client else None,
        "source": "organization" if overrides and overrides.get("provider") not in (None, "inherit") else ("environment" if settings.llm_provider != "none" else "none"),
    }
