"""Chromium discovery and launch configuration.

Resolution order for the browser binary:

1. ``CHROMIUM_EXECUTABLE_PATH`` environment variable
2. Playwright's own managed Chromium (``playwright install chromium``)
3. Well-known fallback locations (bundled builds fetched by ``scripts/fetch-chromium.sh``,
   system packages)
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings

FALLBACK_LOCATIONS = [
    Path.home() / ".cache" / "bowser-chromium" / "extracted" / "chromium",
    Path("/opt/bowser-chromium/chromium"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/google-chrome-stable"),
]

DEFAULT_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-features=TranslateUI",
    "--lang=en-US",
]


@dataclass
class LaunchConfig:
    executable_path: str | None
    env: dict[str, str] = field(default_factory=dict)
    args: list[str] = field(default_factory=list)
    headless: bool = True
    source: str = "playwright"

    @property
    def available(self) -> bool:
        return self.executable_path is None or Path(self.executable_path).exists()


def _playwright_managed_chromium() -> str | None:
    try:
        from playwright._impl._driver import compute_driver_executable  # noqa: F401  (ensures playwright is importable)
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return None
    # Playwright stores browsers under PLAYWRIGHT_BROWSERS_PATH or ~/.cache/ms-playwright
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    candidates = []
    if base:
        candidates.append(Path(base))
    candidates.append(Path.home() / ".cache" / "ms-playwright")
    candidates.append(Path("/ms-playwright"))
    for root in candidates:
        if not root.exists():
            continue
        for child in sorted(root.glob("chromium*-*"), reverse=True):
            for name in (
                "chrome-linux/chrome",
                "chrome-linux/headless_shell",
                "chrome-linux64/chrome",
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            ):
                exe = child / name
                if exe.exists():
                    return str(exe)
    return None


def resolve_launch_config() -> LaunchConfig:
    settings = get_settings()
    env: dict[str, str] = {}
    executable = settings.chromium_executable_path
    source = "env"
    if not executable:
        executable = _playwright_managed_chromium()
        source = "playwright"
    if not executable:
        for candidate in FALLBACK_LOCATIONS:
            if candidate.exists():
                executable = str(candidate)
                source = "fallback"
                break
    if not executable:
        which = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if which:
            executable, source = which, "path"
    lib_paths: list[str] = []
    if settings.chromium_lib_path:
        lib_paths.extend(p for p in settings.chromium_lib_path.split(":") if p)
    if executable:
        exe_dir = Path(executable).parent
        for sub in ("lib/lib", "lib", "."):
            candidate = exe_dir / sub
            if (candidate / "libnss3.so").exists() or (candidate / "libvk_swiftshader.so").exists():
                lib_paths.append(str(candidate))
    if lib_paths:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(dict.fromkeys([*lib_paths, *existing.split(":")])).strip(":")
    args = [*DEFAULT_ARGS, *settings.browser_extra_args]
    if os.geteuid() == 0 if hasattr(os, "geteuid") else False:
        args.append("--no-sandbox")
    if source in ("fallback", "env") and "--no-sandbox" not in args:
        # Bundled headless-shell builds have no setuid sandbox helper.
        args.append("--no-sandbox")
    return LaunchConfig(
        executable_path=executable,
        env=env,
        args=args,
        headless=settings.browser_headless,
        source=source if executable else "none",
    )


def browser_available() -> bool:
    cfg = resolve_launch_config()
    return cfg.executable_path is not None and Path(cfg.executable_path).exists()
