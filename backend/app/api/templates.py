"""Project templates (built-in + custom)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.deps import DB, CurrentAuth, require_role
from app.models import OrgRole
from app.schemas import TemplateIn, TemplateOut, TemplateUpdate
from app.services import templates as template_service

router = APIRouter(prefix="/templates", tags=["templates"])
Writer = Annotated[object, Depends(require_role(OrgRole.member.value))]


@router.get("", response_model=list[TemplateOut])
async def list_templates(auth: CurrentAuth, db: DB) -> list[TemplateOut]:
    return [TemplateOut.model_validate(t) for t in await template_service.list_templates(db, auth.org_id)]


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateIn, auth: CurrentAuth, db: DB, _: Writer) -> TemplateOut:
    template = await template_service.create_template(db, auth.org_id, payload.model_dump())
    await db.commit()
    return TemplateOut.model_validate(template)


@router.get("/{template_id}", response_model=TemplateOut)
async def get_template(template_id: uuid.UUID, auth: CurrentAuth, db: DB) -> TemplateOut:
    return TemplateOut.model_validate(await template_service.get_template(db, auth.org_id, template_id))


@router.patch("/{template_id}", response_model=TemplateOut)
async def update_template(template_id: uuid.UUID, payload: TemplateUpdate, auth: CurrentAuth, db: DB, _: Writer) -> TemplateOut:
    template = await template_service.update_template(db, auth.org_id, template_id, payload.model_dump(exclude_unset=True, exclude_none=True))
    await db.commit()
    return TemplateOut.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer) -> Response:
    await template_service.delete_template(db, auth.org_id, template_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
