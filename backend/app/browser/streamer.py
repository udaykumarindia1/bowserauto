"""Live browser streaming via the Chrome DevTools ``Page.startScreencast`` API.

Chromium only emits a frame when the compositor produces a new one, so a static page costs
nothing. We additionally throttle to ``STREAM_MAX_FPS`` and resize to ``STREAM_MAX_WIDTH`` to
keep bandwidth predictable. Frames are JPEG (base64) published on the session's frame channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from playwright.async_api import Page

from app.browser.playwright_driver import PlaywrightBrowser
from app.core.config import get_settings
from app.realtime.bus import EventBus, frames_channel

logger = logging.getLogger(__name__)


class ScreencastStreamer:
    def __init__(self, browser: PlaywrightBrowser, session_id: str, bus: EventBus) -> None:
        self.browser = browser
        self.session_id = session_id
        self.bus = bus
        self.settings = get_settings()
        self._page: Page | None = None
        self._cdp: Any = None
        self._seq = 0
        self._last_sent = 0.0
        self._last_frame: dict[str, Any] | None = None
        self._running = False
        self._min_interval = 1.0 / max(1, self.settings.stream_max_fps)
        self._lock = asyncio.Lock()
        self.viewers = 0  # number of connected UI viewers (reported by API); informational
        self.on_frame: list = []

    async def start(self) -> None:
        self._running = True
        self.browser.on_page_changed.append(self._on_page_changed)
        await self._attach(self.browser.page)

    async def stop(self) -> None:
        self._running = False
        with contextlib.suppress(ValueError):
            self.browser.on_page_changed.remove(self._on_page_changed)
        await self._detach()

    async def _on_page_changed(self, page: Page | None) -> None:
        if page is None or not self._running:
            return
        await self._attach(page)

    async def _attach(self, page: Page) -> None:
        async with self._lock:
            if self._page is page:
                return
            await self._detach()
            self._page = page
            try:
                self._cdp = await page.context.new_cdp_session(page)
                self._cdp.on("Page.screencastFrame", self._handle_frame)
                await self._cdp.send(
                    "Page.startScreencast",
                    {"format": "jpeg", "quality": self.settings.stream_quality, "maxWidth": self.settings.stream_max_width, "maxHeight": 1600, "everyNthFrame": 1},
                )
            except Exception as exc:
                logger.warning("Could not start screencast: %s", exc)
                self._cdp = None

    async def _detach(self) -> None:
        if self._cdp is not None:
            with contextlib.suppress(Exception):
                await self._cdp.send("Page.stopScreencast")
            with contextlib.suppress(Exception):
                await self._cdp.detach()
        self._cdp = None
        self._page = None

    def _handle_frame(self, params: dict[str, Any]) -> None:
        asyncio.ensure_future(self._process_frame(params))

    async def _process_frame(self, params: dict[str, Any]) -> None:
        cdp = self._cdp
        if cdp is not None:
            with contextlib.suppress(Exception):
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        now = time.monotonic()
        metadata = params.get("metadata", {})
        frame = {
            "type": "frame",
            "session_id": self.session_id,
            "seq": self._seq,
            "data": params["data"],
            "width": metadata.get("deviceWidth"),
            "height": metadata.get("deviceHeight"),
            "page_scale": metadata.get("pageScaleFactor", 1),
            "scroll_x": metadata.get("scrollOffsetX", 0),
            "scroll_y": metadata.get("scrollOffsetY", 0),
            "ts": time.time(),
        }
        self._seq += 1
        self._last_frame = frame
        if now - self._last_sent < self._min_interval:
            # Throttled: keep the latest frame and flush it shortly after.
            asyncio.ensure_future(self._flush_later(frame["seq"]))
            return
        await self._publish(frame)

    async def _flush_later(self, seq: int) -> None:
        await asyncio.sleep(self._min_interval)
        if self._last_frame and self._last_frame["seq"] == seq and time.monotonic() - self._last_sent >= self._min_interval:
            await self._publish(self._last_frame)

    async def _publish(self, frame: dict[str, Any]) -> None:
        self._last_sent = time.monotonic()
        await self.bus.publish(frames_channel(self.session_id), frame)
        for cb in list(self.on_frame):
            with contextlib.suppress(Exception):
                cb(frame)

    async def force_frame(self) -> None:
        """Capture and publish a screenshot immediately (used when a viewer connects)."""
        try:
            page = self.browser.page
            data = await page.screenshot(type="jpeg", quality=self.settings.stream_quality, timeout=10_000)
            import base64

            size = page.viewport_size or {"width": self.settings.browser_viewport_width, "height": self.settings.browser_viewport_height}
            frame = {"type": "frame", "session_id": self.session_id, "seq": self._seq, "data": base64.b64encode(data).decode(), "width": size["width"], "height": size["height"], "page_scale": 1, "scroll_x": 0, "scroll_y": 0, "ts": time.time()}
            self._seq += 1
            await self._publish(frame)
        except Exception as exc:
            logger.debug("force_frame failed: %s", exc)

    @property
    def last_frame(self) -> dict[str, Any] | None:
        return self._last_frame
