"""Regression tests for request body limits, including chunked bodies without Content-Length."""

from __future__ import annotations

import json
from collections.abc import Iterable

import pytest
from starlette.types import Message, Receive, Scope, Send

from app.core.body_limit import RequestBodySizeLimitMiddleware


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/upload",
        "raw_path": b"/upload",
        "query_string": b"",
        "headers": headers or [],
        "client": ("192.0.2.10", 12345),
        "server": ("app.example.test", 443),
    }


async def _request(
    chunks: Iterable[tuple[bytes, bool]],
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    max_bytes: int = 4,
) -> tuple[list[Message], bool]:
    pending = [{"type": "http.request", "body": body, "more_body": more_body} for body, more_body in chunks]
    app_called = False
    sent: list[Message] = []

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal app_called
        app_called = True
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}', "more_body": False})

    middleware = RequestBodySizeLimitMiddleware(endpoint, max_bytes=max_bytes)
    await middleware(_scope(headers), receive, send)
    return sent, app_called


def _response(messages: list[Message]) -> tuple[int, dict]:
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return start["status"], json.loads(body or b"{}")


@pytest.mark.asyncio
async def test_content_length_over_limit_is_rejected_before_app_dispatch() -> None:
    messages, app_called = await _request([], headers=[(b"content-length", b"5")], max_bytes=4)

    status, payload = _response(messages)
    assert status == 413
    assert payload["error"]["code"] == "payload_too_large"
    assert payload["error"]["details"]["max_bytes"] == 4
    assert not app_called


@pytest.mark.asyncio
async def test_chunked_body_without_content_length_is_counted() -> None:
    messages, app_called = await _request([(b"1234", True), (b"5", False)], max_bytes=4)

    status, payload = _response(messages)
    assert status == 413
    assert payload["error"]["code"] == "payload_too_large"
    assert app_called


@pytest.mark.asyncio
async def test_body_exactly_at_limit_is_allowed() -> None:
    messages, app_called = await _request([(b"1234", False)], max_bytes=4)

    status, payload = _response(messages)
    assert status == 200
    assert payload == {"ok": True}
    assert app_called


@pytest.mark.asyncio
async def test_conflicting_content_length_headers_are_rejected() -> None:
    messages, app_called = await _request(
        [], headers=[(b"content-length", b"4"), (b"content-length", b"5")], max_bytes=8
    )

    status, payload = _response(messages)
    assert status == 400
    assert payload["error"]["code"] == "invalid_content_length"
    assert not app_called
