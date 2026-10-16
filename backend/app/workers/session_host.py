"""SessionHost: owns one live browser (persistent profile) inside a worker process.

Responsibilities
----------------
* launch/close the Chromium profile for a BrowserSession
* stream frames (CDP screencast) and browser status to the UI via the event bus
* accept human input (takeover) from the input channel
* persist an encrypted storage-state snapshot (cookies/local storage) for reconnects
* hand the driver to the task executor and report the agent's current action
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import update

from app.browser.input import InputController
from app.browser.playwright_driver import PlaywrightBrowser, PlaywrightDriver
from app.browser.streamer import ScreencastStreamer
from app.core.config import get_settings
from app.core.database import session_scope
from app.core.security import decrypt_secret, encrypt_secret
from app.core.url_safety import sanitize_url
from app.models import BrowserSession, BrowserSessionStatus
from app.realtime.bus import EventBus, input_channel, status_channel
from app.realtime.events import emit, publish_event

logger = logging.getLogger(__name__)


class SessionHost:
    def __init__(
        self,
        *,
        session_id: uuid.UUID,
        org_id: uuid.UUID,
        profile_dir: Path,
        bus: EventBus,
        worker_id: str,
        headless: bool | None = None,
        start_url: str = "",
    ) -> None:
        self.session_id = session_id
        self.org_id = org_id
        self.profile_dir = profile_dir
        self.bus = bus
        self.worker_id = worker_id
        self.start_url = start_url
        self.settings = get_settings()
        downloads_dir = self.settings.tmp_dir / "downloads" / str(session_id)
        if self.settings.browser_backend == "fake":
            # In-memory browser used by the automated tests (no Chromium involved).
            from app.browser.fake_driver import FakeBrowser, FakeDriver, FakeInput, FakeStreamer, site_for

            fake_driver = FakeDriver(site_for(str(session_id)), downloads_dir=downloads_dir)
            self.browser: Any = FakeBrowser(fake_driver)
            self.driver: Any = fake_driver
            self.streamer: Any = FakeStreamer(str(session_id), bus)
            self.input: Any = FakeInput(fake_driver)
        else:
            self.browser = PlaywrightBrowser(
                profile_dir=profile_dir, downloads_dir=downloads_dir, headless=headless
            )
            self.driver = PlaywrightDriver(self.browser)
            self.streamer = ScreencastStreamer(self.browser, str(session_id), bus)
            self.input = InputController(self.browser)
        self.interactive = False
        self.in_use_by_task: uuid.UUID | None = None
        self.last_activity = time.monotonic()
        self.current_action = ""
        self.current_task_id: uuid.UUID | None = None
        self.file_chooser_pending = False
        self._input_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        self._subscription = None
        self._last_status: dict[str, Any] = {}
        self._status_dirty = asyncio.Event()
        self.started = False
        self.stopping = False
        self.on_closed: list = []

    # ------------------------------------------------------------------ lifecycle
    async def start(self, url: str | None = None) -> None:
        await self._update_db(
            status=BrowserSessionStatus.launching.value,
            worker_id=self.worker_id,
            error="",
            last_heartbeat_at=_now(),
        )
        try:
            await self.browser.launch()
        except Exception as exc:
            await self._update_db(
                status=BrowserSessionStatus.error.value,
                error=str(exc)[:500],
                worker_id=None,
                desired_state="closed",
            )
            await emit_safe(
                self.org_id,
                "session.updated",
                f"Browser failed to launch: {str(exc)[:160]}",
                session_id=self.session_id,
                level="error",
                payload={"status": "error"},
            )
            raise
        self.started = True
        self.browser.on_file_chooser.append(self._on_file_chooser)
        self.browser.on_page_changed.append(lambda _p: self._status_dirty.set())
        self._attach_page_listeners()
        await self.streamer.start()
        self._subscription = self.bus.subscribe(
            input_channel(self.session_id), maxsize=2000, drop_oldest=False
        )
        self._input_task = asyncio.create_task(self._input_loop(), name=f"input-{self.session_id}")
        self._status_task = asyncio.create_task(self._status_loop(), name=f"status-{self.session_id}")
        target = url or self.start_url
        if target:
            with contextlib.suppress(Exception):
                await self.driver.goto(target, wait_until="domcontentloaded")
        await self._update_db(
            status=BrowserSessionStatus.ready.value, last_heartbeat_at=_now(), last_used_at=_now()
        )
        await emit_safe(
            self.org_id,
            "session.updated",
            "Browser session ready",
            session_id=self.session_id,
            payload={"status": "ready"},
        )
        await self.publish_status(force=True)
        await self.streamer.force_frame()

    def _attach_page_listeners(self) -> None:
        for page in self.browser.pages:
            self._listen(page)
        original = self.browser._register_page

        def register(page):  # noqa: ANN001
            original(page)
            self._listen(page)

        self.browser._register_page = register  # type: ignore[method-assign]

    def _listen(self, page) -> None:  # noqa: ANN001
        if getattr(page, "_ba_listening", False):
            return
        page._ba_listening = True  # type: ignore[attr-defined]
        for event in ("framenavigated", "load", "domcontentloaded"):
            page.on(event, lambda *_: self._status_dirty.set())
        page.on("request", lambda *_: None)

    async def stop(self, reason: str = "closed") -> None:
        if self.stopping:
            return
        self.stopping = True
        with contextlib.suppress(Exception):
            await self.input.release_all()
        for task in (self._input_task, self._status_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._subscription is not None:
            self.bus.unsubscribe(self._subscription)
        with contextlib.suppress(Exception):
            await self.streamer.stop()
        state_blob = None
        with contextlib.suppress(Exception):
            state = await self.browser.storage_state()
            if state:
                state_blob = encrypt_secret(json.dumps(state))
        url, title = "", ""
        with contextlib.suppress(Exception):
            url = sanitize_url(await self.driver.url())
            title = await self.driver.title()
        with contextlib.suppress(Exception):
            await self.browser.close()
        values: dict[str, Any] = {
            "status": BrowserSessionStatus.closed.value,
            "worker_id": None,
            "interactive": False,
            "locked_by_task_id": None,
            "desired_state": "closed",
            "last_used_at": _now(),
        }
        if state_blob:
            values["storage_state_encrypted"] = state_blob
        if url:
            values["current_url"] = url[:2000]
            values["current_title"] = title[:500]
        await self._update_db(**values)
        await emit_safe(
            self.org_id,
            "session.updated",
            f"Browser session closed ({reason})",
            session_id=self.session_id,
            payload={"status": "closed", "reason": reason},
        )
        await self.bus.publish(
            status_channel(self.session_id),
            {"type": "status", "session_id": str(self.session_id), "status": "closed", "reason": reason},
        )
        for cb in list(self.on_closed):
            with contextlib.suppress(Exception):
                res = cb(self)
                if asyncio.iscoroutine(res):
                    await res

    def is_alive(self) -> bool:
        return self.started and not self.stopping and self.browser.is_alive()

    # --------------------------------------------------------------------- usage
    def acquire(self, task_id: uuid.UUID) -> None:
        self.in_use_by_task = task_id
        self.current_task_id = task_id
        self.touch()

    def release(self) -> None:
        self.in_use_by_task = None
        self.current_task_id = None
        self.current_action = ""
        self.touch()

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity

    async def set_interactive(self, interactive: bool) -> None:
        if self.interactive == interactive:
            return
        self.interactive = interactive
        if not interactive:
            with contextlib.suppress(Exception):
                await self.input.release_all()
        await self._update_db(interactive=interactive, last_used_at=_now())
        await self.publish_status(force=True)

    async def set_action(
        self, action: str, *, kind: str = "action", detail: dict[str, Any] | None = None
    ) -> None:
        self.current_action = action
        self.touch()
        await self.bus.publish(
            status_channel(self.session_id),
            {
                "type": "action",
                "session_id": str(self.session_id),
                "action": action,
                "kind": kind,
                "detail": detail or {},
                "ts": time.time(),
            },
        )

    # -------------------------------------------------------------------- status
    async def publish_status(self, force: bool = False) -> None:
        try:
            url = sanitize_url(await self.driver.url())
            title = await self.driver.title()
            tabs = await self.driver.list_tabs()
            tabs = [{**tab, "url": sanitize_url(str(tab.get("url", "")))} for tab in tabs]
        except Exception:
            return
        status = {
            "type": "status",
            "session_id": str(self.session_id),
            "status": "in_use" if self.in_use_by_task else "ready",
            "url": url,
            "title": title,
            "tabs": tabs,
            "interactive": self.interactive,
            "task_id": str(self.current_task_id) if self.current_task_id else None,
            "action": self.current_action,
            "file_chooser_pending": self.file_chooser_pending,
            "viewport": {
                "width": self.settings.browser_viewport_width,
                "height": self.settings.browser_viewport_height,
            },
            "ts": time.time(),
        }
        comparable = {k: v for k, v in status.items() if k != "ts"}
        if not force and comparable == self._last_status:
            return
        changed_url = comparable.get("url") != self._last_status.get("url")
        self._last_status = comparable
        await self.bus.publish(status_channel(self.session_id), status)
        if changed_url:
            await self._update_db(current_url=url[:2000], current_title=title[:500])
            await publish_event(
                "browser.navigation",
                self.org_id,
                {"url": url, "title": title},
                session_id=self.session_id,
                task_id=self.current_task_id,
            )

    async def _status_loop(self) -> None:
        while True:
            try:
                try:
                    await asyncio.wait_for(self._status_dirty.wait(), timeout=2.0)
                except TimeoutError:
                    pass
                self._status_dirty.clear()
                await asyncio.sleep(0.15)  # coalesce bursts of navigation events
                await self.publish_status()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("status loop error: %s", exc)
                await asyncio.sleep(1)

    async def heartbeat(self) -> None:
        await self._update_db(
            last_heartbeat_at=_now(),
            status=BrowserSessionStatus.in_use.value
            if self.in_use_by_task
            else BrowserSessionStatus.ready.value,
            locked_by_task_id=self.in_use_by_task,
        )

    # --------------------------------------------------------------------- input
    async def _input_loop(self) -> None:
        assert self._subscription is not None
        async for _channel, message in self._subscription:
            try:
                await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("input handling error: %s", exc)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "control":
            action = message.get("action")
            if action == "interactive":
                await self.set_interactive(bool(message.get("interactive")))
            elif action == "open":
                await self.set_interactive(bool(message.get("interactive", self.interactive)))
                if message.get("url"):
                    with contextlib.suppress(Exception):
                        await self.driver.goto(str(message["url"]), wait_until="domcontentloaded")
            elif action == "close":
                asyncio.create_task(self.stop("closed by user"))
            elif action == "viewer_connected":
                await self.streamer.force_frame()
                await self.publish_status(force=True)
            return
        if kind == "upload_file":
            path = Path(str(message.get("path", "")))
            if path.exists() and self.interactive:
                ok = await self.input.upload_file(path)
                self.file_chooser_pending = False
                await self.publish_status(force=True)
                await publish_event(
                    "browser.status",
                    self.org_id,
                    {"upload": ok, "filename": path.name},
                    session_id=self.session_id,
                )
            return
        if kind == "navigate" and message.get("source") == "api":
            if self.interactive or not self.in_use_by_task:
                self.touch()
                await self.input.handle(message)
                self._status_dirty.set()
            return
        # Raw input events are only honoured while the human has control.
        if not self.interactive:
            return
        self.touch()
        await self.input.handle(message)

    def _on_file_chooser(self, chooser) -> None:  # noqa: ANN001
        if self.interactive:
            self.file_chooser_pending = True
            asyncio.ensure_future(self.publish_status(force=True))
            asyncio.ensure_future(
                publish_event(
                    "browser.status", self.org_id, {"file_chooser": True}, session_id=self.session_id
                )
            )

    # ------------------------------------------------------------------ database
    async def _update_db(self, **values: Any) -> None:
        try:
            async with session_scope() as db:
                await db.execute(
                    update(BrowserSession).where(BrowserSession.id == self.session_id).values(**values)
                )
        except Exception as exc:
            logger.warning("Failed to update browser session %s: %s", self.session_id, exc)

    async def restore_storage_state(self, encrypted: str | None) -> None:
        """Best-effort cookie restore when the on-disk profile was lost (e.g. new worker host)."""
        if not encrypted:
            return
        try:
            state = json.loads(decrypt_secret(encrypted))
            cookies = state.get("cookies") or []
            if not cookies:
                return
            if hasattr(self.browser, "restore_cookies"):
                await self.browser.restore_cookies(cookies)
            elif self.browser.context is not None:
                await self.browser.context.add_cookies(cookies)
        except Exception as exc:
            logger.info("Could not restore storage state: %s", exc)


async def emit_safe(org_id: uuid.UUID, event_type: str, message: str, **kwargs: Any) -> None:
    try:
        async with session_scope() as db:
            await emit(db, event_type, org_id, message, **kwargs)
    except Exception as exc:
        logger.debug("emit failed: %s", exc)


def _now() -> datetime:
    return datetime.now(UTC)
