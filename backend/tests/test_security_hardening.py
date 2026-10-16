"""Focused regression tests for the security hardening added in the 2026 roadmap."""

from __future__ import annotations

import logging

import pytest
from starlette.requests import Request

from app.core import deps
from app.core.config import Settings
from app.core.url_safety import BrowserURLPolicy, UnsafeDestination, sanitize_text, sanitize_url
from tests.conftest import ApiClient


def production_settings(**overrides) -> Settings:  # noqa: ANN003
    values = {
        "app_env": "production",
        "secret_key": "a-production-secret-with-at-least-thirty-two-characters",
        "encryption_key": "ZmFrZS1rZXktbXVzdC1iZS1leGFjdGx5LWVudHJ5",
        "database_url": "postgresql+asyncpg://user:pass@db:5432/bowserauto",
        "public_base_url": "https://app.example.test",
        "allowed_origins": ["https://app.example.test"],
        "trusted_hosts": ["app.example.test"],
        "cookie_secure": True,
        "allow_registration": False,
        "mock_platform_url": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_settings_require_https_and_explicit_trust() -> None:
    settings = production_settings()
    assert settings.cookies_secure is True
    with pytest.raises(ValueError, match="ALLOW_REGISTRATION must be false"):
        production_settings(allow_registration=True)
    with pytest.raises(ValueError, match="Refusing to start in production"):
        Settings(
            app_env="production",
            secret_key="a-production-secret-with-at-least-thirty-two-characters",
            encryption_key="ZmFrZS1rZXktbXVzdC1iZS1leGFjdGx5LWVudHJ5",
            database_url="postgresql+asyncpg://user:pass@db:5432/bowserauto",
        )


def test_url_sanitizer_removes_query_fragment_credentials_and_signed_path_tokens() -> None:
    sanitized = sanitize_url("https://user:password@example.test/reports/signed/abc.def?token=secret#frag")
    assert sanitized == "https://example.test/reports/signed/[redacted]"
    text = sanitize_text("Failed at https://example.test/callback?code=secret#state")
    assert "secret" not in text
    assert "code=" not in text


@pytest.mark.asyncio
async def test_signed_download_tokens_are_redacted_from_access_logs(
    client: ApiClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    secret_token = "signed-path-secret-token-12345"
    response = await client.get(f"/api/files/signed/{secret_token}")
    assert response.status_code in (400, 404)
    assert secret_token not in caplog.text
    assert "/api/files/signed/[redacted]" in caplog.text


@pytest.mark.asyncio
async def test_browser_url_policy_blocks_private_dns_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = production_settings()
    monkeypatch.setattr("app.core.url_safety._resolve", lambda host, port: {"10.4.5.6"})
    policy = BrowserURLPolicy(settings)
    with pytest.raises(UnsafeDestination, match="Private"):
        await policy.check("https://public-looking.example.test/")
    with pytest.raises(UnsafeDestination, match="metadata"):
        await policy.check("http://169.254.169.254/latest/meta-data")
    with pytest.raises(UnsafeDestination, match="HTTPS"):
        await policy.check("http://public.example.test/")


@pytest.mark.asyncio
async def test_browser_url_policy_allows_exact_configured_private_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = production_settings(browser_allowed_hosts=["ollama.internal"])
    monkeypatch.setattr("app.core.url_safety._resolve", lambda host, port: {"10.4.5.6"})
    policy = BrowserURLPolicy(settings)
    await policy.check("http://ollama.internal:11434/")
    monkeypatch.setattr("app.core.url_safety._resolve", lambda host, port: {"169.254.169.254"})
    blocked_policy = BrowserURLPolicy(settings)
    with pytest.raises(UnsafeDestination, match="metadata"):
        await blocked_policy.check("http://ollama.internal:11434/")


def _request(peer: str, forwarded: str = "") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("app.example.test", 80),
        }
    )


def test_forwarded_ip_is_used_only_from_trusted_proxy_and_walks_from_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(trusted_proxy_ips=["10.0.0.0/8"]))
    # The trusted proxy appended the observed client to a spoofed incoming XFF value.
    assert deps.client_ip(_request("10.0.0.9", "203.0.113.250, 198.51.100.7")) == "198.51.100.7"
    assert deps.client_ip(_request("192.0.2.8", "203.0.113.250")) == "192.0.2.8"


def test_production_mock_router_is_not_mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main

    monkeypatch.setattr(main, "get_settings", production_settings)
    app = main.create_app()
    assert not any(getattr(route, "path", "").startswith("/mock-platform") for route in app.routes)
    assert app.openapi_url is None
