"""Tool registry for the browser agent."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.browser.driver import BrowserDriver
from app.core.errors import AutomationError, ElementNotFound, TaskStopped
from app.core.logging import redact
from app.core.url_safety import sanitize_url
from app.queue.control import ControlToken
from app.storage.paths import resolve_inside, safe_filename, unique_path

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    done: bool = False  # the agent loop should stop (task.complete / task.fail / task.needs_human)
    status: str = ""  # completed | failed | needs_human | paused

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "summary": self.summary, **({"data": self.data} if self.data else {})}


@dataclass
class ToolContext:
    driver: BrowserDriver
    control: ControlToken
    project_dir: Path
    downloads_dir: Path
    log: Callable[..., Awaitable[None]]
    screenshot: Callable[[str], Awaitable[None]]
    status_update: Callable[[str], Awaitable[None]] | None = None
    allowed_files: list[Path] = field(default_factory=list)  # reference files the agent may upload
    state: dict[str, Any] = field(default_factory=dict)
    approval_mode: str = "never"
    approval_handler: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


_WRITE_TOOLS = {
    "browser.click",
    "browser.type",
    "browser.select",
    "browser.upload",
    "browser.close_tab",
    "browser.download",
    "filesystem.create_folder",
    "filesystem.save_file",
    "filesystem.rename",
    "filesystem.move",
}
_SAFE_READ_TOOLS = {
    "browser.get_url",
    "browser.get_page_text",
    "browser.extract",
    "browser.wait",
    "browser.screenshot",
    "browser.get_accessibility_tree",
    "browser.list_tabs",
    "filesystem.list",
}


def _requires_action_approval(name: str, mode: str) -> bool:
    if mode == "confirm_writes":
        return name in _WRITE_TOOLS
    if mode == "confirm_all":
        return (
            name.startswith(("browser.", "filesystem.")) and name not in _SAFE_READ_TOOLS
        ) or name == "task.undo"
    return False


def sanitize_action_args(args: dict[str, Any]) -> dict[str, Any]:
    """Return a safe, bounded preview for persistence and approval screens.

    Free-form text and selected values can contain passwords or other user-provided secrets,
    so they are represented by their length rather than copied into activity logs or the UI.
    """
    safe = redact(args)
    if not isinstance(safe, dict):
        return {}
    for key, value in list(safe.items()):
        normalized = key.lower().replace("-", "_")
        if normalized in {"text_to_type", "value", "content"}:
            length = len(str(value))
            safe[key] = f"[hidden for privacy; {length} characters]"
        elif isinstance(value, str):
            safe[key] = value[:500]
        elif isinstance(value, (dict, list)):
            safe[key] = "[structured value omitted]"
    return safe


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> str:
        lines = []
        for tool in self._tools.values():
            params = ", ".join(
                f"{k}: {v.get('type', 'any')}" for k, v in tool.parameters.get("properties", {}).items()
            )
            lines.append(f"- {tool.name}({params}): {tool.description}")
        return "\n".join(lines)

    def catalog(self) -> list[dict[str, Any]]:
        """Return the built-in, typed skills with their policy classification for the HUD."""
        items = []
        for tool in self._tools.values():
            if tool.name in _WRITE_TOOLS:
                risk, approval = "write", "confirm_writes or confirm_all"
            elif tool.name in _SAFE_READ_TOOLS:
                risk, approval = "read-only", "never"
            elif tool.name == "task.undo":
                risk, approval = "state-change", "confirm_all"
            elif tool.name.startswith("browser."):
                risk, approval = "state-change", "confirm_all"
            else:
                risk, approval = "task-control", "never"
            items.append(
                {
                    "name": tool.name,
                    "category": tool.name.split(".", 1)[0],
                    "description": tool.description,
                    "risk": risk,
                    "approval": approval,
                    "parameters": tool.parameters,
                }
            )
        return items

    async def execute(self, ctx: ToolContext, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"Unknown tool '{name}'. Available: {', '.join(self._tools)}")
        await ctx.control.checkpoint()
        started = time.perf_counter()
        try:
            tool_args = args or {}
            if _requires_action_approval(name, ctx.approval_mode):
                if ctx.approval_handler is None:
                    raise AutomationError(
                        "Action approval is required but no approval controller is available", retryable=False
                    )
                approved = await ctx.approval_handler(name, tool_args)
                if not approved:
                    raise TaskStopped("The proposed action was rejected by a user")
            result = await tool.handler(ctx, tool_args)
        except ElementNotFound as exc:
            result = ToolResult(False, f"{exc.message}")
        except AutomationError as exc:
            if not exc.retryable:
                raise
            result = ToolResult(False, exc.message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # tool errors must never kill the task silently
            logger.exception("Tool %s crashed", name)
            result = ToolResult(False, f"{type(exc).__name__}: {str(exc)[:200]}")
        result.data.setdefault("duration_ms", int((time.perf_counter() - started) * 1000))
        return result


def _target(args: dict[str, Any]) -> Any:
    if "index" in args and args["index"] is not None:
        return {"index": int(args["index"])}
    if args.get("selector"):
        return args["selector"]
    if args.get("text"):
        return {"text": args["text"]}
    if args.get("role"):
        return {"role": args["role"], "name": args.get("name")}
    raise ElementNotFound("Specify the element with index, selector or text")


def _schema(**props: Any) -> dict[str, Any]:
    required = props.pop("__required__", [])
    return {"type": "object", "properties": props, "required": required}


def _p(type_: str, description: str, **extra: Any) -> dict[str, Any]:
    return {"type": type_, "description": description, **extra}


# -------------------------------------------------------------------- browser tools
async def t_open(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    url = str(a.get("url", "")).strip()
    if not url:
        return ToolResult(False, "url is required")
    if url.lower().startswith(("javascript:", "file:", "data:")):
        return ToolResult(False, "Unsupported URL scheme")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    prior_url = await ctx.driver.url()
    await ctx.driver.goto(url)
    raw_current_url = await ctx.driver.url()
    if prior_url and prior_url not in ("about:blank", raw_current_url):
        stack = ctx.state.setdefault("undo_stack", [])
        stack.append({"kind": "navigation", "from_url": prior_url, "to_url": raw_current_url})
        del stack[:-20]
    current_url = sanitize_url(raw_current_url)
    return ToolResult(
        True,
        f"Opened {current_url}",
        {"url": current_url, "title": await ctx.driver.title()},
    )


async def t_back(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    prior_url = await ctx.driver.url()
    await ctx.driver.back()
    current_url = await ctx.driver.url()
    stack = ctx.state.get("undo_stack") or []
    if stack and stack[-1].get("to_url") == prior_url:
        stack.pop()
    return ToolResult(True, f"Went back to {sanitize_url(current_url)}")


async def task_undo(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """Compensate for the most recent explicit browser.open navigation only."""
    stack = ctx.state.get("undo_stack") or []
    if not stack:
        return ToolResult(False, "There is no recent reversible navigation to undo")
    action = stack[-1]
    if action.get("kind") != "navigation":
        return ToolResult(False, "The latest action has no safe compensating operation")
    current_url = await ctx.driver.url()
    if current_url != action.get("to_url"):
        return ToolResult(False, "Page state changed after that navigation; no action was undone")
    await ctx.driver.back()
    restored_url = await ctx.driver.url()
    if restored_url != action.get("from_url"):
        return ToolResult(False, "Browser history did not return to the prior page; no action was undone")
    stack.pop()
    return ToolResult(True, f"Undid the last navigation; returned to {sanitize_url(restored_url)}")


async def t_forward(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    await ctx.driver.forward()
    return ToolResult(True, f"Went forward to {await ctx.driver.url()}")


async def t_refresh(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    await ctx.driver.reload()
    return ToolResult(True, "Page reloaded")


async def t_click(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    target = _target(a)
    await ctx.driver.click(target)
    return ToolResult(
        True,
        f"Clicked {a.get('text') or a.get('selector') or a.get('index')}",
        {"url": sanitize_url(await ctx.driver.url())},
    )


async def t_type(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    target = _target(a)
    text = str(a.get("text_to_type", a.get("value", "")))
    await ctx.driver.type(
        target, text, clear=bool(a.get("clear", True)), press_enter=bool(a.get("press_enter", False))
    )
    return ToolResult(True, f"Typed {len(text)} characters")


async def t_select(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    await ctx.driver.select_option(_target(a), str(a.get("value", "")))
    return ToolResult(True, f"Selected {a.get('value')}")


async def t_scroll(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    await ctx.driver.scroll(str(a.get("direction", "down")), int(a.get("amount", 600)))
    return ToolResult(True, f"Scrolled {a.get('direction', 'down')}")


async def t_upload(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    files = a.get("files") or ([a["file"]] if a.get("file") else [])
    if not files:
        files = [str(f) for f in ctx.allowed_files]
    paths: list[Path] = []
    for f in files:
        candidate = Path(str(f))
        allowed = {p.resolve() for p in ctx.allowed_files}
        if candidate.resolve() in allowed:
            paths.append(candidate.resolve())
            continue
        try:
            paths.append(resolve_inside(ctx.project_dir, candidate))
        except PermissionError:
            return ToolResult(False, f"File {f} is outside the project folder")
    if not paths:
        return ToolResult(False, "No files to upload")
    target = None
    if a.get("index") is not None or a.get("selector") or a.get("text"):
        target = _target(a)
    await ctx.driver.upload(target, paths)
    return ToolResult(True, f"Uploaded {', '.join(p.name for p in paths)}")


async def t_download(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    target = _target(a) if (a.get("index") is not None or a.get("selector") or a.get("text")) else None
    result = await ctx.driver.download(target, timeout_ms=int(a.get("timeout_seconds", 180)) * 1000)
    ctx.downloads_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(a.get("filename") or result.suggested_filename or "download.bin")
    dest = unique_path(ctx.downloads_dir / filename)
    shutil.move(str(result.path), dest)
    ctx.state["download_path"] = str(dest)
    return ToolResult(
        True,
        f"Downloaded {dest.name} ({dest.stat().st_size} bytes)",
        {"download_path": str(dest), "size_bytes": dest.stat().st_size},
    )


async def t_wait(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    timeout_ms = int(min(float(a.get("timeout_seconds", 30)), 600) * 1000)
    if a.get("text"):
        ok = await ctx.driver.wait_for_text(str(a["text"]), timeout_ms=timeout_ms)
        return ToolResult(ok, f"Text {'appeared' if ok else 'did not appear'}: {a['text']}")
    if a.get("selector") or a.get("index") is not None:
        ok = await ctx.driver.wait_for(
            _target(a), state=str(a.get("state", "visible")), timeout_ms=timeout_ms
        )
        return ToolResult(ok, f"Element {'is' if ok else 'is not'} {a.get('state', 'visible')}")
    if a.get("url_contains"):
        ok = await ctx.driver.wait_for_url(str(a["url_contains"]), timeout_ms=timeout_ms)
        return ToolResult(ok, f"URL {'matched' if ok else 'did not match'}")
    seconds = min(float(a.get("seconds", 2)), 30)
    await ctx.control.sleep(seconds)
    return ToolResult(True, f"Waited {seconds:g}s")


async def t_extract(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    target = _target(a) if (a.get("index") is not None or a.get("selector") or a.get("text")) else None
    if a.get("attribute") and target is not None:
        value = await ctx.driver.extract_attribute(target, str(a["attribute"]))
        return ToolResult(True, f"{a['attribute']}={value}", {"value": value})
    text = await ctx.driver.extract_text(target)
    return ToolResult(True, text[:4000], {"text": text[:20_000]})


async def t_screenshot(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    await ctx.screenshot(str(a.get("label", "agent")))
    return ToolResult(True, "Screenshot captured")


async def t_get_url(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    url = sanitize_url(await ctx.driver.url())
    return ToolResult(True, url, {"url": url, "title": await ctx.driver.title()})


async def t_page_text(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    text = await ctx.driver.page_text(int(a.get("max_chars", 8000)))
    return ToolResult(True, text[:4000], {"text": text})


async def t_ax_tree(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    nodes = await ctx.driver.accessibility_tree(int(a.get("max_nodes", 200)))
    summary = "\n".join(
        f"{n['role']}: {n['name']}" + (f" = {n['value']}" if n.get("value") else "") for n in nodes[:150]
    )
    return ToolResult(True, summary[:4000], {"nodes": nodes})


async def t_switch_tab(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    await ctx.driver.switch_tab(int(a.get("index", 0)))
    return ToolResult(True, f"Switched to tab {a.get('index', 0)}", {"url": await ctx.driver.url()})


async def t_close_tab(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    await ctx.driver.close_tab(a.get("index"))
    return ToolResult(True, "Tab closed")


# ----------------------------------------------------------------- filesystem tools
async def fs_create_folder(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    path = resolve_inside(ctx.project_dir, str(a.get("path", "")))
    path.mkdir(parents=True, exist_ok=True)
    return ToolResult(True, f"Folder ready: {path.relative_to(ctx.project_dir)}")


async def fs_save_file(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    path = resolve_inside(ctx.project_dir, str(a.get("path", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    content = a.get("content", "")
    if isinstance(content, (dict, list)):
        content = json.dumps(content, indent=2)
    path.write_text(str(content), encoding="utf-8")
    return ToolResult(
        True, f"Saved {path.relative_to(ctx.project_dir)} ({path.stat().st_size} bytes)", {"path": str(path)}
    )


async def fs_rename(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    src = resolve_inside(ctx.project_dir, str(a.get("path", "")))
    dst = src.with_name(safe_filename(str(a.get("new_name", ""))))
    if not src.exists():
        return ToolResult(False, "Source file does not exist")
    dst = unique_path(dst)
    src.rename(dst)
    return ToolResult(True, f"Renamed to {dst.name}", {"path": str(dst)})


async def fs_move(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    src = resolve_inside(ctx.project_dir, str(a.get("path", "")))
    dst = resolve_inside(ctx.project_dir, str(a.get("destination", "")))
    if not src.exists():
        return ToolResult(False, "Source file does not exist")
    if dst.is_dir() or str(a.get("destination", "")).endswith("/"):
        dst.mkdir(parents=True, exist_ok=True)
        dst = dst / src.name
    dst = unique_path(dst)
    shutil.move(str(src), str(dst))
    return ToolResult(True, f"Moved to {dst.relative_to(ctx.project_dir)}", {"path": str(dst)})


async def fs_list(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    path = resolve_inside(ctx.project_dir, str(a.get("path", ".")))
    if not path.exists():
        return ToolResult(False, "Folder does not exist")
    entries = []
    for child in sorted(path.iterdir())[:200]:
        entries.append(
            {
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else None,
            }
        )
    summary = "\n".join(
        f"{'[dir] ' if e['is_dir'] else ''}{e['name']}"
        + (f" ({e['size']} B)" if e["size"] is not None else "")
        for e in entries
    )
    return ToolResult(True, summary or "(empty)", {"entries": entries})


# ----------------------------------------------------------------------- task tools
async def task_update_status(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    message = str(a.get("message", ""))[:300]
    if ctx.status_update:
        await ctx.status_update(message)
    return ToolResult(True, f"Status: {message}")


async def task_pause(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    reason = str(a.get("reason", "paused by agent"))
    ctx.control.request_pause(reason)
    return ToolResult(True, f"Paused: {reason}", status="paused")


async def task_resume(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    ctx.control.request_resume()
    return ToolResult(True, "Resumed")


async def task_retry(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    ctx.state["retry_requested"] = True
    return ToolResult(True, "Retry requested", {"reason": a.get("reason", "")}, done=True, status="retry")


async def task_complete(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    data = {k: v for k, v in a.items() if k != "message"}
    if ctx.state.get("download_path") and "download_path" not in data:
        data["download_path"] = ctx.state["download_path"]
    return ToolResult(True, str(a.get("message", "Completed")), data, done=True, status="completed")


async def task_fail(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    return ToolResult(
        False,
        str(a.get("reason", "Failed")),
        {"retryable": bool(a.get("retryable", True))},
        done=True,
        status="failed",
    )


async def task_needs_human(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    return ToolResult(
        False, str(a.get("reason", "Human assistance required")), done=True, status="needs_human"
    )


def build_default_registry() -> ToolRegistry:
    r = ToolRegistry()
    el = {
        "index": _p("integer", "element index from the page snapshot"),
        "selector": _p("string", "CSS selector (alternative to index)"),
        "text": _p("string", "visible text of the element (alternative)"),
    }
    r.register(
        Tool(
            "browser.open",
            "Navigate to a URL",
            _schema(url=_p("string", "absolute URL"), __required__=["url"]),
            t_open,
        )
    )
    r.register(Tool("browser.back", "Go back in history", _schema(), t_back))
    r.register(Tool("browser.forward", "Go forward in history", _schema(), t_forward))
    r.register(Tool("browser.refresh", "Reload the page", _schema(), t_refresh))
    r.register(Tool("browser.click", "Click an element", _schema(**el), t_click))
    r.register(
        Tool(
            "browser.type",
            "Type into an input/textarea/contenteditable (clears first unless clear=false)",
            _schema(
                **el,
                text_to_type=_p("string", "text to type"),
                clear=_p("boolean", "clear existing value"),
                press_enter=_p("boolean", "press Enter afterwards"),
                __required__=["text_to_type"],
            ),
            t_type,
        )
    )
    r.register(
        Tool(
            "browser.select",
            "Choose an option of a select/dropdown",
            _schema(**el, value=_p("string", "option value or label"), __required__=["value"]),
            t_select,
        )
    )
    r.register(
        Tool(
            "browser.scroll",
            "Scroll the page",
            _schema(direction=_p("string", "up|down|left|right"), amount=_p("integer", "pixels")),
            t_scroll,
        )
    )
    r.register(
        Tool(
            "browser.upload",
            "Upload reference file(s) via a file input or upload button",
            _schema(
                **el,
                files=_p(
                    "array", "file paths (defaults to the scene reference files)", items={"type": "string"}
                ),
            ),
            t_upload,
        )
    )
    r.register(
        Tool(
            "browser.download",
            "Click a download control and save the resulting file",
            _schema(
                **el, filename=_p("string", "optional file name"), timeout_seconds=_p("integer", "max wait")
            ),
            t_download,
        )
    )
    r.register(
        Tool(
            "browser.wait",
            "Wait for text/element/url or a few seconds",
            _schema(
                text=_p("string", "wait for this text"),
                selector=_p("string", "wait for this element"),
                state=_p("string", "visible|hidden"),
                url_contains=_p("string", "wait for url"),
                seconds=_p("number", "plain wait (max 30)"),
                timeout_seconds=_p("integer", "timeout"),
            ),
            t_wait,
        )
    )
    r.register(
        Tool(
            "browser.extract",
            "Extract text (or an attribute) from the page or an element",
            _schema(**el, attribute=_p("string", "attribute name, e.g. href")),
            t_extract,
        )
    )
    r.register(
        Tool(
            "browser.screenshot",
            "Capture a screenshot for the history",
            _schema(label=_p("string", "label")),
            t_screenshot,
        )
    )
    r.register(Tool("browser.get_url", "Return current URL and title", _schema(), t_get_url))
    r.register(
        Tool(
            "browser.get_page_text",
            "Return the visible page text",
            _schema(max_chars=_p("integer", "limit")),
            t_page_text,
        )
    )
    r.register(
        Tool(
            "browser.get_accessibility_tree",
            "Return the accessibility tree (roles/names)",
            _schema(max_nodes=_p("integer", "limit")),
            t_ax_tree,
        )
    )
    r.register(
        Tool(
            "browser.switch_tab",
            "Switch to another tab",
            _schema(index=_p("integer", "tab index"), __required__=["index"]),
            t_switch_tab,
        )
    )
    r.register(
        Tool(
            "browser.close_tab",
            "Close a tab",
            _schema(index=_p("integer", "tab index (default current)")),
            t_close_tab,
        )
    )
    r.register(
        Tool(
            "filesystem.create_folder",
            "Create a folder inside the project",
            _schema(path=_p("string", "relative path"), __required__=["path"]),
            fs_create_folder,
        )
    )
    r.register(
        Tool(
            "filesystem.save_file",
            "Save text content to a project file",
            _schema(
                path=_p("string", "relative path"),
                content=_p("string", "content"),
                __required__=["path", "content"],
            ),
            fs_save_file,
        )
    )
    r.register(
        Tool(
            "filesystem.rename",
            "Rename a project file",
            _schema(
                path=_p("string", "relative path"),
                new_name=_p("string", "new file name"),
                __required__=["path", "new_name"],
            ),
            fs_rename,
        )
    )
    r.register(
        Tool(
            "filesystem.move",
            "Move a project file",
            _schema(
                path=_p("string", "relative path"),
                destination=_p("string", "relative destination"),
                __required__=["path", "destination"],
            ),
            fs_move,
        )
    )
    r.register(
        Tool("filesystem.list", "List a project folder", _schema(path=_p("string", "relative path")), fs_list)
    )
    r.register(
        Tool(
            "task.update_status",
            "Report a short status message to the user",
            _schema(message=_p("string", "status"), __required__=["message"]),
            task_update_status,
        )
    )
    r.register(
        Tool(
            "task.undo",
            "Safely undo the most recent explicit browser.open navigation; does not reverse clicks, form submissions, or external side effects",
            _schema(),
            task_undo,
        )
    )
    r.register(
        Tool(
            "task.pause",
            "Pause the task (e.g. to let a human intervene)",
            _schema(reason=_p("string", "why")),
            task_pause,
        )
    )
    r.register(Tool("task.resume", "Resume a paused task", _schema(), task_resume))
    r.register(
        Tool(
            "task.retry",
            "Abort the current attempt and retry from the start",
            _schema(reason=_p("string", "why")),
            task_retry,
        )
    )
    r.register(
        Tool(
            "task.complete",
            "Finish successfully. Include download_path when a file was downloaded.",
            _schema(message=_p("string", "summary"), download_path=_p("string", "downloaded file path")),
            task_complete,
        )
    )
    r.register(
        Tool(
            "task.fail",
            "Give up on the current goal",
            _schema(reason=_p("string", "why"), retryable=_p("boolean", "whether a retry could help")),
            task_fail,
        )
    )
    r.register(
        Tool(
            "task.needs_human",
            "Stop and ask the user to take over (login, CAPTCHA, MFA, consent, payment)",
            _schema(reason=_p("string", "what the human must do"), __required__=["reason"]),
            task_needs_human,
        )
    )
    return r
