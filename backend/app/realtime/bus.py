"""Event bus used for real-time communication between API server, workers and browsers.

Two implementations share one interface:

* ``InMemoryEventBus`` – single process (development / tests / embedded worker).
* ``RedisEventBus`` – multi process deployments (API + N workers) using Redis pub/sub.

Channels (string names, glob patterns supported by ``subscribe``):

* ``org:{org_id}:events``         – domain events for the UI (task.progress, agent.action, …)
* ``session:{session_id}:frames`` – live browser JPEG frames (base64) + viewport metadata
* ``session:{session_id}:input``  – human takeover input events (API -> worker)
* ``session:{session_id}:status`` – browser state (url/title/loading/action indicator)
* ``worker:wakeup``               – nudges workers to poll the database queue immediately
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

Message = dict[str, Any]
Handler = Callable[[str, Message], Awaitable[None]]


class Subscription:
    def __init__(self, pattern: str, maxsize: int, drop_oldest: bool) -> None:
        self.pattern = pattern
        self.queue: asyncio.Queue[tuple[str, Message]] = asyncio.Queue(maxsize=maxsize)
        self.drop_oldest = drop_oldest
        self.closed = False

    def matches(self, channel: str) -> bool:
        return fnmatch.fnmatchcase(channel, self.pattern)

    def push(self, channel: str, message: Message) -> None:
        if self.closed:
            return
        if self.queue.full():
            if self.drop_oldest:
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()
            else:
                return
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait((channel, message))

    async def __aiter__(self) -> AsyncIterator[tuple[str, Message]]:
        while not self.closed:
            channel, message = await self.queue.get()
            if channel == "__close__":
                return
            yield channel, message

    def close(self) -> None:
        self.closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(("__close__", {}))


class EventBus:
    async def start(self) -> None:  # pragma: no cover - interface
        pass

    async def stop(self) -> None:  # pragma: no cover - interface
        pass

    async def publish(self, channel: str, message: Message) -> None:
        raise NotImplementedError

    def subscribe(self, pattern: str, *, maxsize: int = 500, drop_oldest: bool = True) -> Subscription:
        raise NotImplementedError

    def unsubscribe(self, subscription: Subscription) -> None:
        raise NotImplementedError

    @property
    def kind(self) -> str:
        return self.__class__.__name__


class InMemoryEventBus(EventBus):
    def __init__(self) -> None:
        self._subs: list[Subscription] = []

    async def publish(self, channel: str, message: Message) -> None:
        for sub in list(self._subs):
            if sub.matches(channel):
                sub.push(channel, message)

    def subscribe(self, pattern: str, *, maxsize: int = 500, drop_oldest: bool = True) -> Subscription:
        sub = Subscription(pattern, maxsize, drop_oldest)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        subscription.close()
        with contextlib.suppress(ValueError):
            self._subs.remove(subscription)


class RedisEventBus(EventBus):
    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._url = url
        self._redis = aioredis.from_url(url, decode_responses=True)
        self._pubsub = None
        self._subs: list[Subscription] = []
        self._patterns: dict[str, int] = {}
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._pubsub = self._redis.pubsub()
        self._reader = asyncio.create_task(self._read_loop(), name="redis-bus-reader")

    async def stop(self) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        if self._pubsub:
            with contextlib.suppress(Exception):
                await self._pubsub.close()
        with contextlib.suppress(Exception):
            await self._redis.aclose()

    async def publish(self, channel: str, message: Message) -> None:
        try:
            await self._redis.publish(channel, json.dumps(message, default=str))
        except Exception as exc:  # pragma: no cover - network failure path
            logger.warning("Redis publish failed on %s: %s", channel, exc)

    def subscribe(self, pattern: str, *, maxsize: int = 500, drop_oldest: bool = True) -> Subscription:
        sub = Subscription(pattern, maxsize, drop_oldest)
        self._subs.append(sub)
        self._patterns[pattern] = self._patterns.get(pattern, 0) + 1
        if self._patterns[pattern] == 1 and self._pubsub is not None:
            asyncio.create_task(self._psubscribe(pattern))
        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        subscription.close()
        with contextlib.suppress(ValueError):
            self._subs.remove(subscription)
        count = self._patterns.get(subscription.pattern, 0) - 1
        if count <= 0:
            self._patterns.pop(subscription.pattern, None)
            if self._pubsub is not None:
                asyncio.create_task(self._punsubscribe(subscription.pattern))
        else:
            self._patterns[subscription.pattern] = count

    async def _psubscribe(self, pattern: str) -> None:
        async with self._lock:
            with contextlib.suppress(Exception):
                await self._pubsub.psubscribe(pattern)

    async def _punsubscribe(self, pattern: str) -> None:
        async with self._lock:
            with contextlib.suppress(Exception):
                await self._pubsub.punsubscribe(pattern)

    async def _read_loop(self) -> None:
        while True:
            try:
                if not self._patterns:
                    await asyncio.sleep(0.2)
                    continue
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                if message.get("type") not in ("pmessage", "message"):
                    continue
                channel = message.get("channel")
                try:
                    payload = json.loads(message.get("data") or "{}")
                except json.JSONDecodeError:
                    continue
                for sub in list(self._subs):
                    if sub.matches(channel):
                        sub.push(channel, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - resilience path
                logger.warning("Redis bus reader error: %s", exc)
                await asyncio.sleep(1.0)


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        settings = get_settings()
        if settings.redis_url and not settings.is_test:
            _bus = RedisEventBus(settings.redis_url)
        else:
            _bus = InMemoryEventBus()
    return _bus


def set_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus


async def start_bus() -> EventBus:
    bus = get_bus()
    await bus.start()
    return bus


async def stop_bus() -> None:
    global _bus
    if _bus is not None:
        await _bus.stop()
    _bus = None


# Channel helpers -------------------------------------------------------------------
def org_channel(org_id: Any) -> str:
    return f"org:{org_id}:events"


def frames_channel(session_id: Any) -> str:
    return f"session:{session_id}:frames"


def input_channel(session_id: Any) -> str:
    return f"session:{session_id}:input"


def status_channel(session_id: Any) -> str:
    return f"session:{session_id}:status"


WORKER_WAKEUP = "worker:wakeup"
