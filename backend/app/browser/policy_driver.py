"""Approval-aware proxy for browser actions executed by platform adapters.

The generic agent has its own ToolRegistry approval gate. This proxy covers the
adapter-native BrowserDriver calls that would otherwise bypass the task policy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.browser.driver import BrowserDriver, TargetSpec, describe_target
from app.core.errors import AutomationError, TaskStopped
from app.core.url_safety import sanitize_url

ApprovalHandler = Callable[[str, dict[str, Any]], Awaitable[bool]]

_WRITE_OPERATIONS = {"click", "type", "press", "select_option", "upload", "download", "close_tab"}
_STATE_OPERATIONS = {
    "goto",
    "back",
    "forward",
    "reload",
    "scroll",
    "hover",
    "switch_tab",
    "new_tab",
    "evaluate",
}


def _target_preview(target: TargetSpec | None) -> str:
    if target is None:
        return "current page"
    try:
        return describe_target(target)[:240]
    except (TypeError, ValueError):
        return "[target omitted]"


class ApprovalGuardDriver:
    """Delegate reads directly and require policy approval before mutating adapter calls."""

    def __init__(
        self,
        driver: BrowserDriver,
        *,
        approval_mode: str,
        approval_handler: ApprovalHandler | None,
    ) -> None:
        self._driver = driver
        self._approval_mode = (
            approval_mode if approval_mode in {"never", "confirm_writes", "confirm_all"} else "confirm_writes"
        )
        self._approval_handler = approval_handler

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._driver, name)
        if name not in _WRITE_OPERATIONS | _STATE_OPERATIONS:
            return method

        async def guarded(*args: Any, **kwargs: Any) -> Any:
            required = (
                name in _WRITE_OPERATIONS
                if self._approval_mode == "confirm_writes"
                else name in (_WRITE_OPERATIONS | _STATE_OPERATIONS)
                if self._approval_mode == "confirm_all"
                else False
            )
            if required:
                await self._approve(name, args, kwargs)
            return await method(*args, **kwargs)

        return guarded

    async def _approve(self, operation: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if self._approval_handler is None:
            raise AutomationError(
                "This adapter action requires approval, but no approval controller is available",
                retryable=False,
            )
        target = args[0] if args else kwargs.get("target")
        preview: dict[str, Any] = {"operation": operation}
        if operation in {"click", "type", "select_option", "upload", "download"}:
            preview["target"] = _target_preview(target)
        if operation == "type":
            text = str(args[1] if len(args) > 1 else kwargs.get("text", ""))
            preview["text_to_type"] = text
            preview["character_count"] = len(text)
        elif operation == "select_option":
            preview["value"] = str(args[1] if len(args) > 1 else kwargs.get("value", ""))
        elif operation == "press":
            preview["key"] = str(args[0] if args else kwargs.get("key", ""))[:40]
            target_arg = args[1] if len(args) > 1 else kwargs.get("target")
            if target_arg is not None:
                preview["target"] = _target_preview(target_arg)
        elif operation == "upload":
            files = args[1] if len(args) > 1 else kwargs.get("files", [])
            preview["file_count"] = len(files or [])
        elif operation == "close_tab":
            preview["index"] = args[0] if args else kwargs.get("index")
        elif operation in {"goto", "new_tab"}:
            url = args[0] if args else kwargs.get("url")
            preview["url"] = sanitize_url(str(url or ""))
        elif operation == "switch_tab":
            preview["index"] = args[0] if args else kwargs.get("index")
        elif operation == "evaluate":
            preview["script"] = "[adapter-owned page inspection script]"

        approved = await self._approval_handler(f"adapter.browser.{operation}", preview)
        if not approved:
            raise TaskStopped("The adapter action was rejected by a user")
