"""Projects, characters, reference assets, script analysis and scene editing."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.deps import DB, CurrentAuth, require_role
from app.core.errors import ConflictError, NotFoundError
from app.models import OrgRole, Task, TaskStatus
from app.realtime.events import emit
from app.schemas import (
    AnalyzeScriptRequest,
    AnalyzeScriptResponse,
    AssetOut,
    CharacterIn,
    CharacterOut,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectSummary,
    ProjectUpdate,
    SceneIn,
    SceneOut,
    SceneReorder,
    SceneUpdate,
    SignedUrlOut,
)
from app.services import file_service, project_service
from app.services.audit import audit
from app.services.dashboard_service import estimate_task
from app.services.org_settings import effective_limits

router = APIRouter(prefix="/projects", tags=["projects"])
Writer = Annotated[object, Depends(require_role(OrgRole.member.value))]


def _summary(entry: dict) -> ProjectSummary:
    data = ProjectOut.model_validate(entry["project"]).model_dump()
    return ProjectSummary(
        **data,
        scene_count=entry["scene_count"],
        completed_scenes=entry["completed_scenes"],
        active_task_id=entry["active_task_id"],
        active_task_status=entry["active_task_status"],
    )


@router.get("", response_model=list[ProjectSummary])
async def list_projects(auth: CurrentAuth, db: DB, include_archived: bool = False) -> list[ProjectSummary]:
    return [
        _summary(e)
        for e in await project_service.list_projects(db, auth.org_id, include_archived=include_archived)
    ]


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, request: Request, auth: CurrentAuth, db: DB, _: Writer
) -> ProjectDetail:
    project = await project_service.create_project(db, auth.org_id, auth.user_id, payload.model_dump())
    await audit(
        db,
        "project.created",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="project",
        resource_id=project.id,
    )
    await emit(
        db,
        "project.updated",
        auth.org_id,
        f"Project '{project.name}' created",
        project_id=project.id,
        payload={"action": "created"},
    )
    await db.commit()
    return await _detail(db, project)


async def _detail(db, project) -> ProjectDetail:  # noqa: ANN001
    characters = await project_service.list_characters(db, project.id)
    scenes = await project_service.list_scenes(db, project.id)
    stats = await project_service.project_stats(db, project)
    return ProjectDetail(
        **ProjectOut.model_validate(project).model_dump(),
        characters=[CharacterOut.model_validate(c) for c in characters],
        scenes=[SceneOut.model_validate(s) for s in scenes],
        stats=stats,
    )


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: uuid.UUID, auth: CurrentAuth, db: DB) -> ProjectDetail:
    project = await project_service.get_project(db, auth.org_id, project_id)
    return await _detail(db, project)


@router.patch("/{project_id}", response_model=ProjectDetail)
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, auth: CurrentAuth, db: DB, _: Writer
) -> ProjectDetail:
    project = await project_service.get_project(db, auth.org_id, project_id)
    project = await project_service.update_project(db, project, payload.model_dump(exclude_unset=True))
    await emit(
        db,
        "project.updated",
        auth.org_id,
        f"Project '{project.name}' updated",
        project_id=project.id,
        payload={"action": "updated"},
    )
    await db.commit()
    return await _detail(db, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    request: Request,
    auth: CurrentAuth,
    db: DB,
    _: Annotated[object, Depends(require_role(OrgRole.admin.value))],
) -> Response:
    project = await project_service.get_project(db, auth.org_id, project_id)
    from sqlalchemy import select

    active = (
        await db.execute(
            select(Task.id).where(
                Task.project_id == project.id, Task.status.in_([s.value for s in TaskStatus if s.is_active])
            )
        )
    ).first()
    if active:
        raise ConflictError("Stop the project's running task before deleting it")
    await audit(
        db,
        "project.deleted",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="project",
        resource_id=project.id,
        data={"name": project.name},
    )
    await project_service.delete_project(db, project)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/export")
async def export_project(project_id: uuid.UUID, auth: CurrentAuth, db: DB) -> StreamingResponse:
    project = await project_service.get_project(db, auth.org_id, project_id)
    scenes = [
        SceneOut.model_validate(s).model_dump(mode="json")
        for s in await project_service.list_scenes(db, project.id)
    ]
    data, filename = await file_service.build_project_zip(db, project, scenes=scenes)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
        },
    )


@router.get("/{project_id}/estimate")
async def estimate(project_id: uuid.UUID, auth: CurrentAuth, db: DB) -> dict:
    project = await project_service.get_project(db, auth.org_id, project_id)
    scenes = await project_service.list_scenes(db, project.id)
    pending = [s for s in scenes if s.status != "completed"]
    clip = float((project.output_settings or {}).get("clip_duration_seconds") or 8.0)
    return estimate_task(
        len(pending), clip, platform_key=project.platform_key, limits=effective_limits(auth.organization)
    )


# ------------------------------------------------------------------------ characters
@router.get("/{project_id}/characters", response_model=list[CharacterOut])
async def list_characters(project_id: uuid.UUID, auth: CurrentAuth, db: DB) -> list[CharacterOut]:
    project = await project_service.get_project(db, auth.org_id, project_id)
    return [CharacterOut.model_validate(c) for c in await project_service.list_characters(db, project.id)]


@router.post("/{project_id}/characters", response_model=CharacterOut, status_code=status.HTTP_201_CREATED)
async def create_character(
    project_id: uuid.UUID, payload: CharacterIn, auth: CurrentAuth, db: DB, _: Writer
) -> CharacterOut:
    project = await project_service.get_project(db, auth.org_id, project_id)
    character = await project_service.create_character(db, project, payload.model_dump())
    await db.commit()
    return CharacterOut.model_validate(character)


@router.patch("/{project_id}/characters/{character_id}", response_model=CharacterOut)
async def update_character(
    project_id: uuid.UUID, character_id: uuid.UUID, payload: CharacterIn, auth: CurrentAuth, db: DB, _: Writer
) -> CharacterOut:
    project = await project_service.get_project(db, auth.org_id, project_id)
    character = await project_service.update_character(
        db, project, character_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return CharacterOut.model_validate(character)


@router.delete("/{project_id}/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(
    project_id: uuid.UUID, character_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer
) -> Response:
    project = await project_service.get_project(db, auth.org_id, project_id)
    await project_service.delete_character(db, project, character_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------- assets
@router.get("/{project_id}/assets", response_model=list[AssetOut])
async def list_assets(project_id: uuid.UUID, auth: CurrentAuth, db: DB) -> list[AssetOut]:
    project = await project_service.get_project(db, auth.org_id, project_id)
    return [
        AssetOut.model_validate(a) for a in await project_service.list_assets(db, auth.org_id, project.id)
    ]


@router.post("/{project_id}/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    project_id: uuid.UUID,
    auth: CurrentAuth,
    db: DB,
    _: Writer,
    file: UploadFile = File(...),
    kind: str = Form("reference"),
) -> AssetOut:
    project = await project_service.get_project(db, auth.org_id, project_id)
    if kind not in ("reference", "character_reference", "script", "other"):
        kind = "reference"
    asset = await project_service.save_upload(db, project, file, kind=kind, user_id=auth.user_id)
    await emit(
        db,
        "file.created",
        auth.org_id,
        f"Reference '{asset.filename}' uploaded",
        project_id=project.id,
        payload={"asset_id": str(asset.id), "kind": kind},
    )
    await db.commit()
    return AssetOut.model_validate(asset)


@router.get("/{project_id}/assets/{asset_id}/url", response_model=SignedUrlOut)
async def asset_url(
    project_id: uuid.UUID, asset_id: uuid.UUID, auth: CurrentAuth, db: DB, inline: bool = True
) -> SignedUrlOut:
    project = await project_service.get_project(db, auth.org_id, project_id)
    asset = await project_service.get_asset(db, auth.org_id, asset_id)
    if asset.project_id != project.id:
        raise NotFoundError("Asset not found")
    return SignedUrlOut(**file_service.signed_url("asset", auth.org_id, asset.id, inline=inline))


@router.delete("/{project_id}/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    project_id: uuid.UUID, asset_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer
) -> Response:
    project = await project_service.get_project(db, auth.org_id, project_id)
    asset = await project_service.get_asset(db, auth.org_id, asset_id)
    if asset.project_id != project.id:
        raise NotFoundError("Asset not found")
    await project_service.delete_asset(db, asset)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------- script
@router.post("/{project_id}/analyze", response_model=AnalyzeScriptResponse)
async def analyze_script(
    project_id: uuid.UUID, payload: AnalyzeScriptRequest, auth: CurrentAuth, db: DB, _: Writer
) -> AnalyzeScriptResponse:
    project = await project_service.get_project(db, auth.org_id, project_id)
    result, scenes = await project_service.run_script_analysis(
        db,
        project,
        script_text=payload.script_text,
        clip_duration=payload.clip_duration_seconds,
        target_duration=payload.target_duration_seconds,
        replace_existing=payload.replace_existing,
        use_llm=payload.use_llm,
    )
    await emit(
        db,
        "project.updated",
        auth.org_id,
        f"Script analysed into {len(scenes)} scenes ({result.method})",
        project_id=project.id,
        payload={"action": "analyzed", "scenes": len(scenes), "method": result.method},
    )
    await db.commit()
    return AnalyzeScriptResponse(
        scenes=[SceneOut.model_validate(s) for s in scenes], method=result.method, warnings=result.warnings
    )


# ---------------------------------------------------------------------------- scenes
@router.get("/{project_id}/scenes", response_model=list[SceneOut])
async def list_scenes(project_id: uuid.UUID, auth: CurrentAuth, db: DB) -> list[SceneOut]:
    project = await project_service.get_project(db, auth.org_id, project_id)
    return [SceneOut.model_validate(s) for s in await project_service.list_scenes(db, project.id)]


@router.post("/{project_id}/scenes", response_model=SceneOut, status_code=status.HTTP_201_CREATED)
async def add_scene(
    project_id: uuid.UUID,
    payload: SceneIn,
    auth: CurrentAuth,
    db: DB,
    _: Writer,
    after: uuid.UUID | None = Query(default=None),
) -> SceneOut:
    project = await project_service.get_project(db, auth.org_id, project_id)
    scene = await project_service.add_scene(db, project, payload.model_dump(), after_scene_id=after)
    await emit(
        db,
        "scene.updated",
        auth.org_id,
        f"{scene.code} added",
        project_id=project.id,
        scene_id=scene.id,
        payload={"action": "added"},
    )
    await db.commit()
    return SceneOut.model_validate(scene)


@router.patch("/{project_id}/scenes/{scene_id}", response_model=SceneOut)
async def update_scene(
    project_id: uuid.UUID, scene_id: uuid.UUID, payload: SceneUpdate, auth: CurrentAuth, db: DB, _: Writer
) -> SceneOut:
    project = await project_service.get_project(db, auth.org_id, project_id)
    scene = await project_service.update_scene(db, project, scene_id, payload.model_dump(exclude_unset=True))
    await emit(
        db,
        "scene.updated",
        auth.org_id,
        f"{scene.code} edited",
        project_id=project.id,
        scene_id=scene.id,
        payload={"action": "edited"},
    )
    await db.commit()
    return SceneOut.model_validate(scene)


@router.post(
    "/{project_id}/scenes/{scene_id}/duplicate", response_model=SceneOut, status_code=status.HTTP_201_CREATED
)
async def duplicate_scene(
    project_id: uuid.UUID, scene_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer
) -> SceneOut:
    project = await project_service.get_project(db, auth.org_id, project_id)
    scene = await project_service.duplicate_scene(db, project, scene_id)
    await db.commit()
    return SceneOut.model_validate(scene)


@router.post("/{project_id}/scenes/{scene_id}/regenerate-prompt", response_model=SceneOut)
async def regenerate_prompt(
    project_id: uuid.UUID, scene_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer
) -> SceneOut:
    project = await project_service.get_project(db, auth.org_id, project_id)
    scene = await project_service.regenerate_scene_prompt(db, project, scene_id)
    await db.commit()
    return SceneOut.model_validate(scene)


@router.delete("/{project_id}/scenes/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scene(
    project_id: uuid.UUID, scene_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer
) -> Response:
    project = await project_service.get_project(db, auth.org_id, project_id)
    await project_service.delete_scene(db, project, scene_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/scenes/reorder", response_model=list[SceneOut])
async def reorder_scenes(
    project_id: uuid.UUID, payload: SceneReorder, auth: CurrentAuth, db: DB, _: Writer
) -> list[SceneOut]:
    project = await project_service.get_project(db, auth.org_id, project_id)
    scenes = await project_service.reorder_scenes(db, project, payload.scene_ids)
    await db.commit()
    return [SceneOut.model_validate(s) for s in scenes]
