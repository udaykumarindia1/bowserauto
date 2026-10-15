"""Structured logging with secret redaction.

Every log record can carry structured context (task_id, session_id, action, duration...).
Sensitive values (passwords, tokens, cookies, API keys) are redacted before emission so
they never end up in log aggregation systems.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_log_context: ContextVar[dict[str, Any] | None] = ContextVar("log_context", default=None)

SENSITIVE_KEYS = re.compile(r"(password|passwd|secret|token|cookie|authorization|api[_-]?key|session[_-]?id|set-cookie)", re.I)
SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.=]+", re.I),
    re.compile(r"(sk-[A-Za-z0-9]{8})[A-Za-z0-9\-_]+"),
]


def redact(value: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive content from log payloads."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {k: ("***" if isinstance(k, str) and SENSITIVE_KEYS.search(k) else redact(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        out = value
        for pattern in SENSITIVE_VALUE_PATTERNS:
            out = pattern.sub(r"\1***", out)
        return out
    return value


def bind_log_context(**kwargs: Any) -> None:
    current = dict(_log_context.get() or {})
    current.update({k: v for k, v in kwargs.items() if v is not None})
    _log_context.set(current)


def clear_log_context() -> None:
    _log_context.set({})


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _log_context.get() or {}
        for key, value in ctx.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k in ("task_id", "session_id", "scene_id", "action", "duration_ms", "worker_id", "request_id")
        }
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in redact(extras).items())
        return base


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(ConsoleFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(ContextFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore", "aiosqlite", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Timer:
    """Small helper to measure durations for structured action logs."""

    def __init__(self) -> None:
        self.start = time.perf_counter()

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self.start) * 1000)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
