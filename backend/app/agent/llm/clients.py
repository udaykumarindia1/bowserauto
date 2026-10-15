"""HTTP implementations for the supported LLM provider families.

No provider SDKs are needed in the browser worker. Each request uses a bounded timeout, disables
redirects (so an Authorization header cannot be forwarded to an untrusted host), and returns
sanitized errors that never include response bodies, prompts, or credentials.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.agent.llm.types import LLMMessage, LLMResponse, LLMUnavailable


def _safe_base_url(value: str) -> str:
    base = value.strip().rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "LLM base URL must be an http(s) origin/path without credentials, query, or fragment"
        )
    # Accessing .port also validates malformed values such as :99999.
    _ = parsed.port
    return base


def _timeout(value: int | float) -> httpx.Timeout:
    seconds = max(1.0, float(value))
    return httpx.Timeout(seconds, connect=min(10.0, seconds))


def _error_message(provider: str, status_code: int) -> str:
    if status_code in (401, 403):
        detail = "authentication failed; check the provider key and account access"
    elif status_code == 404:
        detail = "endpoint or model was not found; check the base URL and model ID"
    elif status_code == 429:
        detail = "rate limit or quota exceeded"
    elif status_code >= 500:
        detail = "provider service error"
    else:
        detail = "request was rejected; check the model ID and provider settings"
    return f"{provider} request failed ({status_code}): {detail}"


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text", ""))
            for item in value
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        )
    return ""


def _usage_ints(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(count) for key, count in value.items() if isinstance(count, int) and count >= 0}


def _safe_content(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.role if message.role in {"system", "user", "assistant"} else "user"
        if not message.images:
            converted.append({"role": role, "content": message.content})
            continue
        parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        for image in message.images:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{message.image_mime_type or 'image/jpeg'};base64,{image}",
                        "detail": "low",
                    },
                }
            )
        converted.append({"role": role, "content": parts})
    return converted


def _validate_completion(response: httpx.Response, provider: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise LLMUnavailable(_error_message(provider, response.status_code))
    try:
        body = response.json()
    except ValueError as exc:
        raise LLMUnavailable(f"{provider} returned an invalid response") from exc
    if not isinstance(body, dict):
        raise LLMUnavailable(f"{provider} returned an invalid response")
    return body


class OpenAICompatibleClient:
    """OpenAI Chat Completions client for OpenAI and compatible gateways.

    Supports providers such as OpenRouter, Groq, DeepSeek, Together, Fireworks, Mistral, xAI,
    vLLM, LM Studio, LocalAI, and compatible managed gateways by changing ``base_url``.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str,
        base_url: str,
        *,
        name: str = "openai_compatible",
        timeout: int = 90,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.api_key = api_key
        self.model = model
        self.base_url = _safe_base_url(base_url)
        self.timeout = timeout
        self.transport = transport

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _safe_content(messages),
            "max_tokens": max(1, int(max_tokens)),
            "temperature": min(2.0, max(0.0, float(temperature))),
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        # Some current models use max_completion_tokens or reject temperature/JSON mode, while
        # compatible servers vary in which optional fields they implement. Retry only explicit
        # unsupported-parameter 400s; at most four bounded validation attempts are made.
        try:
            async with httpx.AsyncClient(
                timeout=_timeout(self.timeout), follow_redirects=False, transport=self.transport
            ) as client:
                response = await self._post_with_compatibility(client, url, headers, body)
        except httpx.TimeoutException as exc:
            raise LLMUnavailable(f"{self.name} request timed out after {self.timeout} seconds") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"{self.name} could not be reached; check the endpoint and network") from exc
        data = _validate_completion(response, self.name)
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
            text = _text_content(message.get("content"))
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMUnavailable(f"{self.name} returned no completion") from exc
        return LLMResponse(
            text=text, model=str(data.get("model") or self.model), usage=_usage_ints(data.get("usage"))
        )

    async def _post_with_compatibility(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> httpx.Response:
        response: httpx.Response | None = None
        for _attempt in range(4):
            response = await client.post(url, headers=headers, json=body)
            if response.status_code != 400:
                return response
            try:
                error_text = response.text.lower()
            except Exception:
                return response
            if "max_tokens" in error_text and "max_completion_tokens" in error_text and "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens")
                continue
            if "response_format" in error_text and "response_format" in body:
                body.pop("response_format", None)
                continue
            if (
                "temperature" in error_text
                and "temperature" in body
                and any(word in error_text for word in ("unsupported", "not supported", "unknown", "invalid"))
            ):
                body.pop("temperature", None)
                continue
            return response
        assert response is not None
        return response


class GeminiClient:
    """Google Gemini generateContent adapter with JSON mode and vision input."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        *,
        timeout: int = 90,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = _safe_base_url(base_url)
        self.timeout = timeout
        self.transport = transport

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        system_parts = [
            {"text": message.content} for message in messages if message.role == "system" and message.content
        ]
        contents: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                continue
            role = "model" if message.role == "assistant" else "user"
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"text": message.content})
            for image in message.images:
                parts.append(
                    {"inlineData": {"mimeType": message.image_mime_type or "image/jpeg", "data": image}}
                )
            if not parts:
                continue
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})

        if not contents:
            raise LLMUnavailable("Gemini requires at least one user or assistant message")
        generation_config: dict[str, Any] = {
            "maxOutputTokens": max(1, int(max_tokens)),
            "temperature": min(2.0, max(0.0, float(temperature))),
        }
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
        if system_parts:
            body["systemInstruction"] = {"parts": system_parts}

        model_id = self.model.removeprefix("models/")
        url = f"{self.base_url}/models/{quote(model_id, safe='-._')}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-goog-api-key": self.api_key,
        }
        async with httpx.AsyncClient(
            timeout=_timeout(self.timeout), follow_redirects=False, transport=self.transport
        ) as client:
            try:
                response = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                raise LLMUnavailable(f"Gemini request timed out after {self.timeout} seconds") from exc
            except httpx.HTTPError as exc:
                raise LLMUnavailable("Gemini could not be reached; check the endpoint and network") from exc
        data = _validate_completion(response, self.name)
        candidates = data.get("candidates") or []
        try:
            parts = candidates[0]["content"]["parts"]
            if not isinstance(parts, list):
                raise TypeError("parts is not a list")
        except (IndexError, KeyError, TypeError) as exc:
            feedback = data.get("promptFeedback")
            reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            detail = "request was blocked by the provider" if reason else "no completion was returned"
            raise LLMUnavailable(f"Gemini {detail}") from exc
        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
        usage_value = data.get("usageMetadata")
        usage_data = usage_value if isinstance(usage_value, dict) else {}
        usage = {
            "promptTokenCount": usage_data.get("promptTokenCount", 0),
            "candidatesTokenCount": usage_data.get("candidatesTokenCount", 0),
            "totalTokenCount": usage_data.get("totalTokenCount", 0),
        }
        return LLMResponse(text, str(data.get("modelVersion") or self.model), _usage_ints(usage))


class OllamaClient:
    """Ollama's native ``/api/chat`` endpoint (local installations and Ollama Cloud)."""

    name = "ollama"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        base_url: str,
        *,
        timeout: int = 90,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        base = _safe_base_url(base_url)
        # The UI asks for an origin/base URL, not a versioned endpoint. Be forgiving when a user
        # pastes Ollama's common /api or OpenAI-compatible /v1 suffix.
        for suffix in ("/api", "/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        self.base_url = base
        self.timeout = timeout
        self.transport = transport

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload: list[dict[str, Any]] = []
        for message in messages:
            role = message.role if message.role in {"system", "user", "assistant"} else "user"
            item: dict[str, Any] = {"role": role, "content": message.content}
            if message.images:
                item["images"] = message.images
            payload.append(item)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": payload,
            "stream": False,
            "options": {
                "num_predict": max(1, int(max_tokens)),
                "temperature": min(2.0, max(0.0, float(temperature))),
            },
        }
        if json_mode:
            body["format"] = "json"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(
            timeout=_timeout(self.timeout), follow_redirects=False, transport=self.transport
        ) as client:
            try:
                response = await client.post(f"{self.base_url}/api/chat", headers=headers, json=body)
            except httpx.TimeoutException as exc:
                raise LLMUnavailable(f"Ollama request timed out after {self.timeout} seconds") from exc
            except httpx.HTTPError as exc:
                raise LLMUnavailable(
                    "Ollama could not be reached; check that the server is running and reachable"
                ) from exc
        data = _validate_completion(response, self.name)
        try:
            text = str((data.get("message") or {}).get("content") or "")
        except AttributeError as exc:
            raise LLMUnavailable("Ollama returned no completion") from exc
        usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
        }
        return LLMResponse(text, str(data.get("model") or self.model), _usage_ints(usage))


class AnthropicClient:
    """Anthropic Messages API adapter with image input."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        *,
        timeout: int = 90,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = _safe_base_url(base_url)
        self.timeout = timeout
        self.transport = transport

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        system_parts = [
            message.content for message in messages if message.role == "system" and message.content
        ]
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                continue
            role = "assistant" if message.role == "assistant" else "user"
            if message.images:
                content: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
                for image in message.images:
                    content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": message.image_mime_type or "image/jpeg",
                                "data": image,
                            },
                        }
                    )
                converted.append({"role": role, "content": content})
            else:
                converted.append({"role": role, "content": message.content})
        if json_mode:
            system_parts.append("Respond with a single valid JSON document and nothing else.")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": converted,
            "max_tokens": max(1, int(max_tokens)),
            "temperature": min(1.0, max(0.0, float(temperature))),
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "application/json",
        }
        async with httpx.AsyncClient(
            timeout=_timeout(self.timeout), follow_redirects=False, transport=self.transport
        ) as client:
            try:
                response = await client.post(f"{self.base_url}/v1/messages", headers=headers, json=body)
            except httpx.TimeoutException as exc:
                raise LLMUnavailable(f"Anthropic request timed out after {self.timeout} seconds") from exc
            except httpx.HTTPError as exc:
                raise LLMUnavailable(
                    "Anthropic could not be reached; check the endpoint and network"
                ) from exc
        data = _validate_completion(response, self.name)
        blocks = data.get("content") or []
        if not isinstance(blocks, list):
            raise LLMUnavailable("Anthropic returned an invalid completion")
        text = "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return LLMResponse(text, str(data.get("model") or self.model), _usage_ints(data.get("usage")))
