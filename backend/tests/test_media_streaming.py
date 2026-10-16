"""Media-src fallback streams bounded data and validates every redirect without live network IO."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from app.browser.playwright_driver import PlaywrightDriver
from app.core.errors import DownloadFailed


class FakePolicy:
    def __init__(self) -> None:
        self.checked: list[str] = []

    async def check(self, url: str) -> None:
        self.checked.append(url)
        if urlsplit(url).hostname == "blocked.example.test":
            raise ValueError("blocked")


class FakeContext:
    async def cookies(self, urls: list[str]) -> list[dict[str, str]]:
        if "media.example.test" in urls[0]:
            return [{"name": "sid", "value": "private-cookie"}]
        return []


class FakePage:
    url = "https://media.example.test/app"
    context = FakeContext()

    async def evaluate(self, _expression: str) -> str:
        return "Fake Browser"


class FakeBrowser:
    def __init__(self) -> None:
        self.page = FakePage()
        self.url_policy = FakePolicy()


@pytest.mark.asyncio
async def test_fetch_to_file_streams_redirects_and_scopes_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"streamed-media-data"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://cdn.example.test/file"})
        return httpx.Response(200, content=body)

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def mock_client(**kwargs: Any) -> httpx.AsyncClient:
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr("app.browser.playwright_driver.httpx.AsyncClient", mock_client)
    browser = FakeBrowser()
    driver = PlaywrightDriver(browser)  # type: ignore[arg-type]
    target = tmp_path / "media.bin"

    size = await driver.fetch_to_file("https://media.example.test/start?sig=secret", target, max_bytes=100)

    assert size == len(body)
    assert target.read_bytes() == body
    assert browser.url_policy.checked == [
        "https://media.example.test/start?sig=secret",
        "https://cdn.example.test/file",
    ]
    assert seen[0].headers["cookie"] == "sid=private-cookie"
    assert "cookie" not in seen[1].headers


@pytest.mark.asyncio
async def test_fetch_to_file_removes_oversized_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"x" * 101))
    monkeypatch.setattr(
        "app.browser.playwright_driver.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    driver = PlaywrightDriver(FakeBrowser())  # type: ignore[arg-type]
    target = tmp_path / "too-large.bin"

    with pytest.raises(DownloadFailed, match="size limit"):
        await driver.fetch_to_file("https://media.example.test/file", target, max_bytes=100)
    assert not target.exists()
