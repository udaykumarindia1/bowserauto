"""ASGI middleware that caps request bodies before multipart parsing or route handling."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyLimitExceeded(Exception):
    """Raised internally when a streamed request crosses the configured byte limit."""


class RequestBodySizeLimitMiddleware:
    """Reject oversized HTTP request bodies using both declared and streamed byte counts.

    Content-Length is checked before entering the application. Bodies without a trustworthy
    Content-Length are counted as the ASGI server yields chunks, so chunked uploads cannot evade
    the cap. The middleware does not buffer request bodies in memory.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_lengths = [
            value for name, value in scope.get("headers", []) if name.lower() == b"content-length"
        ]
        if content_lengths:
            try:
                parsed_lengths = {int(value) for value in content_lengths}
            except ValueError:
                await self._reject(scope, receive, send, status_code=400, code="invalid_content_length")
                return
            if len(parsed_lengths) != 1 or next(iter(parsed_lengths)) < 0:
                await self._reject(scope, receive, send, status_code=400, code="invalid_content_length")
                return
            if next(iter(parsed_lengths)) > self.max_bytes:
                await self._reject(scope, receive, send)
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    raise _BodyLimitExceeded
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyLimitExceeded:
            # Upload endpoints parse the complete body before returning a response. For routes
            # that start streaming a response without consuming the request, we cannot replace
            # its already-sent status; terminate its body rather than emit a second response.
            if response_started:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
            await self._reject(scope, receive, send)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int = 413,
        code: str = "payload_too_large",
    ) -> None:
        if status_code == 413:
            message = "Request body exceeds the configured size limit"
            details = {"max_bytes": self.max_bytes}
        else:
            message = "Content-Length header is invalid or ambiguous"
            details = {}
        response = JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "details": details,
                }
            },
            status_code=status_code,
        )
        await response(scope, receive, send)
