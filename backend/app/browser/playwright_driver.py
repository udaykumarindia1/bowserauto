"""Playwright implementation of the browser driver + persistent browser management."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import time
from pathlib import Path
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Download,
    ElementHandle,
    FileChooser,
    Locator,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeout,
)

from app.browser.dom_script import ELEMENT_BY_INDEX_SCRIPT, INTERACTIVE_ELEMENTS_SCRIPT, PAGE_TEXT_SCRIPT
from app.browser.driver import (
    DownloadResult,
    InteractiveElement,
    PageSnapshot,
    TargetSpec,
    describe_target,
    normalize_target,
)
from app.browser.launcher import LaunchConfig, resolve_launch_config
from app.core.config import get_settings
from app.core.errors import BrowserCrashed, DownloadFailed, ElementNotFound

logger = logging.getLogger(__name__)


def _regex_or_str(value: str) -> str | re.Pattern[str]:
    if isinstance(value, str) and len(value) > 2 and value.startswith("/") and value.rfind("/") > 0:
        body = value[1 : value.rfind("/")]
        flags = value[value.rfind("/") + 1 :]
        return re.compile(body, re.I if "i" in flags else 0)
    return value


class PlaywrightBrowser:
    """A persistent Chromium context bound to one browser session profile."""

    def __init__(
        self,
        *,
        profile_dir: Path,
        downloads_dir: Path,
        headless: bool | None = None,
        launch_config: LaunchConfig | None = None,
    ) -> None:
        self.profile_dir = profile_dir
        self.downloads_dir = downloads_dir
        self.launch_config = launch_config or resolve_launch_config()
        self.headless = self.launch_config.headless if headless is None else headless
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self._pages: list[Page] = []
        self._active_page: Page | None = None
        self.on_page_changed: list = []  # callbacks(page)
        self.on_file_chooser: list = []  # callbacks(file_chooser)
        self.on_download: list = []  # callbacks(download)
        self.pending_file_chooser: FileChooser | None = None
        self.closed = False
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- lifecycle
    async def launch(self) -> None:
        settings = get_settings()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self.profile_dir.chmod(0o700)
        self._playwright = await async_playwright().start()
        kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "args": list(self.launch_config.args),
            "viewport": {
                "width": settings.browser_viewport_width,
                "height": settings.browser_viewport_height,
            },
            "accept_downloads": True,
            "downloads_path": str(self.downloads_dir),
            "ignore_default_args": ["--enable-automation"],
            "timeout": settings.browser_launch_timeout_seconds * 1000,
            "locale": "en-US",
        }
        if self.launch_config.executable_path:
            kwargs["executable_path"] = self.launch_config.executable_path
        if self.launch_config.env:
            import os

            kwargs["env"] = {**os.environ, **self.launch_config.env}
        try:
            self.context = await self._playwright.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:
            await self._shutdown_playwright()
            raise BrowserCrashed(f"Failed to launch Chromium: {str(exc)[:400]}") from exc
        self.context.set_default_timeout(settings.action_timeout_seconds * 1000)
        self.context.on("page", self._on_new_page)
        self.context.on("close", lambda _: self._mark_closed())
        for page in self.context.pages:
            self._register_page(page)
        if not self._pages:
            self._register_page(await self.context.new_page())
        self._active_page = self._pages[0]

    def _mark_closed(self) -> None:
        self.closed = True

    def _on_new_page(self, page: Page) -> None:
        self._register_page(page)
        # New tabs opened by the site become active (matches what a human would see).
        self._active_page = page
        self._notify_page_changed()

    def _register_page(self, page: Page) -> None:
        if page in self._pages:
            return
        self._pages.append(page)
        page.on("close", lambda: self._on_page_closed(page))
        page.on("filechooser", self._on_file_chooser)
        page.on("download", self._on_download)
        page.on("crash", lambda: logger.error("Page crashed: %s", page.url))

    def _on_page_closed(self, page: Page) -> None:
        with contextlib.suppress(ValueError):
            self._pages.remove(page)
        if self._active_page is page:
            self._active_page = self._pages[-1] if self._pages else None
            self._notify_page_changed()

    def _on_file_chooser(self, chooser: FileChooser) -> None:
        self.pending_file_chooser = chooser
        for cb in list(self.on_file_chooser):
            with contextlib.suppress(Exception):
                res = cb(chooser)
                if asyncio.iscoroutine(res):
                    asyncio.ensure_future(res)

    def _on_download(self, download: Download) -> None:
        for cb in list(self.on_download):
            with contextlib.suppress(Exception):
                res = cb(download)
                if asyncio.iscoroutine(res):
                    asyncio.ensure_future(res)

    def _notify_page_changed(self) -> None:
        for cb in list(self.on_page_changed):
            with contextlib.suppress(Exception):
                res = cb(self._active_page)
                if asyncio.iscoroutine(res):
                    asyncio.ensure_future(res)

    @property
    def page(self) -> Page:
        if self.closed or self.context is None:
            raise BrowserCrashed("Browser is closed")
        if self._active_page is None or self._active_page.is_closed():
            live = [p for p in self._pages if not p.is_closed()]
            if not live:
                raise BrowserCrashed("All browser tabs were closed")
            self._active_page = live[-1]
        return self._active_page

    @property
    def pages(self) -> list[Page]:
        return [p for p in self._pages if not p.is_closed()]

    async def ensure_page(self) -> Page:
        try:
            return self.page
        except BrowserCrashed:
            if self.context is None or self.closed:
                raise
            page = await self.context.new_page()
            self._register_page(page)
            self._active_page = page
            self._notify_page_changed()
            return page

    def set_active_page(self, page: Page) -> None:
        if page in self._pages:
            self._active_page = page
            self._notify_page_changed()

    async def storage_state(self) -> dict[str, Any] | None:
        if self.context is None or self.closed:
            return None
        try:
            return await self.context.storage_state()
        except Exception:
            return None

    async def close(self) -> None:
        if self.closed:
            await self._shutdown_playwright()
            return
        self.closed = True
        try:
            if self.context is not None:
                await asyncio.wait_for(self.context.close(), timeout=20)
        except Exception as exc:
            logger.warning("Error closing browser context: %s", exc)
        finally:
            await self._shutdown_playwright()

    async def _shutdown_playwright(self) -> None:
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    def is_alive(self) -> bool:
        return not self.closed and self.context is not None and bool(self.pages)


class PlaywrightDriver:
    """BrowserDriver implementation operating on the browser's active page."""

    def __init__(self, browser: PlaywrightBrowser) -> None:
        self.browser = browser
        self.settings = get_settings()
        self._cdp_sessions: dict[Page, Any] = {}
        self.last_snapshot: PageSnapshot | None = None

    @property
    def page(self) -> Page:
        return self.browser.page

    def _timeout(self, timeout_ms: int | None) -> int:
        return timeout_ms if timeout_ms is not None else self.settings.action_timeout_seconds * 1000

    # ---------------------------------------------------------------- navigation
    async def goto(self, url: str, *, wait_until: str = "load", timeout_ms: int | None = None) -> None:
        page = await self.browser.ensure_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=self._timeout(timeout_ms) * 2)  # type: ignore[arg-type]
        except PlaywrightTimeout:
            # Many SPA platforms never reach "load" because of long polling; continue with what we have.
            logger.info("Navigation to %s timed out waiting for %s; continuing", url, wait_until)
        except PlaywrightError as exc:
            if "ERR_ABORTED" in str(exc):
                return
            raise self._wrap(exc) from exc

    async def back(self) -> None:
        with contextlib.suppress(PlaywrightTimeout):
            await self.page.go_back(timeout=self._timeout(None))

    async def forward(self) -> None:
        with contextlib.suppress(PlaywrightTimeout):
            await self.page.go_forward(timeout=self._timeout(None))

    async def reload(self) -> None:
        with contextlib.suppress(PlaywrightTimeout):
            await self.page.reload(timeout=self._timeout(None) * 2)

    async def url(self) -> str:
        try:
            return self.page.url
        except BrowserCrashed:
            return ""

    async def title(self) -> str:
        try:
            return await self.page.title()
        except Exception:
            return ""

    # ------------------------------------------------------------- target lookup
    async def _resolve(
        self, target: TargetSpec, *, timeout_ms: int = 0, must_exist: bool = True
    ) -> Locator | ElementHandle | None:
        spec = normalize_target(target)
        if "any" in spec:
            deadline = time.monotonic() + timeout_ms / 1000
            fallback: Locator | ElementHandle | None = None
            while True:
                for candidate in spec["any"]:
                    found = await self._resolve(candidate, timeout_ms=0, must_exist=False)
                    if found is None:
                        continue
                    # Prefer a visible candidate: hidden alternatives (e.g. a login form that is
                    # display:none while logged in) must not shadow the one actually on screen.
                    visible = False
                    with contextlib.suppress(PlaywrightError):
                        visible = await found.is_visible()  # type: ignore[union-attr]
                    if visible:
                        return found
                    fallback = fallback or found
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.25)
            if fallback is not None:
                return fallback
            if must_exist:
                raise ElementNotFound(f"None of the candidate elements were found: {describe_target(target)}")
            return None
        if "index" in spec:
            handle = await self.page.evaluate_handle(ELEMENT_BY_INDEX_SCRIPT, int(spec["index"]))
            element = handle.as_element()
            if element is None:
                if must_exist:
                    raise ElementNotFound(
                        f"Element [{spec['index']}] is no longer on the page; take a new snapshot"
                    )
                return None
            return element
        locator = self._locator(spec)
        if locator is None:
            if must_exist:
                raise ElementNotFound(f"Unsupported target: {describe_target(target)}")
            return None
        try:
            if timeout_ms > 0:
                await locator.first.wait_for(state="attached", timeout=timeout_ms)
            count = await locator.count()
        except PlaywrightTimeout:
            count = 0
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc
        if count == 0:
            if must_exist:
                raise ElementNotFound(f"Element not found: {describe_target(target)}")
            return None
        if count > 1:
            visible = locator.locator("visible=true")
            with contextlib.suppress(PlaywrightError):
                if await visible.count() > 0:
                    return visible.first
        return locator.first

    def _locator(self, spec: dict[str, Any]) -> Locator | None:
        page = self.page
        if "css" in spec:
            return page.locator(spec["css"])
        if "xpath" in spec:
            return page.locator(f"xpath={spec['xpath']}")
        if "role" in spec:
            name = spec.get("name")
            kwargs: dict[str, Any] = {}
            if name:
                kwargs["name"] = _regex_or_str(name)
                kwargs["exact"] = bool(spec.get("exact", False)) if isinstance(kwargs["name"], str) else False
                if not kwargs["exact"]:
                    kwargs.pop("exact")
            return page.get_by_role(spec["role"], **kwargs)  # type: ignore[arg-type]
        if "text" in spec:
            return page.get_by_text(_regex_or_str(spec["text"]), exact=bool(spec.get("exact", False)))
        if "placeholder" in spec:
            return page.get_by_placeholder(_regex_or_str(spec["placeholder"]))
        if "label" in spec:
            return page.get_by_label(_regex_or_str(spec["label"]))
        if "testid" in spec:
            return page.get_by_test_id(spec["testid"])
        if "title" in spec:
            return page.get_by_title(_regex_or_str(spec["title"]))
        return None

    # --------------------------------------------------------------- interaction
    async def click(self, target: TargetSpec, *, timeout_ms: int | None = None, force: bool = False) -> None:
        timeout = self._timeout(timeout_ms)
        element = await self._resolve(target, timeout_ms=min(timeout, 5000))
        try:
            await element.scroll_into_view_if_needed(timeout=timeout)  # type: ignore[union-attr]
            await element.click(timeout=timeout, force=force)  # type: ignore[union-attr]
        except PlaywrightTimeout as exc:
            if not force:
                # Overlays/animations often intercept pointer events; a forced click is the usual remedy.
                with contextlib.suppress(PlaywrightError):
                    await element.click(timeout=timeout, force=True)  # type: ignore[union-attr]
                    return
            raise ElementNotFound(f"Could not click {describe_target(target)}: {str(exc)[:200]}") from exc
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc

    async def type(
        self,
        target: TargetSpec,
        text: str,
        *,
        clear: bool = True,
        press_enter: bool = False,
        delay_ms: int = 0,
    ) -> None:
        timeout = self._timeout(None)
        element = await self._resolve(target, timeout_ms=min(timeout, 5000))
        try:
            await element.scroll_into_view_if_needed(timeout=timeout)  # type: ignore[union-attr]
            await element.click(timeout=timeout)  # type: ignore[union-attr]
            if clear:
                try:
                    await element.fill("", timeout=timeout)  # type: ignore[union-attr]
                except PlaywrightError:
                    await self.page.keyboard.press("Control+A")
                    await self.page.keyboard.press("Backspace")
            if delay_ms > 0:
                await element.type(text, delay=delay_ms, timeout=timeout)  # type: ignore[union-attr]
            else:
                try:
                    await element.fill(text, timeout=timeout)  # type: ignore[union-attr]
                except PlaywrightError:
                    # contenteditable editors (Draft.js, ProseMirror) frequently reject fill(); type instead.
                    await self.page.keyboard.type(text, delay=5)
            # Trigger frameworks that listen to input events only.
            await self.page.keyboard.press("Shift")
            if press_enter:
                await self.page.keyboard.press("Enter")
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc

    async def press(self, key: str, target: TargetSpec | None = None) -> None:
        try:
            if target is not None:
                element = await self._resolve(target, timeout_ms=3000)
                await element.press(key)  # type: ignore[union-attr]
            else:
                await self.page.keyboard.press(key)
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc

    async def select_option(self, target: TargetSpec, value: str) -> None:
        element = await self._resolve(target, timeout_ms=3000)
        try:
            await element.select_option(value)  # type: ignore[union-attr]
        except PlaywrightError:
            try:
                await element.select_option(label=value)  # type: ignore[union-attr]
            except PlaywrightError as exc:
                # Custom dropdowns: open it and click the option text.
                await element.click()  # type: ignore[union-attr]
                try:
                    await self.click(
                        {"any": [{"role": "option", "name": value}, {"text": value}]}, timeout_ms=5000
                    )
                except ElementNotFound:
                    raise ElementNotFound(f"Option '{value}' not found in {describe_target(target)}") from exc

    async def scroll(
        self, direction: str = "down", amount: int = 600, target: TargetSpec | None = None
    ) -> None:
        dy = amount if direction == "down" else -amount if direction == "up" else 0
        dx = amount if direction == "right" else -amount if direction == "left" else 0
        try:
            if target is not None:
                element = await self._resolve(target, timeout_ms=3000)
                await element.scroll_into_view_if_needed()  # type: ignore[union-attr]
                await element.hover()  # type: ignore[union-attr]
            await self.page.mouse.wheel(dx, dy)
            await asyncio.sleep(0.3)
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc

    async def hover(self, target: TargetSpec) -> None:
        element = await self._resolve(target, timeout_ms=3000)
        await element.hover()  # type: ignore[union-attr]

    async def upload(self, target: TargetSpec | None, files: list[Path]) -> None:
        paths = [str(p) for p in files]
        for p in files:
            if not Path(p).exists():
                raise DownloadFailed(f"Reference file missing: {p}", retryable=False)
        page = self.page
        try:
            if target is None:
                inputs = page.locator("input[type=file]")
                if await inputs.count() > 0:
                    await inputs.first.set_input_files(paths)
                    return
                raise ElementNotFound("No file input found on the page")
            element = await self._resolve(target, timeout_ms=5000)
            tag = await element.evaluate("el => el.tagName.toLowerCase() + ':' + (el.type || '')")  # type: ignore[union-attr]
            if tag == "input:file":
                await element.set_input_files(paths)  # type: ignore[union-attr]
                return
            async with page.expect_file_chooser(timeout=self._timeout(None)) as chooser_info:
                await element.click()  # type: ignore[union-attr]
            chooser = await chooser_info.value
            await chooser.set_files(paths)
        except PlaywrightTimeout as exc:
            raise ElementNotFound(
                f"Upload control did not open a file chooser: {describe_target(target)}"
            ) from exc
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc

    # --------------------------------------------------------------- observation
    async def exists(self, target: TargetSpec, *, timeout_ms: int = 0) -> bool:
        try:
            found = await self._resolve(target, timeout_ms=timeout_ms, must_exist=False)
            if found is None:
                return False
            with contextlib.suppress(PlaywrightError):
                return await found.is_visible()  # type: ignore[union-attr]
            return True
        except (ElementNotFound, PlaywrightError):
            return False

    async def wait_for(
        self, target: TargetSpec, *, state: str = "visible", timeout_ms: int | None = None
    ) -> bool:
        timeout = self._timeout(timeout_ms)
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            visible = await self.exists(target)
            if (state == "visible" and visible) or (state == "hidden" and not visible):
                return True
            await asyncio.sleep(0.4)
        return False

    async def wait_for_text(self, text: str, *, timeout_ms: int | None = None) -> bool:
        timeout = self._timeout(timeout_ms)
        deadline = time.monotonic() + timeout / 1000
        pattern = _regex_or_str(text)
        while time.monotonic() < deadline:
            body = await self.page_text(30_000)
            if pattern.search(body) if isinstance(pattern, re.Pattern) else text.lower() in body.lower():
                return True
            await asyncio.sleep(0.5)
        return False

    async def wait_for_url(self, pattern: str, *, timeout_ms: int | None = None) -> bool:
        try:
            await self.page.wait_for_url(
                _regex_or_str(pattern) if pattern.startswith("/") else f"**{pattern}**",
                timeout=self._timeout(timeout_ms),
            )
            return True
        except PlaywrightTimeout:
            return False

    async def wait_for_load(self, *, state: str = "load", timeout_ms: int | None = None) -> None:
        with contextlib.suppress(PlaywrightTimeout, PlaywrightError):
            await self.page.wait_for_load_state(state, timeout=self._timeout(timeout_ms))  # type: ignore[arg-type]

    async def extract_text(self, target: TargetSpec | None = None) -> str:
        if target is None:
            return await self.page_text()
        element = await self._resolve(target, timeout_ms=3000)
        try:
            return (await element.inner_text()).strip()  # type: ignore[union-attr]
        except PlaywrightError:
            return (await element.text_content() or "").strip()  # type: ignore[union-attr]

    async def extract_attribute(self, target: TargetSpec, attribute: str) -> str | None:
        element = await self._resolve(target, timeout_ms=3000)
        return await element.get_attribute(attribute)  # type: ignore[union-attr]

    async def page_text(self, max_chars: int = 20_000) -> str:
        try:
            return await self.page.evaluate(PAGE_TEXT_SCRIPT, max_chars)
        except PlaywrightError:
            return ""

    async def snapshot(self, *, include_screenshot: bool = False) -> PageSnapshot:
        page = await self.browser.ensure_page()
        try:
            data = await page.evaluate(INTERACTIVE_ELEMENTS_SCRIPT)
        except PlaywrightError as exc:
            if "Execution context was destroyed" in str(exc) or "navigation" in str(exc).lower():
                await asyncio.sleep(0.5)
                data = await page.evaluate(INTERACTIVE_ELEMENTS_SCRIPT)
            else:
                raise self._wrap(exc) from exc
        elements = [
            InteractiveElement(
                index=e["index"],
                tag=e["tag"],
                role=e["role"],
                name=e["name"],
                text=e["text"],
                placeholder=e["placeholder"],
                value=e["value"],
                href=e["href"],
                input_type=e["input_type"],
                disabled=e["disabled"],
                checked=e["checked"],
                bbox=tuple(e["bbox"]) if e.get("bbox") else None,
                selector=e.get("selector", ""),
            )
            for e in data.get("elements", [])
        ]
        snap = PageSnapshot(
            url=data.get("url", page.url),
            title=data.get("title", ""),
            text=data.get("text", ""),
            elements=elements,
            loading=bool(data.get("loading")),
            scroll=tuple(data.get("scroll", (0, 0, 0))),
            tabs=await self.list_tabs(),
            file_inputs=int(data.get("file_inputs", 0)),
        )
        if include_screenshot:
            with contextlib.suppress(Exception):
                snap.screenshot_b64 = base64.b64encode(await self.screenshot(quality=45)).decode()
        self.last_snapshot = snap
        return snap

    async def accessibility_tree(self, max_nodes: int = 400) -> list[dict[str, Any]]:
        page = self.page
        try:
            cdp = await self._cdp(page)
            result = await cdp.send("Accessibility.getFullAXTree")
        except Exception as exc:
            logger.debug("accessibility tree unavailable: %s", exc)
            return []
        nodes = []
        for node in result.get("nodes", []):
            if node.get("ignored"):
                continue
            role = (node.get("role") or {}).get("value", "")
            name = (node.get("name") or {}).get("value", "")
            value = (node.get("value") or {}).get("value", "")
            if role in ("none", "generic", "InlineTextBox", "StaticText") and not name:
                continue
            nodes.append({"role": role, "name": name, "value": value, "id": node.get("nodeId")})
            if len(nodes) >= max_nodes:
                break
        return nodes

    async def _cdp(self, page: Page):
        session = self._cdp_sessions.get(page)
        if session is None:
            session = await page.context.new_cdp_session(page)
            self._cdp_sessions[page] = session
        return session

    async def screenshot(self, *, full_page: bool = False, quality: int = 60) -> bytes:
        try:
            return await self.page.screenshot(
                type="jpeg", quality=quality, full_page=full_page, timeout=15_000
            )
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc

    async def evaluate(self, expression: str) -> Any:
        try:
            return await self.page.evaluate(expression)
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc

    # ---------------------------------------------------------- downloads / tabs
    async def download(
        self, target: TargetSpec | None, *, timeout_ms: int | None = None, trigger: Any = None
    ) -> DownloadResult:
        timeout = timeout_ms if timeout_ms is not None else 120_000
        page = self.page
        try:
            async with page.expect_download(timeout=timeout) as info:
                if trigger is not None:
                    await trigger()
                elif target is not None:
                    await self.click(target)
            download = await info.value
            failure = await download.failure()
            if failure:
                raise DownloadFailed(f"Download failed: {failure}")
            path = await download.path()
            if path is None:
                raise DownloadFailed("Download produced no file")
            return DownloadResult(
                path=Path(path),
                suggested_filename=download.suggested_filename,
                url=download.url,
                size_bytes=Path(path).stat().st_size,
            )
        except PlaywrightTimeout as exc:
            raise DownloadFailed("No download started within the timeout") from exc
        except PlaywrightError as exc:
            raise self._wrap(exc) from exc

    async def fetch_to_file(self, url: str, path: Path) -> int:
        """Fetch a URL with the page's cookies/session and store it on disk."""
        try:
            response = await self.page.request.get(url, timeout=180_000)
            if not response.ok:
                raise DownloadFailed(f"Fetching {url[:80]} failed with HTTP {response.status}")
            body = await response.body()
        except PlaywrightError as exc:
            raise DownloadFailed(f"Fetching media failed: {str(exc)[:200]}") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return len(body)

    async def list_tabs(self) -> list[dict[str, Any]]:
        tabs = []
        active = None
        with contextlib.suppress(BrowserCrashed):
            active = self.browser.page
        for i, p in enumerate(self.browser.pages):
            title = ""
            with contextlib.suppress(Exception):
                title = await p.title()
            tabs.append({"index": i, "url": p.url, "title": title, "active": p is active})
        return tabs

    async def switch_tab(self, index: int) -> None:
        pages = self.browser.pages
        if index < 0 or index >= len(pages):
            raise ElementNotFound(f"Tab {index} does not exist")
        self.browser.set_active_page(pages[index])
        with contextlib.suppress(Exception):
            await pages[index].bring_to_front()

    async def close_tab(self, index: int | None = None) -> None:
        pages = self.browser.pages
        if not pages:
            return
        page = pages[index] if index is not None and 0 <= index < len(pages) else self.browser.page
        if len(pages) == 1:
            await page.goto("about:blank")
            return
        await page.close()

    async def new_tab(self, url: str | None = None) -> None:
        assert self.browser.context is not None
        page = await self.browser.context.new_page()
        self.browser._register_page(page)
        self.browser.set_active_page(page)
        if url:
            await self.goto(url)

    # -------------------------------------------------------------------- errors
    def _wrap(self, exc: Exception) -> Exception:
        message = str(exc)
        if (
            "Target page, context or browser has been closed" in message
            or "Target closed" in message
            or "browser has been closed" in message
        ):
            return BrowserCrashed("The browser or tab was closed unexpectedly")
        if "crashed" in message.lower():
            return BrowserCrashed("The page crashed")
        return ElementNotFound(message[:300])
