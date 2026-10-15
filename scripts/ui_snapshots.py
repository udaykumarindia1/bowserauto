"""Visual smoke test: log into the running web app with the bundled Chromium and screenshot every page.

Usage (API on :8000 and the Next proxy on :3000 must be running):

    python scripts/ui_snapshots.py --out /tmp/ui --email you@example.com --password secret

Creates the account when it does not exist yet (registration must be open). Screenshots are PNGs
named after the route. Console errors raised by the pages are printed at the end and make the
script exit non-zero, so this doubles as a crude front-end regression check in CI-like setups.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.browser.launcher import resolve_launch_config  # noqa: E402

ROUTES = [
    "/dashboard",
    "/new-task",
    "/projects",
    "/sessions",
    "/tasks",
    "/files",
    "/platforms",
    "/templates",
    "/activity",
    "/settings",
    "/settings?tab=memory",
]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("UI_BASE_URL", "http://127.0.0.1:3000"))
    parser.add_argument("--out", default="/tmp/ui")
    parser.add_argument("--email", default=os.environ.get("UI_EMAIL", "snapshot@example.com"))
    parser.add_argument("--password", default=os.environ.get("UI_PASSWORD", "Snapshot-Pass-123"))
    parser.add_argument("--routes", nargs="*", default=ROUTES)
    parser.add_argument("--width", type=int, default=1480)
    parser.add_argument("--height", type=int, default=940)
    args = parser.parse_args()

    from playwright.async_api import async_playwright

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = resolve_launch_config()
    if not cfg.executable_path:
        print("No Chromium available – run scripts/fetch_chromium.py first")
        return 2
    console_errors: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=cfg.executable_path, args=cfg.args, env={**os.environ, **cfg.env, "HOME": "/tmp"}, headless=True)
        context = await browser.new_context(viewport={"width": args.width, "height": args.height}, color_scheme="dark")
        page = await context.new_page()
        page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))

        # Register or log in through the real UI.
        await page.goto(f"{args.base}/register", wait_until="networkidle")
        if "/register" in page.url:
            await page.fill("#full_name", "Snapshot User") if await page.locator("#full_name").count() else None
            await page.fill("input[type=email]", args.email)
            await page.fill("input[type=password]", args.password)
            await page.click("button[type=submit]")
            await page.wait_for_timeout(1500)
        if "/register" in page.url or "/login" in page.url:
            await page.goto(f"{args.base}/login", wait_until="networkidle")
            await page.fill("input[type=email]", args.email)
            await page.fill("input[type=password]", args.password)
            await page.click("button[type=submit]")
            await page.wait_for_url("**/dashboard", timeout=15000)
        await page.screenshot(path=str(out / "login-done.png"))

        for route in args.routes:
            name = route.strip("/").replace("/", "_").replace("?", "_").replace("=", "-") or "root"
            await page.goto(f"{args.base}{route}", wait_until="networkidle")
            await page.wait_for_timeout(1200)
            await page.screenshot(path=str(out / f"{name}.png"), full_page=False)
            print(f"captured {route} -> {name}.png")
        await browser.close()

    errors = [e for e in console_errors if "favicon" not in e and "Download the React DevTools" not in e]
    if errors:
        print("\nConsole errors:")
        for e in errors[:40]:
            print("  ", e[:300])
        return 1
    print("\nNo console errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
