"""Organization settings, members and limits."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.agent.llm import LLMMessage, LLMUnavailable, build_llm_client, llm_status
from app.core.config import get_settings
from app.core.deps import DB, CurrentAuth, require_role
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models import OrganizationMember, OrgRole, User
from app.notifications import configured_channels
from app.realtime.bus import get_bus
from app.schemas import OrganizationOut, OrgSettingsOut, OrgSettingsUpdate
from app.services.audit import audit
from app.services.org_settings import (
    effective_limits,
    llm_overrides_for,
    llm_settings_view,
    update_org_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])
Admin = Annotated[object, Depends(require_role(OrgRole.admin.value))]


def _view(auth) -> OrgSettingsOut:  # noqa: ANN001
    settings = get_settings()
    org = auth.organization
    llm = build_llm_client(llm_overrides_for(org))
    return OrgSettingsOut(
        organization=OrganizationOut.model_validate(org),
        llm=llm_settings_view(org),
        limits=dict(org.limits or {}),
        effective_limits=effective_limits(org),
        system={
            "environment": settings.app_env,
            "bus": get_bus().kind,
            "embedded_worker": settings.worker_is_embedded,
            "llm_active": llm is not None,
            "llm_model": getattr(llm, "model", None),
            "agent_use_vision": settings.agent_use_vision,
            "notification_channels": ["in_app", "browser", *configured_channels()],
            "mock_platform_url": settings.effective_mock_platform_url
            if settings.app_env != "production"
            else None,
            "system_limits": {
                "max_task_duration_seconds": settings.max_task_duration_seconds,
                "max_retries_limit": settings.max_retries_limit,
                "max_scenes_per_task": settings.max_scenes_per_task,
                "max_browser_sessions_per_org": settings.max_browser_sessions_per_org,
                "max_generated_file_size_mb": settings.max_generated_file_size_mb,
                "max_concurrent_tasks_per_worker": settings.max_concurrent_tasks_per_worker,
            },
        },
    )


@router.get("", response_model=OrgSettingsOut)
async def get_org_settings(auth: CurrentAuth) -> OrgSettingsOut:
    return _view(auth)


@router.patch("", response_model=OrgSettingsOut)
async def patch_org_settings(
    payload: OrgSettingsUpdate, request: Request, auth: CurrentAuth, db: DB, _: Admin
) -> OrgSettingsOut:
    await update_org_settings(db, auth.organization, payload.model_dump(exclude_unset=True))
    await audit(
        db,
        "org.settings_updated",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        data={k: ("***" if "key" in k else v) for k, v in payload.model_dump(exclude_unset=True).items()},
    )
    await db.commit()
    await db.refresh(auth.organization)
    return _view(auth)


class MemberIn(BaseModel):
    email: EmailStr
    role: str = Field(default=OrgRole.member.value)


class MemberRoleIn(BaseModel):
    role: str


@router.post("/llm/test", response_model=dict)
async def test_llm_connection(request: Request, auth: CurrentAuth, db: DB, _: Admin) -> dict:
    """Test the saved organisation provider without returning prompts, output, or credentials."""
    overrides = llm_overrides_for(auth.organization)
    config_status = llm_status(overrides)
    provider = str(config_status.get("provider") or "none")
    model = config_status.get("model")
    if not config_status.get("configured"):
        result = {
            "ok": False,
            "provider": provider,
            "model": model,
            "latency_ms": 0,
            "message": str(config_status.get("message") or "AI provider is not configured."),
        }
    else:
        client = build_llm_client(overrides)
        started = time.perf_counter()
        try:
            if client is None:
                raise LLMUnavailable("Provider is not configured.")
            response = await client.complete(
                [LLMMessage("user", "Reply with the single word OK.")],
                max_tokens=16,
                temperature=0.0,
            )
            ok = bool(response.text.strip())
            message = (
                "Connection successful; the model returned a response."
                if ok
                else "The provider returned an empty response."
            )
        except LLMUnavailable as exc:
            ok = False
            message = str(exc)
        except Exception as exc:  # keep unexpected library/network details out of the response
            logger.warning("LLM connection test failed: provider=%s error=%s", provider, type(exc).__name__)
            ok = False
            message = "The provider request failed. Check the server logs and provider configuration."
        result = {
            "ok": ok,
            "provider": provider,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": message,
        }
    await audit(
        db,
        "org.llm_connection_test",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        data={"provider": provider, "model": model, "ok": result["ok"]},
    )
    await db.commit()
    return result


@router.get("/members", response_model=list[dict])
async def list_members(auth: CurrentAuth, db: DB) -> list[dict]:
    rows = (
        await db.execute(
            select(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .where(OrganizationMember.organization_id == auth.org_id)
            .order_by(OrganizationMember.created_at)
        )
    ).all()
    return [
        {
            "id": str(m.id),
            "user_id": str(u.id),
            "email": u.email,
            "full_name": u.full_name,
            "role": m.role,
            "joined_at": m.created_at,
            "is_you": u.id == auth.user_id,
        }
        for m, u in rows
    ]


@router.post("/members", response_model=dict, status_code=status.HTTP_201_CREATED)
async def add_member(payload: MemberIn, request: Request, auth: CurrentAuth, db: DB, _: Admin) -> dict:
    if payload.role not in (r.value for r in OrgRole) or payload.role == OrgRole.owner.value:
        raise ValidationFailed("Role must be admin, member or viewer")
    user = (await db.execute(select(User).where(User.email == payload.email.lower()))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("No account with that e-mail exists yet; ask them to register first")
    existing = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == auth.org_id, OrganizationMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise ConflictError("Already a member")
    member = OrganizationMember(organization_id=auth.org_id, user_id=user.id, role=payload.role)
    db.add(member)
    await audit(
        db,
        "org.member_added",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="user",
        resource_id=user.id,
        data={"role": payload.role},
    )
    await db.commit()
    return {"id": str(member.id), "user_id": str(user.id), "email": user.email, "role": member.role}


@router.patch("/members/{member_id}", response_model=dict)
async def change_role(
    member_id: uuid.UUID, payload: MemberRoleIn, request: Request, auth: CurrentAuth, db: DB, _: Admin
) -> dict:
    member = await db.get(OrganizationMember, member_id)
    if member is None or member.organization_id != auth.org_id:
        raise NotFoundError("Member not found")
    if member.role == OrgRole.owner.value:
        raise ConflictError("The owner's role cannot be changed")
    if payload.role not in (OrgRole.admin.value, OrgRole.member.value, OrgRole.viewer.value):
        raise ValidationFailed("Role must be admin, member or viewer")
    member.role = payload.role
    await audit(
        db,
        "org.member_role_changed",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="user",
        resource_id=member.user_id,
        data={"role": payload.role},
    )
    await db.commit()
    return {"id": str(member.id), "role": member.role}


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB, _: Admin
) -> Response:
    member = await db.get(OrganizationMember, member_id)
    if member is None or member.organization_id != auth.org_id:
        raise NotFoundError("Member not found")
    if member.role == OrgRole.owner.value:
        raise ConflictError("The owner cannot be removed")
    await audit(
        db,
        "org.member_removed",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="user",
        resource_id=member.user_id,
    )
    await db.delete(member)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
