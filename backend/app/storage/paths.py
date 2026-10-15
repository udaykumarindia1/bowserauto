"""Project folder layout and path safety helpers.

Layout::

    {DATA_DIR}/projects/{project_id}/
        script/
        references/
        scenes/
        generated/
        final/
        logs/
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from app.core.config import get_settings

PROJECT_SUBDIRS = ("script", "references", "scenes", "generated", "final", "logs")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def ensure_data_dirs() -> None:
    """Create the data directory tree (idempotent)."""
    settings = get_settings()
    for path in (settings.resolved_data_dir, settings.projects_dir, settings.profiles_dir, settings.screenshots_dir, settings.tmp_dir):
        path.mkdir(parents=True, exist_ok=True)
    try:
        settings.profiles_dir.chmod(0o700)
    except OSError:
        pass


def project_dir(project_id: uuid.UUID | str) -> Path:
    path = get_settings().projects_dir / str(project_id)
    return path


def ensure_project_dirs(project_id: uuid.UUID | str) -> Path:
    base = project_dir(project_id)
    for sub in PROJECT_SUBDIRS:
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def safe_filename(name: str, default: str = "file") -> str:
    name = os.path.basename(name or "").strip().replace(" ", "_")
    name = _SAFE_NAME.sub("_", name)
    name = re.sub(r"_{2,}", "_", name).replace("_.", ".")
    name = name.strip("._")
    if not name:
        name = default
    return name[:180]


def resolve_inside(base: Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` and make sure it stays inside ``base`` (prevents path traversal)."""
    base_resolved = base.resolve()
    target = (base_resolved / candidate).resolve() if not Path(candidate).is_absolute() else Path(candidate).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise PermissionError(f"Path {candidate} escapes {base}") from exc
    return target


def unique_path(path: Path) -> Path:
    """Return a non-colliding path by appending _v2, _v3 ... before the extension."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 10_000):
        candidate = path.with_name(f"{stem}_v{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a free filename for {path}")


def format_scene_filename(pattern: str, *, index: int, code: str, project_name: str, platform: str, ext: str) -> str:
    """Render a filename pattern such as ``scene_{index:03d}.mp4``."""
    pattern = pattern or "scene_{index:03d}{ext}"
    ext = ext if ext.startswith(".") else f".{ext}"
    try:
        rendered = pattern.format(index=index, code=code, project=safe_filename(project_name, "project"), platform=platform, ext=ext)
    except (KeyError, IndexError, ValueError):
        rendered = f"scene_{index:03d}{ext}"
    if not rendered.lower().endswith(ext.lower()):
        rendered += ext
    return safe_filename(rendered, f"scene_{index:03d}{ext}")
