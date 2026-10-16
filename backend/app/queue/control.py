"""Cooperative control token shared by the executor, adapters and agent tools.

The worker refreshes ``ControlToken`` from the database/bus; long-running operations call
``await token.checkpoint()`` regularly which:

* raises ``TaskStopped`` when the user stopped the task,
* suspends execution while the task is paused or a human has taken over the browser,
* returns normally otherwise.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.core.errors import SceneRetryRequested, SceneSkipped, TaskStopped


class ControlToken:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._paused = False
        self._human = False
        self._resume = asyncio.Event()
        self._resume.set()
        self.stop_reason = ""
        self.pause_reason = ""
        self.last_resume_at = 0.0
        self.resumed_from_human = False
        self.on_pause: list[Callable[[str], Awaitable[None]]] = []
        self.on_resume: list[Callable[[bool], Awaitable[None]]] = []
        self.skip_requested = False
        self.retry_requested = False
        self.waiting = False  # True while a checkpoint is actually blocked in a pause
        self._approval_event = asyncio.Event()
        self._approval_result: bool | None = None

    # -------------------------------------------------------------- transitions
    def request_stop(self, reason: str = "stopped by user") -> None:
        self.stop_reason = reason
        self._stop.set()
        self._resume.set()
        self._approval_event.set()

    def request_pause(self, reason: str = "paused by user", *, human: bool = False) -> None:
        if self._stop.is_set():
            return
        self.pause_reason = reason
        self._paused = True
        self._human = self._human or human
        self._resume.clear()

    def request_resume(self) -> bool:
        """Lift the pause. Returns True when a checkpoint was actually blocked (so the executor's
        ``on_resume`` callbacks will run); False when the pause was never observed - the caller
        must then reconcile the persisted task status itself."""
        was_waiting = self.waiting
        was_human = self._human
        self._paused = False
        self._human = False
        self.resumed_from_human = was_human
        self.last_resume_at = time.monotonic()
        self._resume.set()
        return was_waiting

    def request_skip(self) -> None:
        self.skip_requested = True
        self.request_resume()

    def request_retry(self) -> None:
        self.retry_requested = True
        self.request_resume()

    def clear_requests(self) -> None:
        self.skip_requested = False
        self.retry_requested = False

    def raise_for_requests(self) -> None:
        """Raise the pending skip/retry signal (consumed once)."""
        if self.skip_requested:
            self.skip_requested = False
            raise SceneSkipped("scene skipped by user")
        if self.retry_requested:
            self.retry_requested = False
            raise SceneRetryRequested("scene retry requested by user")

    # ------------------------------------------------------------------ queries
    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def human_control(self) -> bool:
        return self._human

    def raise_if_stopped(self) -> None:
        if self._stop.is_set():
            raise TaskStopped(self.stop_reason or "stopped")

    def begin_approval(self) -> None:
        """Prepare a fresh one-shot approval wait before publishing the pending action."""
        self._approval_result = None
        self._approval_event.clear()

    def resolve_approval(self, approved: bool) -> None:
        """Resolve the pending browser-action approval from an authenticated API control."""
        self._approval_result = approved
        self._approval_event.set()

    async def wait_for_approval(self) -> bool:
        """Wait for explicit approve/reject; Stop sets the same wake event and takes priority."""
        self.raise_if_stopped()
        await self._approval_event.wait()
        self.raise_if_stopped()
        approved = self._approval_result
        self._approval_result = None
        self._approval_event.clear()
        return bool(approved)

    async def checkpoint(self, *, raise_requests: bool = True) -> bool:
        """Yield to pause/stop.

        Raises ``TaskStopped`` on stop and (unless ``raise_requests`` is False) ``SceneSkipped`` /
        ``SceneRetryRequested`` when the user asked for those. Returns True when execution resumed
        after a pause.
        """
        self.raise_if_stopped()
        if raise_requests:
            self.raise_for_requests()
        if not self._paused:
            return False
        for cb in list(self.on_pause):
            await cb(self.pause_reason)
        self.waiting = True
        try:
            while self._paused and not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._resume.wait(), timeout=1.0)
                except TimeoutError:
                    continue
        finally:
            self.waiting = False
        self.raise_if_stopped()
        was_human = self.resumed_from_human
        self.resumed_from_human = False
        for cb in list(self.on_resume):
            await cb(was_human)
        if raise_requests:
            self.raise_for_requests()
        return True

    async def sleep(self, seconds: float) -> None:
        """Interruptible sleep: wakes up immediately on stop/pause changes."""
        end = time.monotonic() + seconds
        while True:
            self.raise_if_stopped()
            if self._paused:
                await self.checkpoint()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=min(remaining, 0.5))
            except TimeoutError:
                continue
