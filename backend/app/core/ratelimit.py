"""Rate limiting (sliding window).

Uses Redis when configured (shared across API replicas) and falls back to an in-memory
limiter otherwise.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from app.core.config import get_settings


class RateLimiter:
    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        """Register a hit; returns (allowed, retry_after_seconds)."""
        raise NotImplementedError


class MemoryRateLimiter(RateLimiter):
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup = time.monotonic()

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = int(bucket[0] + window_seconds - now) + 1
                return False, max(retry_after, 1)
            bucket.append(now)
            if now - self._last_cleanup > 60:
                self._last_cleanup = now
                for k in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                    self._hits.pop(k, None)
            return True, 0


class RedisRateLimiter(RateLimiter):
    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)
        self._fallback = MemoryRateLimiter()

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        try:
            now = time.time()
            pipe = self._redis.pipeline()
            redis_key = f"ratelimit:{key}"
            pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
            pipe.zadd(redis_key, {str(now): now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, window_seconds)
            _, _, count, _ = await pipe.execute()
            if count > limit:
                return False, window_seconds
            return True, 0
        except Exception:
            return await self._fallback.hit(key, limit, window_seconds)


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        settings = get_settings()
        if settings.redis_url and not settings.is_test:
            _limiter = RedisRateLimiter(settings.redis_url)
        else:
            _limiter = MemoryRateLimiter()
    return _limiter


def reset_rate_limiter() -> None:
    global _limiter
    _limiter = None
