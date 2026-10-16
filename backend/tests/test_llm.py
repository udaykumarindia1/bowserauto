"""LLM provider adapters are tested with in-memory HTTP transports only."""

from __future__ import annotations

import json

import httpx
import pytest

from app.agent.llm import (
    AnthropicClient,
    GeminiClient,
    LLMMessage,
    LLMUnavailable,
    OllamaClient,
    OpenAICompatibleClient,
    build_test_client,
    extract_json,
    validate_llm_base_url,
)
from app.core.config import Settings


async def test_openai_compatible_completion_json_vision_and_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"tool":"task.complete"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            },
        )

    transport = httpx.MockTransport(handler)
    client = OpenAICompatibleClient(
        "secret-key",
        "test-model",
        "https://llm.example/v1",
        name="openrouter",
        transport=transport,
    )
    result = await client.complete(
        [LLMMessage("system", "Return JSON"), LLMMessage("user", "Inspect this", images=["aW1n"])],
        json_mode=True,
        max_tokens=64,
        temperature=0.0,
    )

    assert result.model == "test-model"
    assert result.json() == {"tool": "task.complete"}
    assert result.usage["total_tokens"] == 16
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer secret-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {"type": "json_object"}
    user_content = body["messages"][1]["content"]
    assert user_content[1]["image_url"]["url"] == "data:image/jpeg;base64,aW1n"


async def test_openai_compatible_retries_only_explicit_unsupported_json_mode() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(400, text='{"error":"response_format is not supported"}')
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    client = OpenAICompatibleClient(
        None, "local-model", "http://localhost:1234/v1", transport=httpx.MockTransport(handler)
    )
    result = await client.complete([LLMMessage("user", "test")], json_mode=True)
    assert result.text == "OK"
    assert "response_format" in requests[0]
    assert "response_format" not in requests[1]


async def test_openai_compatible_retries_max_completion_token_parameter() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                400,
                text='{"error":"max_tokens is not supported; use max_completion_tokens instead"}',
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    client = OpenAICompatibleClient(
        "key", "reasoning-model", "https://api.example/v1", transport=httpx.MockTransport(handler)
    )
    result = await client.complete([LLMMessage("user", "test")])
    assert result.text == "OK"
    assert "max_tokens" in requests[0]
    assert "max_tokens" not in requests[1]
    assert requests[1]["max_completion_tokens"] == 2048


async def test_openai_provider_errors_are_sanitized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error":"private provider details sk-do-not-leak"}')

    client = OpenAICompatibleClient(
        "sk-do-not-leak", "private-model", "https://llm.example/v1", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LLMUnavailable) as exc:
        await client.complete([LLMMessage("user", "private prompt")])
    assert "authentication failed" in str(exc.value)
    assert "do-not-leak" not in str(exc.value)
    assert "private prompt" not in str(exc.value)


async def test_provider_redirect_is_not_followed_with_authorization_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example/collect"}, json={})

    client = OpenAICompatibleClient(
        "secret-key",
        "model",
        "https://provider.example/v1",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMUnavailable):
        await client.complete([LLMMessage("user", "test")])
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer secret-key"


async def test_gemini_native_request_supports_json_and_inline_images() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key_header"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-test",
                "candidates": [{"content": {"parts": [{"text": '{"tool":"task.complete"}'}]}}],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 5, "totalTokenCount": 16},
            },
        )

    client = GeminiClient(
        "google-secret",
        "gemini-test",
        "https://generativelanguage.googleapis.com/v1beta",
        transport=httpx.MockTransport(handler),
    )
    result = await client.complete(
        [
            LLMMessage("system", "Only JSON"),
            LLMMessage("user", "Describe this", images=["aW1n"], image_mime_type="image/png"),
        ],
        json_mode=True,
        max_tokens=128,
    )

    assert result.json() == {"tool": "task.complete"}
    assert result.usage["totalTokenCount"] == 16
    assert (
        seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    )
    assert seen["key_header"] == "google-secret"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "Only JSON"
    assert body["contents"][0]["parts"][1]["inlineData"] == {"mimeType": "image/png", "data": "aW1n"}


async def test_gemini_malformed_blocked_response_is_sanitized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []},
        )

    client = GeminiClient(
        "secret",
        "gemini-test",
        "https://generativelanguage.googleapis.com/v1beta",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMUnavailable, match="blocked by the provider") as exc:
        await client.complete([LLMMessage("user", "test")])
    assert "SAFETY" not in str(exc.value)


async def test_ollama_native_chat_supports_json_images_and_token_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen3:latest",
                "message": {"role": "assistant", "content": '{"tool":"task.complete"}'},
                "prompt_eval_count": 9,
                "eval_count": 4,
            },
        )

    client = OllamaClient(
        None,
        "qwen3:latest",
        "http://ollama:11434/v1",
        transport=httpx.MockTransport(handler),
    )
    result = await client.complete(
        [LLMMessage("user", "inspect", images=["aW1n"])], json_mode=True, max_tokens=77
    )
    assert result.json() == {"tool": "task.complete"}
    assert result.usage == {"prompt_tokens": 9, "completion_tokens": 4}
    assert seen["url"] == "http://ollama:11434/api/chat"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["format"] == "json"
    assert body["options"]["num_predict"] == 77
    assert body["messages"][0]["images"] == ["aW1n"]


async def test_anthropic_native_messages_request() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("x-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "claude-test",
                "content": [{"type": "text", "text": '{"tool":"task.complete"}'}],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        )

    client = AnthropicClient(
        "anthropic-secret",
        "claude-test",
        "https://api.anthropic.com",
        transport=httpx.MockTransport(handler),
    )
    result = await client.complete(
        [LLMMessage("system", "Return JSON"), LLMMessage("user", "go")], json_mode=True
    )
    assert result.json() == {"tool": "task.complete"}
    assert result.usage == {"input_tokens": 10, "output_tokens": 3}
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["api_key"] == "anthropic-secret"
    body = seen["body"]
    assert isinstance(body, dict)
    assert "single valid JSON" in body["system"]


def test_provider_factory_uses_provider_defaults_and_optional_ollama_key() -> None:
    settings = Settings(
        app_env="test",
        llm_provider="none",
        llm_timeout_seconds=45,
        database_url="sqlite+aiosqlite:///./test.db",
    )
    gemini, gemini_status = build_test_client(
        {"provider": "gemini", "api_key": "secret", "model": None, "base_url": None},
        settings=settings,
    )
    assert gemini is not None and gemini.name == "gemini"
    assert gemini.model == "gemini-2.5-flash"
    assert gemini_status["configured"] is True

    ollama, ollama_status = build_test_client(
        {"provider": "ollama", "model": "custom:latest", "base_url": None}, settings=settings
    )
    assert ollama is not None and ollama.name == "ollama"
    assert ollama.model == "custom:latest"
    assert ollama_status["configured"] is True

    missing_key, missing_status = build_test_client(
        {"provider": "openai", "model": None, "base_url": None}, settings=settings
    )
    assert missing_key is None
    assert missing_status["configured"] is False
    assert "API key" in missing_status["message"]


def test_organization_provider_cannot_inherit_server_api_key_or_endpoint() -> None:
    settings = Settings(
        app_env="test",
        llm_provider="openai",
        llm_api_key="server-global-secret",
        llm_model="gpt-global",
        llm_base_url="https://global.example/v1",
        database_url="sqlite+aiosqlite:///./test.db",
    )
    client, status = build_test_client(
        {
            "provider": "openai",
            "api_key": None,
            "model": None,
            "base_url": "https://attacker.example/v1",
        },
        settings=settings,
    )
    assert client is None
    assert status["configured"] is False
    assert "API key" in status["message"]

    local, local_status = build_test_client(
        {"provider": "ollama", "api_key": None, "model": None, "base_url": None}, settings=settings
    )
    assert local is not None and local_status["configured"] is True
    assert local.model == "qwen3:latest" and local.base_url == "http://localhost:11434"
    assert local.api_key is None


def test_production_llm_base_url_requires_private_http_host_allowlist() -> None:
    settings = Settings(
        app_env="production",
        secret_key="a-production-secret-with-at-least-thirty-two-characters",
        encryption_key="ZmFrZS1rZXktbXVzdC1iZS1leGFjdGx5LWVudHJ5",
        database_url="postgresql+asyncpg://user:pass@db:5432/bowserauto",
        public_base_url="https://app.example.test",
        allowed_origins=["https://app.example.test"],
        trusted_hosts=["app.example.test"],
        cookie_secure=True,
        allow_registration=False,
        mock_platform_url=None,
        llm_allowed_hosts=[],
    )
    with pytest.raises(ValueError, match="allowlisted"):
        validate_llm_base_url("http://ollama:11434", settings=settings, provider="ollama")
    for internal_url in ("https://ollama.internal", "https://ollama", "https://2130706433"):
        with pytest.raises(ValueError, match="allowlisted"):
            validate_llm_base_url(internal_url, settings=settings, provider="ollama")
    for metadata_url in (
        "http://169.254.169.254/latest/meta-data",
        "http://[fd00:ec2::254]/latest/meta-data",
        "http://100.100.100.200/latest/meta-data",
    ):
        with pytest.raises(ValueError, match="metadata"):
            validate_llm_base_url(metadata_url, settings=settings)
    for invalid_url in (
        "ftp://llm.example",
        "https://user:pass@llm.example/v1",
        "https://llm.example/v1?key=secret",
        "https://llm.example/v1#fragment",
    ):
        with pytest.raises(ValueError):
            validate_llm_base_url(invalid_url, settings=settings)
    trusted = settings.model_copy(update={"llm_allowed_hosts": ["ollama"]})
    assert (
        validate_llm_base_url("http://ollama:11434", settings=trusted, provider="ollama")
        == "http://ollama:11434"
    )


def test_production_llm_url_checks_resolved_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        app_env="production",
        secret_key="a-production-secret-with-at-least-thirty-two-characters",
        encryption_key="ZmFrZS1rZXktbXVzdC1iZS1leGFjdGx5LWVudHJ5",
        database_url="postgresql+asyncpg://user:pass@db:5432/bowserauto",
        public_base_url="https://app.example.test",
        allowed_origins=["https://app.example.test"],
        trusted_hosts=["app.example.test"],
        cookie_secure=True,
        allow_registration=False,
        mock_platform_url=None,
    )
    monkeypatch.setattr("app.agent.llm.factory._resolved_addresses", lambda _host, _port: {"10.0.0.9"})
    with pytest.raises(ValueError, match="allowlisted"):
        validate_llm_base_url("https://public-looking.example.test", settings=settings)

    trusted = settings.model_copy(update={"llm_allowed_hosts": ["ollama.internal"]})
    assert validate_llm_base_url("https://ollama.internal", settings=trusted) == "https://ollama.internal"

    monkeypatch.setattr("app.agent.llm.factory._resolved_addresses", lambda _host, _port: {"169.254.169.254"})
    with pytest.raises(ValueError, match="metadata"):
        validate_llm_base_url("https://ollama.internal", settings=trusted)


def test_extract_json_finds_document_inside_prose_and_fences() -> None:
    assert extract_json('Result:\n```json\n{"tool":"browser.click","args":{"index":2}}\n```') == {
        "tool": "browser.click",
        "args": {"index": 2},
    }
    assert extract_json('text [1, {"a": 2}] done') == [1, {"a": 2}]
