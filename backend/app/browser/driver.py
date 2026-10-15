"""Browser driver interface.

Platform adapters and the generic agent only talk to this interface; the Playwright
implementation lives in ``playwright_driver.py`` and a fake implementation used in tests
lives in ``fake_driver.py``.

Selectors are expressed as *target specs* so that adapters can describe elements robustly::

    {"css": "button.generate"}
    {"role": "button", "name": "Generate"}          # accessible role + name (regex allowed with "/.../i")
    {"text": "Download"}                             # visible text (substring, case-insensitive)
    {"placeholder": "Describe your video"}
    {"label": "Prompt"}
    {"testid": "generate-button"}
    {"index": 12}                                    # element index from the last page snapshot
    {"any": [spec, spec, ...]}                       # first spec that resolves wins

A plain string is treated as ``{"css": value}`` unless it starts with ``text=``, ``role=``,
``placeholder=`` or ``#`` (index).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

TargetSpec = str | dict[str, Any] | int


@dataclass
class InteractiveElement:
    index: int
    tag: str
    role: str
    name: str
    text: str = ""
    placeholder: str = ""
    value: str = ""
    href: str = ""
    input_type: str = ""
    disabled: bool = False
    checked: bool | None = None
    bbox: tuple[float, float, float, float] | None = None  # x, y, width, height (viewport px)
    selector: str = ""  # best-effort unique CSS selector

    def describe(self) -> str:
        bits = [f"[{self.index}]", f"<{self.tag}"]
        if self.role and self.role != self.tag:
            bits.append(f'role="{self.role}"')
        if self.input_type:
            bits.append(f'type="{self.input_type}"')
        if self.name:
            bits.append(f'name="{self.name[:80]}"')
        if self.placeholder:
            bits.append(f'placeholder="{self.placeholder[:60]}"')
        if self.value:
            bits.append(f'value="{self.value[:40]}"')
        if self.href:
            bits.append(f'href="{self.href[:60]}"')
        if self.disabled:
            bits.append("disabled")
        if self.checked is not None:
            bits.append(f"checked={str(self.checked).lower()}")
        text = self.text.strip()
        return " ".join(bits) + ">" + (text[:80] if text and text != self.name else "")


@dataclass
class PageSnapshot:
    url: str
    title: str
    text: str
    elements: list[InteractiveElement] = field(default_factory=list)
    loading: bool = False
    scroll: tuple[int, int, int] = (0, 0, 0)  # scrollY, viewport height, document height
    tabs: list[dict[str, Any]] = field(default_factory=list)
    file_inputs: int = 0
    screenshot_b64: str | None = None

    def element(self, index: int) -> InteractiveElement | None:
        for el in self.elements:
            if el.index == index:
                return el
        return None

    def as_prompt(self, max_elements: int = 80, max_text: int = 2500) -> str:
        lines = [
            f"URL: {self.url}",
            f"TITLE: {self.title}",
            f"SCROLL: {self.scroll[0]}px of {self.scroll[2]}px (viewport {self.scroll[1]}px)",
        ]
        if self.tabs:
            lines.append(
                "TABS: "
                + "; ".join(
                    f"{t.get('index')}:{t.get('title', '')[:40]}{' *' if t.get('active') else ''}"
                    for t in self.tabs
                )
            )
        lines.append("INTERACTIVE ELEMENTS:")
        for el in self.elements[:max_elements]:
            lines.append("  " + el.describe())
        if len(self.elements) > max_elements:
            lines.append(f"  … {len(self.elements) - max_elements} more elements")
        lines.append("VISIBLE TEXT:")
        lines.append(self.text[:max_text])
        return "\n".join(lines)


@dataclass
class DownloadResult:
    path: Path
    suggested_filename: str
    url: str = ""
    size_bytes: int = 0


@runtime_checkable
class BrowserDriver(Protocol):
    """Operations available to adapters and agent tools."""

    # navigation ------------------------------------------------------------------
    async def goto(self, url: str, *, wait_until: str = "load", timeout_ms: int | None = None) -> None: ...
    async def back(self) -> None: ...
    async def forward(self) -> None: ...
    async def reload(self) -> None: ...
    async def url(self) -> str: ...
    async def title(self) -> str: ...

    # interaction -----------------------------------------------------------------
    async def click(
        self, target: TargetSpec, *, timeout_ms: int | None = None, force: bool = False
    ) -> None: ...
    async def type(
        self,
        target: TargetSpec,
        text: str,
        *,
        clear: bool = True,
        press_enter: bool = False,
        delay_ms: int = 0,
    ) -> None: ...
    async def press(self, key: str, target: TargetSpec | None = None) -> None: ...
    async def select_option(self, target: TargetSpec, value: str) -> None: ...
    async def scroll(
        self, direction: str = "down", amount: int = 600, target: TargetSpec | None = None
    ) -> None: ...
    async def upload(self, target: TargetSpec | None, files: list[Path]) -> None: ...
    async def hover(self, target: TargetSpec) -> None: ...

    # observation -----------------------------------------------------------------
    async def exists(self, target: TargetSpec, *, timeout_ms: int = 0) -> bool: ...
    async def wait_for(
        self, target: TargetSpec, *, state: str = "visible", timeout_ms: int | None = None
    ) -> bool: ...
    async def wait_for_text(self, text: str, *, timeout_ms: int | None = None) -> bool: ...
    async def wait_for_url(self, pattern: str, *, timeout_ms: int | None = None) -> bool: ...
    async def wait_for_load(self, *, state: str = "load", timeout_ms: int | None = None) -> None: ...
    async def extract_text(self, target: TargetSpec | None = None) -> str: ...
    async def extract_attribute(self, target: TargetSpec, attribute: str) -> str | None: ...
    async def page_text(self, max_chars: int = 20_000) -> str: ...
    async def snapshot(self, *, include_screenshot: bool = False) -> PageSnapshot: ...
    async def accessibility_tree(self, max_nodes: int = 400) -> list[dict[str, Any]]: ...
    async def screenshot(self, *, full_page: bool = False, quality: int = 60) -> bytes: ...
    async def evaluate(self, expression: str) -> Any: ...

    # downloads / tabs ------------------------------------------------------------
    async def download(
        self, target: TargetSpec | None, *, timeout_ms: int | None = None, trigger: Any = None
    ) -> DownloadResult: ...
    async def list_tabs(self) -> list[dict[str, Any]]: ...
    async def switch_tab(self, index: int) -> None: ...
    async def close_tab(self, index: int | None = None) -> None: ...
    async def new_tab(self, url: str | None = None) -> None: ...


def normalize_target(target: TargetSpec) -> dict[str, Any]:
    if isinstance(target, int):
        return {"index": target}
    if isinstance(target, dict):
        return target
    value = str(target).strip()
    if value.startswith("#") and value[1:].isdigit():
        return {"index": int(value[1:])}
    for prefix in ("text=", "role=", "placeholder=", "label=", "testid=", "css=", "xpath="):
        if value.lower().startswith(prefix):
            key, rest = prefix[:-1], value[len(prefix) :]
            if key == "role" and ":" in rest:
                role, name = rest.split(":", 1)
                return {"role": role.strip(), "name": name.strip()}
            return {key: rest}
    return {"css": value}


def describe_target(target: TargetSpec) -> str:
    spec = normalize_target(target)
    if "any" in spec:
        return " | ".join(describe_target(s) for s in spec["any"])
    return ", ".join(f"{k}={v}" for k, v in spec.items())
