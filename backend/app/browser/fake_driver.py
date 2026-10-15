"""In-memory browser backend used by the test-suite and CI.

It simulates a small "video generator" web application that mirrors the built-in mock
platform (login wall, prompt box, reference upload, generate button, status text that
progresses QUEUED → GENERATING → COMPLETED, download link, optional "verify you are human"
challenge) without a real browser, so queue / retry / takeover / file logic can be tested
quickly and deterministically.

Three pieces are provided so :class:`app.workers.session_host.SessionHost` can run unchanged:

* :class:`FakeDriver`  – implements the :class:`app.browser.driver.BrowserDriver` protocol,
* :class:`FakeBrowser` – stands in for ``PlaywrightBrowser`` (launch/close/is_alive/storage_state),
* :class:`FakeStreamer` / :class:`FakeInput` – stand-ins for the screencast streamer and input
  controller (they publish tiny frames / record input events so WebSocket plumbing is testable).

Tests obtain the simulated site for a session through :func:`site_for` / :func:`configure_sites`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.browser.driver import (
    DownloadResult,
    InteractiveElement,
    PageSnapshot,
    TargetSpec,
    describe_target,
    normalize_target,
)
from app.core.errors import BrowserCrashed, ElementNotFound

SAMPLE_MP4 = Path(__file__).resolve().parents[1] / "mock_platform" / "static" / "sample.mp4"

# 1x1 white JPEG used as the "frame" payload of the fake streamer.
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
)


class FakeVideoSite:
    """State machine of the simulated platform."""

    def __init__(
        self,
        *,
        generation_seconds: float = 0.3,
        fail_first_attempts: int = 0,
        require_login: bool = False,
        human_check_on_attempt: int | None = None,
        queue_seconds: float = 0.0,
    ) -> None:
        self.generation_seconds = generation_seconds
        self.queue_seconds = queue_seconds
        self.fail_first_attempts = fail_first_attempts
        self.require_login = require_login
        self.human_check_on_attempt = human_check_on_attempt
        self.logged_in = not require_login
        self.prompt = ""
        self.uploaded: list[str] = []
        self.state = "IDLE"
        self.started_at = 0.0
        self.attempts = 0
        self.human_check_pending = False
        self.human_verified = False
        self.history: list[str] = []
        self.downloads = 0
        self.selected: dict[str, str] = {}

    # ----------------------------------------------------------------- behaviour
    def tick(self) -> None:
        if self.state == "QUEUED" and time.monotonic() - self.started_at >= self.queue_seconds:
            self.state = "GENERATING"
        if self.state == "GENERATING" and time.monotonic() - self.started_at >= self.queue_seconds + self.generation_seconds:
            self.state = "FAILED" if self.attempts <= self.fail_first_attempts else "COMPLETED"

    def generate(self) -> None:
        if not self.prompt.strip():
            raise ElementNotFound("Generate button is disabled (empty prompt)")
        self.attempts += 1
        if self.human_check_on_attempt and self.attempts == self.human_check_on_attempt and not self.human_verified:
            self.human_check_pending = True
            self.state = "IDLE"
            return
        self.state = "QUEUED" if self.queue_seconds > 0 else "GENERATING"
        self.started_at = time.monotonic()
        self.history.append(f"generate:{self.prompt[:30]}")

    def apply_url(self, url: str) -> None:
        """Honour the same query options as the real mock platform (``?login=1`` etc.)."""
        query = parse_qs(urlsplit(url).query)
        if query.get("login", [""])[0] == "1":
            self.require_login = True
            if not self.logged_in:
                self.logged_in = False
        elif not self.require_login:
            self.logged_in = True
        self.reset()

    def reset(self) -> None:
        self.state = "IDLE"
        self.prompt = ""
        self.uploaded = []

    @property
    def login_wall(self) -> bool:
        return self.require_login and not self.logged_in

    def status_text(self) -> str:
        return {
            "IDLE": "Status: ready",
            "QUEUED": "Status: queued\nYour request is queued",
            "GENERATING": "Status: generating\nGenerating your video…",
            "COMPLETED": "Status: completed\nGeneration complete",
            "FAILED": "Status: failed\nGeneration failed: server error, please retry",
        }[self.state]


@dataclass
class FakeElement(InteractiveElement):
    testid: str = ""


# --------------------------------------------------------------------------- site registry
_sites: dict[str, FakeVideoSite] = {}
_site_factory: Callable[[str], FakeVideoSite] = lambda _sid: FakeVideoSite()  # noqa: E731


def configure_sites(factory: Callable[[str], FakeVideoSite] | None) -> None:
    """Install a factory used to build the simulated site of newly launched sessions."""
    global _site_factory
    _site_factory = factory or (lambda _sid: FakeVideoSite())
    _sites.clear()


def site_for(session_id: str) -> FakeVideoSite:
    site = _sites.get(session_id)
    if site is None:
        site = _sites[session_id] = _site_factory(session_id)
    return site


def all_sites() -> dict[str, FakeVideoSite]:
    return dict(_sites)


# --------------------------------------------------------------------------------- driver
class FakeDriver:
    def __init__(self, site: FakeVideoSite | None = None, downloads_dir: Path | None = None) -> None:
        self.site = site or FakeVideoSite()
        self.downloads_dir = downloads_dir or Path("/tmp/bowser-fake-downloads")
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._url = "about:blank"
        self.actions: list[tuple[str, Any]] = []
        self.last_snapshot: PageSnapshot | None = None
        self.tabs: list[dict[str, Any]] = [{"index": 0, "url": self._url, "title": "", "active": True}]
        self.crash_next = False
        self.alive = True
        # Navigation listeners (the real backend fires Playwright's ``framenavigated``).
        self.on_navigated: list[Callable[[str], None]] = []

    def _navigated(self) -> None:
        for callback in list(self.on_navigated):
            try:
                callback(self._url)
            except Exception:  # pragma: no cover - listener bugs must not break navigation
                pass

    # ------------------------------------------------------------------ page model
    def _elements(self) -> list[FakeElement]:
        self.site.tick()
        if self._url == "about:blank":
            return []
        if self.site.login_wall:
            return [
                FakeElement(0, "section", "region", "Login", selector="#loginView"),
                FakeElement(1, "input", "textbox", "Email", placeholder="Email", input_type="email", selector="#email"),
                FakeElement(2, "input", "textbox", "Password", placeholder="Password", input_type="password", selector="#password"),
                FakeElement(3, "button", "button", "Sign in", text="Sign in", selector="#loginBtn"),
            ]
        els: list[FakeElement] = [
            FakeElement(0, "main", "main", "App", selector="#appView"),
            FakeElement(1, "textarea", "textbox", "Prompt", placeholder="Describe the video you want to generate", value=self.site.prompt, selector="#prompt", testid="prompt"),
            FakeElement(2, "input", "file", "Reference image", input_type="file", selector="input[type=file]"),
            FakeElement(3, "button", "button", "5s", text="5s", selector="#durations [data-duration='5']"),
            FakeElement(4, "button", "button", "8s", text="8s", selector="#durations [data-duration='8']"),
            FakeElement(5, "button", "button", "16:9", text="16:9", selector="#ratios [data-ratio='16:9']"),
            FakeElement(6, "button", "button", "9:16", text="9:16", selector="#ratios [data-ratio='9:16']"),
        ]
        if self.site.human_check_pending:
            els.append(FakeElement(7, "div", "region", "Security check", selector="#humanCheck"))
            els.append(FakeElement(8, "input", "checkbox", "Verify you are human", input_type="checkbox", checked=False, selector="#humanCheckbox"))
        els.append(FakeElement(9, "button", "button", "Generate", text="Generate", disabled=self.site.state in ("QUEUED", "GENERATING") or self.site.human_check_pending, selector="#generate", testid="generate"))
        if self.site.state == "COMPLETED":
            els.append(FakeElement(10, "video", "video", "Result video", selector="#result video"))
            els.append(FakeElement(11, "a", "link", "Download video", text="Download video", href="/mock-platform/api/download/clip.mp4", selector="a#download"))
        if self.site.state == "FAILED":
            els.append(FakeElement(12, "div", "status", "Failed", text="Generation failed: server error, please retry", selector=".status-failed"))
            els.append(FakeElement(13, "button", "button", "Retry", text="Retry", selector="#retry"))
        return els

    def _text(self) -> str:
        self.site.tick()
        if self._url == "about:blank":
            return ""
        if self.site.login_wall:
            return "MockVid Studio\nSign in to continue\nAny email and password work."
        lines = ["MockVid Studio – AI Video Generator (Demo)", self.site.status_text(), f"Uploads: {len(self.site.uploaded)}"]
        if self.site.human_check_pending:
            lines.insert(1, "Security check required\nVerify you are human")
        return "\n".join(lines)

    @staticmethod
    def _pattern(needle: str) -> re.Pattern[str] | None:
        if needle.startswith("/") and needle.rfind("/") > 0:
            body = needle[1 : needle.rfind("/")]
            flags = needle[needle.rfind("/") + 1 :]
            return re.compile(body, re.I if "i" in flags else 0)
        return None

    def _candidates(self, spec: dict[str, Any]) -> list[FakeElement]:
        elements = self._elements()
        role = spec.get("role")
        if role:
            elements = [e for e in elements if e.role == role or (role == "button" and e.role == "link")]
        return elements

    def _match(self, target: TargetSpec) -> FakeElement:
        spec = normalize_target(target)
        if "any" in spec:
            for candidate in spec["any"]:
                try:
                    return self._match(candidate)
                except ElementNotFound:
                    continue
            raise ElementNotFound(f"None of the candidate elements were found: {describe_target(target)}")
        elements = self._candidates(spec)
        if "index" in spec:
            for el in elements:
                if el.index == int(spec["index"]):
                    return el
            raise ElementNotFound(f"Element [{spec['index']}] is no longer on the page; take a new snapshot")
        if "testid" in spec:
            for el in elements:
                if el.testid == str(spec["testid"]):
                    return el
            raise ElementNotFound(f"Element not found: {describe_target(target)}")
        if "css" in spec:
            css = re.sub(r":not\([^)]*\)", "", str(spec["css"])).strip().lower()
            for el in elements:
                sel = el.selector.lower()
                if css == sel or (css.startswith("#") and sel == css.split()[0] and " " not in css):
                    return el
            for el in elements:
                if css in ("button", "a", "textarea", "video", "input") and el.tag == css:
                    return el
                if css.endswith(el.selector.lower()) and el.selector:
                    return el
            raise ElementNotFound(f"Element not found: {describe_target(target)}")
        needle = str(spec.get("name") or spec.get("text") or spec.get("placeholder") or spec.get("label") or "").strip()
        if not needle:
            if elements and spec.get("role"):
                return elements[0]
            raise ElementNotFound(f"Unsupported target: {describe_target(target)}")
        regex = self._pattern(needle)
        scored: list[tuple[int, FakeElement]] = []
        for el in elements:
            if "placeholder" in spec:
                fields = [el.placeholder]
            else:
                fields = [el.name, el.text, el.placeholder]
            score = 0
            for weight, field in zip((3, 3, 1), fields, strict=False):
                if not field:
                    continue
                if regex is not None:
                    if regex.search(field):
                        score = max(score, weight + (1 if regex.fullmatch(field) else 0))
                elif needle.lower() == field.lower():
                    score = max(score, weight + 1)
                elif needle.lower() in field.lower():
                    score = max(score, weight)
            if score:
                scored.append((score, el))
        if not scored:
            raise ElementNotFound(f"Element not found: {describe_target(target)}")
        scored.sort(key=lambda item: (-item[0], item[1].index))
        return scored[0][1]

    def _check_alive(self) -> None:
        if not self.alive:
            raise BrowserCrashed("Browser has been closed")
        if self.crash_next:
            self.crash_next = False
            self.alive = False
            raise BrowserCrashed("simulated crash")

    # ------------------------------------------------------------------ interface
    async def goto(self, url: str, *, wait_until: str = "load", timeout_ms: int | None = None) -> None:
        self._check_alive()
        self._url = url
        self.tabs[0]["url"] = url
        self.site.apply_url(url)
        self.actions.append(("goto", url))
        self._navigated()

    async def back(self) -> None:
        self.actions.append(("back", None))

    async def forward(self) -> None:
        self.actions.append(("forward", None))

    async def reload(self) -> None:
        self.actions.append(("reload", None))
        self.site.reset()

    async def url(self) -> str:
        return self._url

    async def title(self) -> str:
        if self._url == "about:blank":
            return ""
        return "MockVid Studio – AI Video Generator (Demo)"

    async def click(self, target: TargetSpec, *, timeout_ms: int | None = None, force: bool = False) -> None:
        self._check_alive()
        el = self._match(target)
        self.actions.append(("click", el.name))
        if el.name == "Sign in":
            self.site.logged_in = True
        elif el.name == "Generate":
            if el.disabled:
                raise ElementNotFound("Generate is disabled")
            self.site.generate()
        elif el.name == "Retry":
            self.site.state = "IDLE"
        elif el.name == "Verify you are human":
            # A human must do this; the automation must never tick it. Simulate the site rejecting it.
            raise ElementNotFound("Automated interaction with the security check is blocked")
        elif el.selector.startswith("#durations"):
            self.site.selected["duration"] = el.text
        elif el.selector.startswith("#ratios"):
            self.site.selected["ratio"] = el.text

    async def type(self, target: TargetSpec, text: str, *, clear: bool = True, press_enter: bool = False, delay_ms: int = 0) -> None:
        self._check_alive()
        el = self._match(target)
        self.actions.append(("type", (el.name, text)))
        if el.name == "Prompt":
            self.site.prompt = text if clear else self.site.prompt + text

    async def press(self, key: str, target: TargetSpec | None = None) -> None:
        self.actions.append(("press", key))

    async def select_option(self, target: TargetSpec, value: str) -> None:
        self.actions.append(("select", value))

    async def scroll(self, direction: str = "down", amount: int = 600, target: TargetSpec | None = None) -> None:
        self.actions.append(("scroll", direction))

    async def hover(self, target: TargetSpec) -> None:
        self.actions.append(("hover", describe_target(target)))

    async def upload(self, target: TargetSpec | None, files: list[Path]) -> None:
        self._check_alive()
        self.actions.append(("upload", [str(f) for f in files]))
        self.site.uploaded.extend(str(f) for f in files)

    async def exists(self, target: TargetSpec, *, timeout_ms: int = 0) -> bool:
        try:
            self._match(target)
            return True
        except ElementNotFound:
            return False

    async def wait_for(self, target: TargetSpec, *, state: str = "visible", timeout_ms: int | None = None) -> bool:
        deadline = time.monotonic() + (timeout_ms or 2000) / 1000
        while True:
            visible = await self.exists(target)
            if (state == "visible") == visible:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.02)

    async def wait_for_text(self, text: str, *, timeout_ms: int | None = None) -> bool:
        deadline = time.monotonic() + (timeout_ms or 2000) / 1000
        while True:
            if text.lower() in self._text().lower():
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.02)

    async def wait_for_url(self, pattern: str, *, timeout_ms: int | None = None) -> bool:
        return pattern in self._url

    async def wait_for_load(self, *, state: str = "load", timeout_ms: int | None = None) -> None:
        return None

    async def extract_text(self, target: TargetSpec | None = None) -> str:
        if target is None:
            return self._text()
        el = self._match(target)
        return el.text or el.name

    async def extract_attribute(self, target: TargetSpec, attribute: str) -> str | None:
        el = self._match(target)
        if attribute == "href":
            return el.href
        if attribute == "src" and el.tag == "video":
            return "/mock-platform/api/download/clip.mp4"
        return None

    async def page_text(self, max_chars: int = 20_000) -> str:
        return self._text()[:max_chars]

    async def snapshot(self, *, include_screenshot: bool = False) -> PageSnapshot:
        snap = PageSnapshot(url=self._url, title=await self.title(), text=self._text(), elements=list(self._elements()), tabs=list(self.tabs), file_inputs=1)
        self.last_snapshot = snap
        return snap

    async def accessibility_tree(self, max_nodes: int = 400) -> list[dict[str, Any]]:
        return [{"role": e.role, "name": e.name, "value": e.value} for e in self._elements()]

    async def screenshot(self, *, full_page: bool = False, quality: int = 60) -> bytes:
        return _TINY_JPEG

    async def evaluate(self, expression: str) -> Any:
        return None

    async def download(self, target: TargetSpec | None, *, timeout_ms: int | None = None, trigger: Any = None) -> DownloadResult:
        self._check_alive()
        if target is not None:
            el = self._match(target)
            if el.name != "Download video":
                raise ElementNotFound("Not a download control")
        self.site.downloads += 1
        path = self.downloads_dir / f"clip-{int(time.time() * 1000)}-{self.site.downloads}.mp4"
        shutil.copyfile(SAMPLE_MP4, path)
        self.actions.append(("download", str(path)))
        return DownloadResult(path=path, suggested_filename="clip.mp4", url=self._url + "download/clip.mp4", size_bytes=path.stat().st_size)

    async def list_tabs(self) -> list[dict[str, Any]]:
        return list(self.tabs)

    async def switch_tab(self, index: int) -> None:
        self.actions.append(("switch_tab", index))

    async def close_tab(self, index: int | None = None) -> None:
        self.actions.append(("close_tab", index))

    async def new_tab(self, url: str | None = None) -> None:
        self.actions.append(("new_tab", url))


# -------------------------------------------------------------------------------- backend
class FakeBrowser:
    """Minimal stand-in for ``PlaywrightBrowser`` used by ``SessionHost`` in tests."""

    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver
        self.on_file_chooser: list[Callable[[Any], None]] = []
        self.on_page_changed: list[Callable[[Any], None]] = []
        self.pages: list[Any] = []
        self.page = None
        self.context = None
        self.pending_file_chooser = None
        self.restored_cookies: list[dict[str, Any]] = []
        self._alive = False
        driver.on_navigated.append(lambda _url: [cb(None) for cb in list(self.on_page_changed)])

    async def launch(self) -> None:
        self._alive = True
        self.driver.alive = True

    async def close(self) -> None:
        self._alive = False
        self.driver.alive = False

    def is_alive(self) -> bool:
        return self._alive and self.driver.alive

    async def storage_state(self) -> dict[str, Any]:
        cookies = [{"name": "mockvid_session", "value": "fake", "domain": "127.0.0.1", "path": "/"}] if self.driver.site.logged_in else []
        return {"cookies": cookies, "origins": []}

    async def restore_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self.restored_cookies = list(cookies)
        if any(c.get("name") == "mockvid_session" for c in cookies):
            self.driver.site.logged_in = True

    def _register_page(self, page: Any) -> None:
        self.pages.append(page)

    def set_active_page(self, page: Any) -> None:
        self.page = page


class FakeStreamer:
    def __init__(self, session_id: str, bus: Any) -> None:
        self.session_id = session_id
        self.bus = bus
        self.frames = 0
        self.running = False

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def force_frame(self) -> None:
        from app.realtime.bus import frames_channel

        self.frames += 1
        payload = {"type": "frame", "session_id": self.session_id, "seq": self.frames, "data": base64.b64encode(_TINY_JPEG).decode(), "width": 1, "height": 1, "format": "jpeg", "ts": time.time()}
        with contextlib.suppress(Exception):
            await self.bus.publish(frames_channel(self.session_id), payload)


class FakeInput:
    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver
        self.events: list[dict[str, Any]] = []
        self.uploads: list[Path] = []

    async def release_all(self) -> None:
        self.events.append({"type": "release_all"})

    async def handle(self, message: dict[str, Any]) -> None:
        self.events.append(dict(message))
        if message.get("type") == "navigate" and message.get("url"):
            await self.driver.goto(str(message["url"]))
        elif message.get("type") == "click" and self.driver.site.human_check_pending:
            # A human clicking the challenge box is allowed: that is the whole point of takeover.
            self.driver.site.human_check_pending = False
            self.driver.site.human_verified = True
        elif message.get("type") == "click" and self.driver.site.login_wall:
            self.driver.site.logged_in = True

    async def upload_file(self, path: Path) -> bool:
        self.uploads.append(path)
        self.driver.site.uploaded.append(str(path))
        return True
