"""Projects, characters, assets and scene persistence."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import build_llm_client
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models import Asset, Character, GeneratedFile, Project, Scene, SceneStatus, Task, TaskStatus
from app.platforms.registry import get_adapter_class
from app.services.org_settings import llm_overrides_for
from app.services.scene_engine import AnalysisContext, AnalysisResult, analyze_script
from app.storage.files import sha256_file, sniff_kind
from app.storage.paths import ensure_project_dirs, project_dir, safe_filename, unique_path


async def list_projects(
    db: AsyncSession, org_id: uuid.UUID, *, include_archived: bool = False
) -> list[dict[str, Any]]:
    query = select(Project).where(Project.organization_id == org_id)
    if not include_archived:
        query = query.where(Project.status != "archived")
    projects = (await db.execute(query.order_by(Project.updated_at.desc()))).scalars().all()
    if not projects:
        return []
    ids = [p.id for p in projects]
    counts = (
        await db.execute(
            select(
                Scene.project_id,
                func.count(Scene.id),
                func.sum(
                    func.cast(
                        Scene.status == SceneStatus.completed.value, type_=__import__("sqlalchemy").Integer
                    )
                ),
            )
            .where(Scene.project_id.in_(ids))
            .group_by(Scene.project_id)
        )
    ).all()
    stats = {pid: (total or 0, completed or 0) for pid, total, completed in counts}
    active_tasks = (
        await db.execute(
            select(Task.project_id, Task.id, Task.status)
            .where(Task.project_id.in_(ids), Task.status.in_([s.value for s in TaskStatus if s.is_active]))
            .order_by(Task.created_at.desc())
        )
    ).all()
    active: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    for pid, tid, status in active_tasks:
        active.setdefault(pid, (tid, status))
    out = []
    for p in projects:
        total, completed = stats.get(p.id, (0, 0))
        task = active.get(p.id)
        out.append(
            {
                "project": p,
                "scene_count": int(total),
                "completed_scenes": int(completed),
                "active_task_id": task[0] if task else None,
                "active_task_status": task[1] if task else None,
            }
        )
    return out


async def get_project(db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.organization_id != org_id:
        raise NotFoundError("Project not found")
    return project


async def create_project(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, data: dict[str, Any]
) -> Project:
    if get_adapter_class(data.get("platform_key") or "mock") is None:
        raise ValidationFailed(f"Unknown platform '{data.get('platform_key')}'")
    project = Project(organization_id=org_id, created_by=user_id, **data)
    db.add(project)
    await db.flush()
    folder = ensure_project_dirs(project.id)
    project.folder_path = str(folder)
    if project.script_text:
        _write_script_copy(project)
    await db.flush()
    return project


async def update_project(db: AsyncSession, project: Project, data: dict[str, Any]) -> Project:
    if "platform_key" in data and data["platform_key"] and get_adapter_class(data["platform_key"]) is None:
        raise ValidationFailed(f"Unknown platform '{data['platform_key']}'")
    for key, value in data.items():
        if key == "status" and value == "archived":
            project.status = "archived"
            from app.models.base import utcnow

            project.archived_at = utcnow()
            continue
        if key == "status" and value == "active":
            project.status = "active"
            project.archived_at = None
            continue
        setattr(project, key, value)
    if "script_text" in data:
        _write_script_copy(project)
    await db.flush()
    return project


async def delete_project(db: AsyncSession, project: Project) -> None:
    active = (
        await db.execute(
            select(Task.id).where(
                Task.project_id == project.id, Task.status.in_([s.value for s in TaskStatus if s.is_active])
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise ConflictError("Stop the running task before deleting this project")
    import shutil

    folder = project_dir(project.id)
    await db.delete(project)
    await db.flush()
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)


def _write_script_copy(project: Project) -> None:
    try:
        folder = ensure_project_dirs(project.id)
        (folder / "script" / "script.txt").write_text(project.script_text or "", encoding="utf-8")
    except OSError:
        pass


async def project_stats(db: AsyncSession, project: Project) -> dict[str, Any]:
    scene_rows = (
        await db.execute(
            select(Scene.status, func.count(Scene.id))
            .where(Scene.project_id == project.id)
            .group_by(Scene.status)
        )
    ).all()
    by_status = {status: int(count) for status, count in scene_rows}
    total = sum(by_status.values())
    duration = (
        await db.execute(
            select(func.coalesce(func.sum(Scene.duration_seconds), 0)).where(Scene.project_id == project.id)
        )
    ).scalar_one()
    files = (
        await db.execute(
            select(func.count(GeneratedFile.id), func.coalesce(func.sum(GeneratedFile.size_bytes), 0)).where(
                GeneratedFile.project_id == project.id
            )
        )
    ).one()
    active_task = (
        (
            await db.execute(
                select(Task)
                .where(
                    Task.project_id == project.id,
                    Task.status.in_([s.value for s in TaskStatus if s.is_active]),
                )
                .order_by(Task.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    return {
        "scene_count": total,
        "scenes_by_status": by_status,
        "completed_scenes": by_status.get(SceneStatus.completed.value, 0),
        "total_duration_seconds": float(duration or 0),
        "generated_files": int(files[0] or 0),
        "generated_bytes": int(files[1] or 0),
        "active_task_id": str(active_task.id) if active_task else None,
        "active_task_status": active_task.status if active_task else None,
    }


# --------------------------------------------------------------------------- characters
async def list_characters(db: AsyncSession, project_id: uuid.UUID) -> list[Character]:
    return list(
        (
            await db.execute(
                select(Character).where(Character.project_id == project_id).order_by(Character.created_at)
            )
        )
        .scalars()
        .all()
    )


async def create_character(db: AsyncSession, project: Project, data: dict[str, Any]) -> Character:
    if data.get("reference_asset_id"):
        await get_asset(db, project.organization_id, data["reference_asset_id"])
    character = Character(organization_id=project.organization_id, project_id=project.id, **data)
    db.add(character)
    await db.flush()
    return character


async def get_character(db: AsyncSession, project: Project, character_id: uuid.UUID) -> Character:
    character = await db.get(Character, character_id)
    if character is None or character.project_id != project.id:
        raise NotFoundError("Character not found")
    return character


async def update_character(
    db: AsyncSession, project: Project, character_id: uuid.UUID, data: dict[str, Any]
) -> Character:
    character = await get_character(db, project, character_id)
    if data.get("reference_asset_id"):
        await get_asset(db, project.organization_id, data["reference_asset_id"])
    for key, value in data.items():
        setattr(character, key, value)
    await db.flush()
    return character


async def delete_character(db: AsyncSession, project: Project, character_id: uuid.UUID) -> None:
    character = await get_character(db, project, character_id)
    await db.delete(character)


# -------------------------------------------------------------------------------- assets
async def get_asset(db: AsyncSession, org_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
    asset = await db.get(Asset, asset_id)
    if asset is None or asset.organization_id != org_id:
        raise NotFoundError("Asset not found")
    return asset


async def list_assets(
    db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID | None = None
) -> list[Asset]:
    query = select(Asset).where(Asset.organization_id == org_id)
    if project_id:
        query = query.where(Asset.project_id == project_id)
    return list((await db.execute(query.order_by(Asset.created_at.desc()))).scalars().all())


async def save_upload(
    db: AsyncSession, project: Project, upload: UploadFile, *, kind: str, user_id: uuid.UUID
) -> Asset:
    settings = get_settings()
    original = upload.filename or "upload"
    suffix = Path(original).suffix.lower()
    if suffix not in settings.allowed_upload_extensions:
        raise ValidationFailed(f"File type {suffix or '(none)'} is not allowed")
    folder = ensure_project_dirs(project.id) / (
        "references"
        if kind in ("reference", "character_reference")
        else "script"
        if kind == "script"
        else "references"
    )
    target = unique_path(folder / safe_filename(original, f"upload{suffix}"))
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    written = 0
    try:
        with open(target, "wb") as fh:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValidationFailed(f"File exceeds the {settings.max_upload_size_mb} MB upload limit")
                fh.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    sniffed = sniff_kind(target)
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif") and not sniffed.startswith("image/"):
        target.unlink(missing_ok=True)
        raise ValidationFailed("Uploaded file is not a valid image")
    if suffix in (".mp4", ".mov", ".webm") and not sniffed.startswith("video/"):
        target.unlink(missing_ok=True)
        raise ValidationFailed("Uploaded file is not a valid video")
    asset = Asset(
        organization_id=project.organization_id,
        project_id=project.id,
        uploaded_by=user_id,
        kind=kind,
        filename=target.name,
        path=str(target),
        mime_type=sniffed
        if sniffed != "application/octet-stream"
        else (upload.content_type or "application/octet-stream"),
        size_bytes=written,
        sha256=sha256_file(target),
        metadata_={"original_filename": original},
    )
    db.add(asset)
    await db.flush()
    return asset


async def delete_asset(db: AsyncSession, asset: Asset) -> None:
    path = Path(asset.path)
    await db.delete(asset)
    await db.flush()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


# -------------------------------------------------------------------------------- scenes
async def list_scenes(db: AsyncSession, project_id: uuid.UUID) -> list[Scene]:
    return list(
        (await db.execute(select(Scene).where(Scene.project_id == project_id).order_by(Scene.index)))
        .scalars()
        .all()
    )


async def get_scene(db: AsyncSession, project: Project, scene_id: uuid.UUID) -> Scene:
    scene = await db.get(Scene, scene_id)
    if scene is None or scene.project_id != project.id:
        raise NotFoundError("Scene not found")
    return scene


def _scene_code(index: int) -> str:
    return f"scene_{index:03d}"


async def renumber_scenes(db: AsyncSession, project_id: uuid.UUID) -> list[Scene]:
    scenes = await list_scenes(db, project_id)
    for i, scene in enumerate(scenes, start=1):
        scene.index = i
        scene.code = _scene_code(i)
    await db.flush()
    return scenes


async def replace_scenes(
    db: AsyncSession, project: Project, drafts: list[dict[str, Any]], *, replace_existing: bool
) -> list[Scene]:
    settings = get_settings()
    existing = await list_scenes(db, project.id)
    if replace_existing:
        locked = [
            s
            for s in existing
            if s.status
            in (SceneStatus.generating.value, SceneStatus.processing.value, SceneStatus.downloading.value)
        ]
        if locked:
            raise ConflictError(
                "Scenes are currently being generated; stop the task before re-analysing the script"
            )
        for scene in existing:
            await db.delete(scene)
        await db.flush()
        start = 1
    else:
        start = len(existing) + 1
    if start - 1 + len(drafts) > settings.max_scenes_per_task:
        raise ValidationFailed(f"Projects are limited to {settings.max_scenes_per_task} scenes")
    created: list[Scene] = []
    for offset, draft in enumerate(drafts):
        index = start + offset
        scene = Scene(
            organization_id=project.organization_id,
            project_id=project.id,
            index=index,
            code=_scene_code(index),
            status=SceneStatus.draft.value,
            **draft,
        )
        db.add(scene)
        created.append(scene)
    await db.flush()
    return created


async def add_scene(
    db: AsyncSession, project: Project, data: dict[str, Any], *, after_scene_id: uuid.UUID | None = None
) -> Scene:
    scenes = await list_scenes(db, project.id)
    if len(scenes) >= get_settings().max_scenes_per_task:
        raise ValidationFailed("Scene limit reached for this project")
    position = len(scenes)
    if after_scene_id:
        for i, s in enumerate(scenes):
            if s.id == after_scene_id:
                position = i + 1
                break
    data = {**data, "reference_asset_ids": [str(x) for x in data.get("reference_asset_ids", [])]}
    if data.get("platform_key") and get_adapter_class(data["platform_key"]) is None:
        raise ValidationFailed(f"Unknown platform '{data['platform_key']}'")
    if not (data.get("prompt") or "").strip():
        # Manually added scenes get a generated prompt from their fields (same engine as analysis).
        from app.services.scene_engine import SceneDraft, build_prompt

        ctx = await analysis_context_for(db, project)
        draft = SceneDraft(**{k: v for k, v in data.items() if k in SceneDraft.__dataclass_fields__})
        data["prompt"] = build_prompt(draft, ctx)
        data.setdefault("negative_prompt", "")
        if not data["negative_prompt"]:
            data["negative_prompt"] = ctx.negative_prompt
    scene = Scene(
        organization_id=project.organization_id,
        project_id=project.id,
        index=position + 1,
        code=_scene_code(position + 1),
        **data,
    )
    for s in scenes[position:]:
        s.index += 1
    db.add(scene)
    await db.flush()
    await renumber_scenes(db, project.id)
    return scene


async def update_scene(
    db: AsyncSession, project: Project, scene_id: uuid.UUID, data: dict[str, Any]
) -> Scene:
    scene = await get_scene(db, project, scene_id)
    if scene.status in (
        SceneStatus.generating.value,
        SceneStatus.processing.value,
        SceneStatus.downloading.value,
    ) and any(k != "status" for k in data):
        raise ConflictError("This scene is currently being generated")
    if "reference_asset_ids" in data and data["reference_asset_ids"] is not None:
        data["reference_asset_ids"] = [str(x) for x in data["reference_asset_ids"]]
    if "platform_key" in data and data["platform_key"] and get_adapter_class(data["platform_key"]) is None:
        raise ValidationFailed(f"Unknown platform '{data['platform_key']}'")
    for key, value in data.items():
        if key == "status" and value == "queued":
            scene.status = SceneStatus.queued.value
            scene.last_error = ""
            continue
        setattr(scene, key, value)
    await db.flush()
    return scene


async def duplicate_scene(db: AsyncSession, project: Project, scene_id: uuid.UUID) -> Scene:
    scene = await get_scene(db, project, scene_id)
    data = {
        "title": f"{scene.title} (copy)" if scene.title else "",
        "duration_seconds": scene.duration_seconds,
        "characters": list(scene.characters),
        "location": scene.location,
        "visual": scene.visual,
        "camera": scene.camera,
        "dialogue": scene.dialogue,
        "narration": scene.narration,
        "prompt": scene.prompt,
        "negative_prompt": scene.negative_prompt,
        "platform_key": scene.platform_key,
        "reference_asset_ids": list(scene.reference_asset_ids),
    }
    return await add_scene(db, project, data, after_scene_id=scene.id)


async def delete_scene(db: AsyncSession, project: Project, scene_id: uuid.UUID) -> None:
    scene = await get_scene(db, project, scene_id)
    if scene.status in (
        SceneStatus.generating.value,
        SceneStatus.processing.value,
        SceneStatus.downloading.value,
    ):
        raise ConflictError("This scene is currently being generated")
    await db.delete(scene)
    await db.flush()
    await renumber_scenes(db, project.id)


async def reorder_scenes(db: AsyncSession, project: Project, scene_ids: list[uuid.UUID]) -> list[Scene]:
    scenes = await list_scenes(db, project.id)
    by_id = {s.id: s for s in scenes}
    if set(scene_ids) != set(by_id):
        raise ValidationFailed("scene_ids must contain every scene of the project exactly once")
    for i, sid in enumerate(scene_ids, start=1):
        by_id[sid].index = i
        by_id[sid].code = _scene_code(i)
    await db.flush()
    return await list_scenes(db, project.id)


async def analysis_context_for(
    db: AsyncSession,
    project: Project,
    *,
    clip_duration: float | None = None,
    target_duration: float | None = None,
) -> AnalysisContext:
    characters = await list_characters(db, project.id)
    settings = get_settings()
    output = project.output_settings or {}
    adapter_cls = get_adapter_class(project.platform_key)
    max_clip = adapter_cls.capabilities.get("max_clip_seconds") if adapter_cls else None
    clip = float(clip_duration or output.get("clip_duration_seconds") or 8.0)
    if max_clip:
        clip = min(clip, float(max_clip))
    return AnalysisContext(
        visual_style=project.visual_style or "cinematic",
        clip_duration_seconds=clip,
        target_duration_seconds=target_duration or output.get("total_duration_seconds"),
        max_scenes=settings.max_scenes_per_task,
        characters=[
            {
                "name": c.name,
                "description": c.description,
                "reference_asset_id": str(c.reference_asset_id) if c.reference_asset_id else None,
            }
            for c in characters
        ],
        aspect_ratio=output.get("aspect_ratio") or "16:9",
        platform_name=adapter_cls.name if adapter_cls else project.platform_key,
    )


async def regenerate_scene_prompt(db: AsyncSession, project: Project, scene_id: uuid.UUID) -> Scene:
    from app.services.scene_engine import LLMMessage, SceneDraft, build_prompt

    scene = await get_scene(db, project, scene_id)
    ctx = await analysis_context_for(db, project)
    draft = SceneDraft(
        title=scene.title,
        duration_seconds=scene.duration_seconds,
        characters=list(scene.characters),
        location=scene.location,
        visual=scene.visual,
        camera=scene.camera,
        dialogue=scene.dialogue,
        narration=scene.narration,
    )
    llm = build_llm_client(llm_overrides_for(await _org_for(db, project)))
    new_prompt = None
    if llm is not None:
        try:
            response = await llm.complete(
                [
                    LLMMessage(
                        "system",
                        "You write concise, vivid prompts for AI video generators. Reply with the prompt text only.",
                    ),
                    LLMMessage(
                        "user",
                        f"Style: {ctx.visual_style}. Duration {scene.duration_seconds}s. Characters: {ctx.characters}. Scene: {draft.to_dict()}",
                    ),
                ],
                max_tokens=600,
            )
            new_prompt = response.text.strip()[:12000] or None
        except Exception:
            new_prompt = None
    scene.prompt = new_prompt or build_prompt(draft, ctx)
    await db.flush()
    return scene


async def _org_for(db: AsyncSession, project: Project):
    from app.models import Organization

    return await db.get(Organization, project.organization_id)


async def run_script_analysis(
    db: AsyncSession,
    project: Project,
    *,
    script_text: str | None,
    clip_duration: float | None,
    target_duration: float | None,
    replace_existing: bool,
    use_llm: bool | None,
) -> tuple[AnalysisResult, list[Scene]]:
    if script_text is not None:
        project.script_text = script_text
        _write_script_copy(project)
    if not (project.script_text or "").strip():
        raise ValidationFailed("Add a script before analysing it")
    ctx = await analysis_context_for(
        db, project, clip_duration=clip_duration, target_duration=target_duration
    )
    if clip_duration or target_duration:
        output = dict(project.output_settings or {})
        if clip_duration:
            output["clip_duration_seconds"] = clip_duration
        if target_duration:
            output["total_duration_seconds"] = target_duration
        project.output_settings = output
    llm = build_llm_client(llm_overrides_for(await _org_for(db, project)))
    result = await analyze_script(project.script_text, ctx, llm, use_llm=use_llm)
    drafts = [d.to_dict() for d in result.scenes]
    scenes = await replace_scenes(db, project, drafts, replace_existing=replace_existing)
    return result, scenes
