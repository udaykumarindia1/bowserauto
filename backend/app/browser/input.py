"""Human takeover input: translate UI input events into real browser input.

The frontend sends normalised coordinates (0..1 relative to the rendered frame) which are
converted to viewport pixels. Keyboard events use ``KeyboardEvent.key`` values, which
Playwright's keyboard API understands directly ("a", "Enter", "Shift", "ArrowLeft", ...).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError

from app.browser.playwright_driver import PlaywrightBrowser

logger = logging.getLogger(__name__)

BUTTONS = {0: "left", 1: "middle", 2: "right", "left": "left", "middle": "middle", "right": "right"}
MODIFIER_KEYS = {"Shift", "Control", "Alt", "Meta"}
BLOCKED_KEYS = {"F5", "F11", "F12"}  # would affect the headless browser itself rather than the page


class InputController:
    def __init__(self, browser: PlaywrightBrowser) -> None:
        self.browser = browser
        self._pressed_keys: set[str] = set()
        self._mouse_down = False

    def _viewport(self) -> tuple[int, int]:
        page = self.browser.page
        size = page.viewport_size or {"width": 1280, "height": 800}
        return size["width"], size["height"]

    def _coords(self, event: dict[str, Any]) -> tuple[float, float]:
        width, height = self._viewport()
        if "nx" in event and "ny" in event:
            return max(0.0, min(1.0, float(event["nx"]))) * width, max(
                0.0, min(1.0, float(event["ny"]))
            ) * height
        return float(event.get("x", 0)), float(event.get("y", 0))

    async def handle(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        page = self.browser.page
        try:
            if kind == "mousemove":
                x, y = self._coords(event)
                await page.mouse.move(x, y)
            elif kind == "mousedown":
                x, y = self._coords(event)
                await page.mouse.move(x, y)
                await page.mouse.down(
                    button=BUTTONS.get(event.get("button", 0), "left"),
                    click_count=int(event.get("click_count", 1)),
                )
                self._mouse_down = True
            elif kind == "mouseup":
                x, y = self._coords(event)
                await page.mouse.move(x, y)
                await page.mouse.up(
                    button=BUTTONS.get(event.get("button", 0), "left"),
                    click_count=int(event.get("click_count", 1)),
                )
                self._mouse_down = False
            elif kind == "click":
                x, y = self._coords(event)
                await page.mouse.click(
                    x,
                    y,
                    button=BUTTONS.get(event.get("button", 0), "left"),
                    click_count=int(event.get("click_count", 1)),
                )
            elif kind == "wheel":
                x, y = self._coords(event)
                await page.mouse.move(x, y)
                await page.mouse.wheel(float(event.get("delta_x", 0)), float(event.get("delta_y", 0)))
            elif kind == "keydown":
                key = str(event.get("key", ""))
                if not key or key in BLOCKED_KEYS:
                    return
                if key == "Unidentified":
                    return
                await page.keyboard.down(key)
                self._pressed_keys.add(key)
            elif kind == "keyup":
                key = str(event.get("key", ""))
                if not key or key in BLOCKED_KEYS:
                    return
                await page.keyboard.up(key)
                self._pressed_keys.discard(key)
            elif kind == "text":
                await page.keyboard.insert_text(str(event.get("text", "")))
            elif kind == "paste":
                await page.keyboard.insert_text(str(event.get("text", ""))[:20_000])
            elif kind == "navigate":
                url = str(event.get("url", "")).strip()
                if url and not url.lower().startswith(("javascript:", "data:", "file:")):
                    if not url.startswith(("http://", "https://", "about:")):
                        url = "https://" + url
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    except PlaywrightError as exc:
                        logger.info("navigation during takeover failed: %s", str(exc)[:120])
            elif kind == "back":
                await page.go_back(wait_until="domcontentloaded", timeout=30_000)
            elif kind == "forward":
                await page.go_forward(wait_until="domcontentloaded", timeout=30_000)
            elif kind == "reload":
                await page.reload(wait_until="domcontentloaded", timeout=45_000)
            elif kind == "switch_tab":
                pages = self.browser.pages
                idx = int(event.get("index", 0))
                if 0 <= idx < len(pages):
                    self.browser.set_active_page(pages[idx])
                    await pages[idx].bring_to_front()
            elif kind == "new_tab":
                assert self.browser.context is not None
                new_page = await self.browser.context.new_page()
                self.browser._register_page(new_page)
                self.browser.set_active_page(new_page)
                if event.get("url"):
                    await self.handle({"type": "navigate", "url": event["url"]})
            elif kind == "close_tab":
                pages = self.browser.pages
                if len(pages) > 1:
                    await page.close()
            elif kind == "release_keys":
                await self.release_all()
        except PlaywrightError as exc:
            logger.debug("takeover input error (%s): %s", kind, str(exc)[:160])

    async def upload_file(self, path: Path) -> bool:
        """Provide a file to the pending file chooser (opened by a human click) or the first file input."""
        chooser = self.browser.pending_file_chooser
        if chooser is not None:
            try:
                await chooser.set_files(str(path))
                self.browser.pending_file_chooser = None
                return True
            except PlaywrightError as exc:
                logger.info("file chooser set_files failed: %s", exc)
                self.browser.pending_file_chooser = None
        page = self.browser.page
        try:
            inputs = page.locator("input[type=file]")
            if await inputs.count() > 0:
                await inputs.first.set_input_files(str(path))
                return True
        except PlaywrightError as exc:
            logger.info("set_input_files failed: %s", exc)
        return False

    async def release_all(self) -> None:
        page = self.browser.page
        for key in list(self._pressed_keys):
            try:
                await page.keyboard.up(key)
            except PlaywrightError:
                pass
        self._pressed_keys.clear()
        if self._mouse_down:
            try:
                await page.mouse.up()
            except PlaywrightError:
                pass
            self._mouse_down = False
