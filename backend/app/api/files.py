"""Generated files + signed downloads."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse

from app.core.deps import DB, CurrentAuth, require_role
from app.core.errors import NotFoundError
from app.models import OrgRole
from app.schemas import GeneratedFileOut, SignedUrlOut
from app.services import file_service
from app.services.audit import audit

router = APIRouter(prefix="/files", tags=["files"])
Writer = Annotated[object, Depends(require_role(OrgRole.member.value))]


@router.get("", response_model=dict)
async def list_files(auth: CurrentAuth, db: DB, project_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None, limit: int = Query(default=200, le=1000), offset: int = 0) -> dict:
    rows, total = await file_service.list_generated_files(db, auth.org_id, project_id=project_id, task_id=task_id, limit=limit, offset=offset)
    items = []
    for row in rows:
        data = GeneratedFileOut.model_validate(row).model_dump(by_alias=True)
        data["download"] = file_service.signed_url("generated", auth.org_id, row.id)
        data["preview"] = file_service.signed_url("generated", auth.org_id, row.id, inline=True, ttl=3600)
        items.append(data)
    return {"items": items, "total": total}


@router.get("/signed/{token}", include_in_schema=False)
async def download_signed(token: str, db: DB) -> FileResponse:
    path, filename, mime, inline = await file_service.resolve_signed(db, token)
    if not path.exists():
        raise NotFoundError("File is missing on disk")
    disposition = "inline" if inline else "attachment"
    return FileResponse(path, media_type=mime, filename=None if inline else filename, headers={"Content-Disposition": f'{disposition}; filename="{filename}"', "Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"})


@router.get("/{file_id}", response_model=dict)
async def get_file(file_id: uuid.UUID, auth: CurrentAuth, db: DB) -> dict:
    row = await file_service.get_generated_file(db, auth.org_id, file_id)
    data = GeneratedFileOut.model_validate(row).model_dump(by_alias=True)
    data["download"] = file_service.signed_url("generated", auth.org_id, row.id)
    data["preview"] = file_service.signed_url("generated", auth.org_id, row.id, inline=True, ttl=3600)
    return data


@router.get("/{file_id}/url", response_model=SignedUrlOut)
async def file_url(file_id: uuid.UUID, auth: CurrentAuth, db: DB, inline: bool = False) -> SignedUrlOut:
    row = await file_service.get_generated_file(db, auth.org_id, file_id)
    return SignedUrlOut(**file_service.signed_url("generated", auth.org_id, row.id, inline=inline))


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Writer) -> Response:
    row = await file_service.get_generated_file(db, auth.org_id, file_id)
    await audit(db, "file.deleted", request=request, user_id=auth.user_id, org_id=auth.org_id, resource_type="generated_file", resource_id=row.id, data={"filename": row.filename})
    await file_service.delete_generated_file(db, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
