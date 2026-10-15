"""JARVIS console endpoints: natural-language commands, briefing, memory, host telemetry."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.deps import DB, CurrentAuth, require_role
from app.core.errors import NotFoundError
from app.core.sysinfo import system_metrics
from app.models import MemoryFact, OrgRole
from app.schemas import ORMModel
from app.services import assistant_service
from app.services.assistant_service import Assistant, CommandContext
from app.services.audit import audit

router = APIRouter(prefix="/assistant", tags=["assistant"])
Writer = Annotated[object, Depends(require_role(OrgRole.member.value))]


class CommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    confirmed: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class CommandResponse(BaseModel):
    reply: str
    intent: str
    ok: bool
    actions: list[dict[str, Any]]
    navigate: str | None
    confirm: dict[str, Any] | None
    client: dict[str, Any] | None
    data: dict[str, Any]


class MemoryIn(BaseModel):
    key: str = Field(default="", max_length=120)
    value: str = Field(min_length=1, max_length=4000)
    category: str = Field(default="notes", max_length=40)


class MemoryOut(ORMModel):
    id: uuid.UUID
    key: str
    value: str
    category: str
    source: str
    created_at: Any
    updated_at: Any


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _context(raw: dict[str, Any]) -> CommandContext:
    hour = raw.get("local_hour")
    return CommandContext(
        task_id=_uuid(raw.get("task_id")),
        session_id=_uuid(raw.get("session_id")),
        project_id=_uuid(raw.get("project_id")),
        page=str(raw.get("page") or "")[:200],
        local_hour=int(hour) if isinstance(hour, (int, float)) and 0 <= int(hour) <= 23 else None,
    )


@router.post("/command", response_model=CommandResponse)
async def command(payload: CommandRequest, request: Request, auth: CurrentAuth, db: DB) -> CommandResponse:
    result = await Assistant(db, auth).handle(payload.text, _context(payload.context), confirmed=payload.confirmed)
    if result.actions:
        await audit(db, "assistant.command", request=request, user_id=auth.user_id, org_id=auth.org_id, data={"intent": result.intent, "actions": result.actions[:5]})
    await db.commit()
    return CommandResponse(**result.as_dict())


@router.get("/briefing", response_model=dict)
async def briefing(auth: CurrentAuth, db: DB, local_hour: int | None = Query(default=None, ge=0, le=23)) -> dict:
    return await assistant_service.briefing(db, auth, local_hour)


@router.get("/capabilities", response_model=dict)
async def capabilities(auth: CurrentAuth) -> dict:
    return {
        "tools": assistant_service.TOOL_CATALOGUE,
        "pages": sorted(set(assistant_service.PAGES.values())),
        "assistant_name": assistant_service.assistant_name(auth),
        "user_name": assistant_service.honorific(auth),
    }


@router.get("/memory", response_model=list[MemoryOut])
async def list_memory(auth: CurrentAuth, db: DB) -> list[MemoryOut]:
    return [MemoryOut.model_validate(f) for f in await assistant_service.list_memory(db, auth.org_id)]


@router.post("/memory", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def add_memory(payload: MemoryIn, auth: CurrentAuth, db: DB, _: Writer) -> MemoryOut:
    fact = await assistant_service.remember(db, auth, key=payload.key, value=payload.value, category=payload.category)
    await db.commit()
    return MemoryOut.model_validate(fact)


@router.delete("/memory", status_code=status.HTTP_204_NO_CONTENT)
async def clear_memory(auth: CurrentAuth, db: DB, _: Writer) -> Response:
    await assistant_service.forget(db, auth.org_id, "everything")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/memory/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(fact_id: uuid.UUID, auth: CurrentAuth, db: DB, _: Writer) -> Response:
    fact = await db.get(MemoryFact, fact_id)
    if fact is None or fact.organization_id != auth.org_id:
        raise NotFoundError("Memory fact not found")
    await db.delete(fact)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/system", response_model=dict)
async def system(auth: CurrentAuth) -> dict:
    """Host telemetry for the SYS MONITOR panel (CPU, memory, network, disk, temperature, uptime)."""
    settings = get_settings()
    frame = dict(system_metrics(settings.resolved_data_dir))
    frame["environment"] = settings.app_env
    frame["browser_backend"] = settings.browser_backend
    return frame
