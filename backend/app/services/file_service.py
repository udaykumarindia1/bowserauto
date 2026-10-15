"""Generated files, assets, screenshots: listing, signed download URLs, zip export, deletion.

Downloads never expose raw filesystem paths. The API hands out short-lived signed URLs
(HMAC-signed JWT with org/file/kind) that the file endpoint validates before streaming.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailed
from app.core.security import sign_payload, verify_signed_payload
from app.models import Asset, GeneratedFile, Project, Scene, Screenshot
from app.realtime.events import emit
from app.storage.files import dir_size

FileKind = Literal["generated", "asset", "screenshot"]


async def list_generated_files(db: AsyncSession, org_id: uuid.UUID, *, project_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None, limit: int = 200, offset: int = 0) -> tuple[list[GeneratedFile], int]:
    query = select(GeneratedFile).where(GeneratedFile.organization_id == org_id)
    if project_id:
        query = query.where(GeneratedFile.project_id == project_id)
    if task_id:
        query = query.where(GeneratedFile.task_id == task_id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await db.execute(query.order_by(GeneratedFile.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return list(rows), int(total)


async def get_generated_file(db: AsyncSession, org_id: uuid.UUID, file_id: uuid.UUID) -> GeneratedFile:
    row = await db.get(GeneratedFile, file_id)
    if row is None or row.organization_id != org_id:
        raise NotFoundError("File not found")
    return row


async def get_screenshot(db: AsyncSession, org_id: uuid.UUID, screenshot_id: uuid.UUID) -> Screenshot:
    row = await db.get(Screenshot, screenshot_id)
    if row is None or row.organization_id != org_id:
        raise NotFoundError("Screenshot not found")
    return row


async def list_screenshots(db: AsyncSession, org_id: uuid.UUID, *, task_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None, limit: int = 100) -> list[Screenshot]:
    query = select(Screenshot).where(Screenshot.organization_id == org_id)
    if task_id:
        query = query.where(Screenshot.task_id == task_id)
    if session_id:
        query = query.where(Screenshot.browser_session_id == session_id)
    rows = (await db.execute(query.order_by(Screenshot.created_at.desc()).limit(limit))).scalars().all()
    return list(rows)


def signed_url(kind: FileKind, org_id: uuid.UUID, file_id: uuid.UUID, *, inline: bool = False, ttl: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    token = sign_payload({"k": kind, "o": str(org_id), "f": str(file_id), "i": 1 if inline else 0}, ttl_seconds=ttl)
    return {"url": f"/api/files/signed/{token}", "expires_in": ttl or settings.signed_url_ttl_seconds}


async def resolve_signed(db: AsyncSession, token: str) -> tuple[Path, str, str, bool]:
    """Validate a signed token and return (path, filename, mime_type, inline)."""
    payload = verify_signed_payload(token)
    if not payload:
        raise NotFoundError("This download link is invalid or has expired")
    kind = payload.get("k")
    org_id = uuid.UUID(str(payload.get("o")))
    file_id = uuid.UUID(str(payload.get("f")))
    inline = bool(payload.get("i"))
    if kind == "generated":
        row = await get_generated_file(db, org_id, file_id)
        return Path(row.path), row.filename, row.mime_type, inline
    if kind == "asset":
        asset = await db.get(Asset, file_id)
        if asset is None or asset.organization_id != org_id:
            raise NotFoundError("File not found")
        return Path(asset.path), asset.filename, asset.mime_type, inline
    if kind == "screenshot":
        shot = await get_screenshot(db, org_id, file_id)
        return Path(shot.path), Path(shot.path).name, "image/jpeg", True
    raise NotFoundError("Unknown file kind")


async def delete_generated_file(db: AsyncSession, row: GeneratedFile) -> None:
    path = Path(row.path)
    settings = get_settings()
    try:
        path.resolve().relative_to(settings.projects_dir.resolve())
        inside = True
    except ValueError:
        inside = False
    scenes = (await db.execute(select(Scene).where(Scene.generated_file_id == row.id))).scalars().all()
    for scene in scenes:
        scene.generated_file_id = None
        if scene.status == "completed":
            scene.status = "draft"
    org_id, project_id, filename = row.organization_id, row.project_id, row.filename
    await db.delete(row)
    await db.flush()
    if inside and path.exists():
        path.unlink(missing_ok=True)
    await emit(db, "file.deleted", org_id, f"{filename} deleted", project_id=project_id, payload={"filename": filename}, commit=True)


async def build_project_zip(
    db: AsyncSession,
    project: Project,
    *,
    include: tuple[str, ...] = ("generated", "final", "scenes", "references", "script", "logs"),
    scenes: list[dict[str, Any]] | None = None,
) -> tuple[bytes, str]:
    """Create a zip archive of a project's folder (sync work runs in a thread).

    ``scenes`` (serialised scene definitions) are written as ``scenes/scenes.json`` so the export
    is self-describing even before any generation ran.
    """
    root = Path(project.folder_path)
    if not root.exists():
        raise NotFoundError("Project folder does not exist")
    size = dir_size(root)
    if size > 4 * 1024 * 1024 * 1024:
        raise ValidationFailed("Project is too large to export as a single archive")
    manifest = {
        "project": {"id": str(project.id), "name": project.name, "platform_key": project.platform_key, "visual_style": project.visual_style, "exported_at": datetime.now(UTC).isoformat()},
        "scenes": scenes or [],
    }

    def _zip() -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            written: set[str] = set()
            for sub in include:
                folder = root / sub
                if not folder.exists():
                    continue
                for path in sorted(folder.rglob("*")):
                    if path.is_file() and ".incoming" not in path.parts:
                        arcname = path.relative_to(root).as_posix()
                        zf.write(path, arcname=arcname)
                        written.add(arcname)
            if "script" in include and project.script_text and "script/script.txt" not in written:
                zf.writestr("script/script.txt", project.script_text)
            if scenes is not None:
                zf.writestr("scenes/scenes.json", json.dumps(manifest, indent=2, default=str))
        return buffer.getvalue()

    data = await asyncio.to_thread(_zip)
    safe = "".join(c for c in project.name if c.isalnum() or c in " -_").strip() or "project"
    return data, f"{safe}.zip"


def storage_summary(org_project_ids: list[uuid.UUID]) -> dict[str, Any]:
    settings = get_settings()
    total = 0
    for pid in org_project_ids:
        folder = settings.projects_dir / str(pid)
        if folder.exists():
            total += dir_size(folder)
    return {"bytes": total, "mb": round(total / (1024 * 1024), 1)}
