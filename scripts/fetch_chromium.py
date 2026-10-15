#!/usr/bin/env python
"""Fetch a self-contained headless Chromium for local development.

Playwright's own ``playwright install chromium`` needs access to Playwright's CDN. In locked
down environments (CI sandboxes, corporate networks) that is often blocked while the npm
registry is reachable, so this helper downloads the ``@sparticuz/chromium`` package instead
(a headless-shell build with all shared libraries bundled) and unpacks it into
``~/.cache/bowser-chromium/extracted``.

Afterwards set ``CHROMIUM_EXECUTABLE_PATH=<dest>/chromium`` in ``backend/.env`` (``dev_setup.sh``
does that automatically). Production images use the official Playwright base image and do
not need this.

Usage: python scripts/fetch_chromium.py [--dest DIR] [--version 153.0.0]
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

DEFAULT_VERSION = "153.0.0"
DEFAULT_DEST = Path.home() / ".cache" / "bowser-chromium"


def log(message: str) -> None:
    print(f"[fetch-chromium] {message}", flush=True)


def download(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 1_000_000:
        log(f"using cached {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response, open(target, "wb") as fh:  # noqa: S310
        shutil.copyfileobj(response, fh)
    log(f"saved {target} ({target.stat().st_size // 1024} KB)")


def brotli_decompress(data: bytes) -> bytes:
    try:
        import brotli  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - depends on the environment
        sys.exit("The 'brotli' package is required: pip install brotli")
    return brotli.decompress(data)


def extract_tar_bytes(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        # Refuse members that would escape the destination (tar path traversal).
        for member in tar.getmembers():
            resolved = (dest / member.name).resolve()
            if not str(resolved).startswith(str(dest.resolve())):
                raise RuntimeError(f"unsafe tar member: {member.name}")
        tar.extractall(dest)  # noqa: S202 - members validated above


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="cache directory (default: ~/.cache/bowser-chromium)")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="@sparticuz/chromium version")
    parser.add_argument("--force", action="store_true", help="re-extract even if the binary already exists")
    args = parser.parse_args()

    extracted = args.dest / "extracted"
    binary = extracted / "chromium"
    if binary.exists() and not args.force:
        log(f"already present: {binary}")
        print(binary)
        return 0

    tarball = args.dest / f"chromium-{args.version}.tgz"
    download(f"https://registry.npmjs.org/@sparticuz/chromium/-/chromium-{args.version}.tgz", tarball)

    extracted.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as package:
        members = {m.name: m for m in package.getmembers() if m.name.startswith("package/bin/") and m.isfile()}
        if "package/bin/chromium.br" not in members:
            sys.exit(f"unexpected package layout: {sorted(members)}")
        for name, member in members.items():
            fh = package.extractfile(member)
            assert fh is not None
            raw = fh.read()
            payload = brotli_decompress(raw) if name.endswith(".br") else raw
            stem = Path(name).name.removesuffix(".br")
            if stem == "chromium":
                binary.write_bytes(payload)
                binary.chmod(0o755)
                log(f"wrote {binary} ({len(payload) // (1024 * 1024)} MB)")
            elif stem.endswith(".tar"):
                # al2023.tar -> extracted/lib/..., swiftshader.tar -> next to the binary, fonts.tar -> extracted/fonts
                target = {"al2023.tar": extracted / "lib", "swiftshader.tar": extracted, "fonts.tar": extracted / "fonts"}.get(stem, extracted / stem.removesuffix(".tar"))
                extract_tar_bytes(payload, target)
                log(f"extracted {stem} -> {target}")
            else:
                (extracted / stem).write_bytes(payload)
                log(f"wrote {extracted / stem}")

    # The bundled fontconfig tree is not usable as FONTCONFIG_PATH on Debian hosts; the system
    # fontconfig is used instead. Keep the directory for reference only.
    libs = [p for p in (extracted / "lib" / "lib", extracted / "lib", extracted) if p.exists() and any(p.glob("*.so*"))]
    log("shared library dirs: " + ", ".join(str(p) for p in libs))
    log("export CHROMIUM_EXECUTABLE_PATH=" + str(binary))
    print(binary)
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    raise SystemExit(main())
