"""Platform adapter registry (plugin discovery)."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from app.platforms.base import PlatformAdapter

logger = logging.getLogger(__name__)

_registry: dict[str, type[PlatformAdapter]] | None = None


def _discover() -> dict[str, type[PlatformAdapter]]:
    import app.platforms as package

    found: dict[str, type[PlatformAdapter]] = {}
    for module_info in pkgutil.iter_modules(package.__path__):
        if not module_info.ispkg:
            continue
        try:
            module = importlib.import_module(f"app.platforms.{module_info.name}")
        except Exception as exc:  # pragma: no cover - a broken plugin must not kill the app
            logger.error("Failed to import platform adapter %s: %s", module_info.name, exc)
            continue
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, PlatformAdapter)
                and attr is not PlatformAdapter
                and getattr(attr, "key", "base") != "base"
            ):
                found[attr.key] = attr
    return found


def get_registry() -> dict[str, type[PlatformAdapter]]:
    global _registry
    if _registry is None:
        _registry = _discover()
    return _registry


def get_adapter_class(key: str | None) -> type[PlatformAdapter] | None:
    if not key:
        return None
    return get_registry().get(key)


def create_adapter(key: str, config: dict[str, Any] | None = None) -> PlatformAdapter:
    cls = get_adapter_class(key)
    if cls is None:
        from app.platforms.generic import GenericAdapter

        logger.warning("No adapter for platform %s, using the generic agent", key)
        return GenericAdapter(config)
    return cls(config)


def list_platform_info() -> list[dict[str, Any]]:
    order = [
        "mock",
        "generic",
        "google_flow",
        "runway",
        "kling",
        "luma",
        "hailuo",
        "pika",
        "pixverse",
        "vidu",
        "chatgpt",
        "gemini",
        "arena",
    ]
    registry = get_registry()
    keys = [k for k in order if k in registry] + sorted(k for k in registry if k not in order)
    return [registry[k].info() for k in keys]
