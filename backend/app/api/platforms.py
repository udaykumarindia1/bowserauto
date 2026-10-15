"""Platform catalogue (adapter registry + per-org enable flags)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.deps import DB, CurrentAuth, require_role
from app.core.errors import NotFoundError
from app.models import OrgRole
from app.platforms.registry import get_adapter_class, list_platform_info
from app.schemas import PlatformOut
from app.services.org_settings import update_org_settings

router = APIRouter(prefix="/platforms", tags=["platforms"])


def _enabled_map(org) -> dict[str, bool]:  # noqa: ANN001
    return dict(((org.settings or {}).get("platforms") or {}).get("enabled", {}))


@router.get("", response_model=list[PlatformOut])
async def list_platforms(auth: CurrentAuth) -> list[PlatformOut]:
    enabled = _enabled_map(auth.organization)
    out = []
    for info in list_platform_info():
        out.append(
            PlatformOut(**{**info, "adapter_available": True, "enabled": enabled.get(info["key"], True)})
        )
    return out


@router.get("/{key}", response_model=dict)
async def platform_detail(key: str, auth: CurrentAuth) -> dict:
    cls = get_adapter_class(key)
    if cls is None:
        raise NotFoundError("Unknown platform")
    info = cls.info()
    adapter = cls(None)
    return {
        **info,
        "adapter_available": True,
        "enabled": _enabled_map(auth.organization).get(key, True),
        "start_url": adapter.start_url(),
        "selectors": {k: v for k, v in cls.selectors.items()},
        "login_hint": await adapter.login_hint(),
    }


class PlatformEnabledIn(BaseModel):
    enabled: bool


@router.post("/{key}/enabled", response_model=PlatformOut)
async def set_enabled(
    key: str,
    payload: PlatformEnabledIn,
    auth: CurrentAuth,
    db: DB,
    _: Annotated[object, Depends(require_role(OrgRole.admin.value))],
) -> PlatformOut:
    enabled = payload.enabled
    cls = get_adapter_class(key)
    if cls is None:
        raise NotFoundError("Unknown platform")
    settings = dict(auth.organization.settings or {})
    platforms = dict(settings.get("platforms") or {})
    flags = dict(platforms.get("enabled") or {})
    flags[key] = enabled
    platforms["enabled"] = flags
    settings["platforms"] = platforms
    await update_org_settings(db, auth.organization, {"settings": settings})
    await db.commit()
    return PlatformOut(**{**cls.info(), "adapter_available": True, "enabled": enabled})
