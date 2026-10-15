"""Organization-level settings, limits and encrypted secrets."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import llm_status, provider_catalog, validate_llm_base_url
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.security import decrypt_secret, encrypt_secret, try_decrypt_secret
from app.models import Organization

LIMIT_KEYS = {
    "max_task_duration_seconds": (60, 24 * 3600),
    "max_retries": (0, 10),
    "max_scenes_per_task": (1, 1000),
    "max_concurrent_tasks": (1, 20),
    "max_file_size_mb": (1, 10_000),
    "max_browser_sessions": (1, 50),
    "generation_timeout_seconds": (30, 7200),
}


def effective_limits(org: Organization | None) -> dict[str, Any]:
    settings = get_settings()
    base = {
        "max_task_duration_seconds": settings.max_task_duration_seconds,
        "max_retries": settings.max_retries_limit,
        "max_scenes_per_task": settings.max_scenes_per_task,
        "max_concurrent_tasks": settings.max_concurrent_tasks_per_worker,
        "max_file_size_mb": settings.max_generated_file_size_mb,
        "max_browser_sessions": settings.max_browser_sessions_per_org,
        "generation_timeout_seconds": settings.generation_timeout_seconds,
    }
    if org and org.limits:
        for key, value in org.limits.items():
            if key in base and isinstance(value, (int, float)):
                # Organization limits can only tighten the system limits, never exceed them.
                base[key] = min(base[key], int(value))
    return base


def llm_overrides_for(org: Organization | None) -> dict[str, Any] | None:
    if org is None:
        return None
    cfg = (org.settings or {}).get("llm") or {}
    provider = cfg.get("provider")
    if not provider or provider == "inherit":
        return None
    api_key = None
    encrypted = (org.secrets_encrypted or {}).get("llm_api_key")
    if encrypted:
        api_key = try_decrypt_secret(encrypted)
    return {
        "provider": provider,
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "api_key": api_key,
    }


def llm_settings_view(org: Organization) -> dict[str, Any]:
    cfg = (org.settings or {}).get("llm") or {}
    encrypted = (org.secrets_encrypted or {}).get("llm_api_key")
    key_plain = try_decrypt_secret(encrypted) if encrypted else None
    status = llm_status(llm_overrides_for(org))
    return {
        "provider": cfg.get("provider") or "inherit",
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        # Never return even a key prefix/suffix; only report whether an encrypted key exists.
        "api_key_masked": "••••••••" if key_plain else None,
        "has_api_key": bool(key_plain),
        "configured": status["configured"],
        "effective_provider": status["provider"],
        "status": status,
        "providers": provider_catalog(),
    }


async def update_org_settings(db: AsyncSession, org: Organization, data: dict[str, Any]) -> Organization:
    if data.get("name"):
        org.name = data["name"]
    settings_blob = dict(org.settings or {})
    if data.get("settings") is not None:
        allowed = {
            k: v for k, v in data["settings"].items() if k in ("notifications", "defaults", "ui", "platforms")
        }
        settings_blob.update(allowed)
    llm_cfg = dict(settings_blob.get("llm") or {})
    previous_provider = llm_cfg.get("provider") or "inherit"
    next_provider = data.get("llm_provider")
    provider_changed = next_provider is not None and next_provider != previous_provider
    if next_provider is not None:
        llm_cfg["provider"] = next_provider
        if provider_changed:
            # Avoid accidentally carrying a model ID or endpoint from a different provider.
            if "llm_model" not in data:
                llm_cfg["model"] = None
            if "llm_base_url" not in data:
                llm_cfg["base_url"] = None
    if data.get("llm_model") is not None:
        llm_cfg["model"] = data["llm_model"].strip() or None
    if data.get("llm_base_url") is not None:
        url = data["llm_base_url"].strip()
        if url:
            try:
                url = validate_llm_base_url(url, provider=str(next_provider or llm_cfg.get("provider") or ""))
            except ValueError as exc:
                raise ValidationFailed(str(exc)) from exc
        llm_cfg["base_url"] = url or None
    new_api_key = str(data.get("llm_api_key") or "").strip()
    if new_api_key and llm_cfg.get("provider") in (None, "", "inherit", "none"):
        raise ValidationFailed("Select an organization AI provider before saving an API key")
    settings_blob["llm"] = llm_cfg
    org.settings = settings_blob
    secrets = dict(org.secrets_encrypted or {})
    if data.get("clear_llm_api_key") or (provider_changed and not new_api_key):
        secrets.pop("llm_api_key", None)
    elif new_api_key:
        secrets["llm_api_key"] = encrypt_secret(new_api_key)
    org.secrets_encrypted = secrets
    if data.get("limits") is not None:
        limits = dict(org.limits or {})
        for key, value in data["limits"].items():
            if key not in LIMIT_KEYS:
                continue
            if value is None:
                limits.pop(key, None)
                continue
            lo, hi = LIMIT_KEYS[key]
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValidationFailed(f"Limit {key} must be a number") from exc
            if number < lo or number > hi:
                raise ValidationFailed(f"Limit {key} must be between {lo} and {hi}")
            limits[key] = number
        org.limits = limits
    await db.flush()
    return org


def decrypt_org_secret(org: Organization, key: str) -> str | None:
    encrypted = (org.secrets_encrypted or {}).get(key)
    return decrypt_secret(encrypted) if encrypted else None
