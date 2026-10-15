"""Audit logging for security-relevant actions."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip
from app.core.logging import redact
from app.models import AuditLog


async def audit(
    db: AsyncSession,
    action: str,
    *,
    request: Request | None = None,
    user_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    resource_type: str = "",
    resource_id: Any = "",
    data: dict[str, Any] | None = None,
) -> None:
    entry = AuditLog(
        organization_id=org_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        ip_address=client_ip(request) if request else "",
        user_agent=(request.headers.get("user-agent", "")[:400] if request else ""),
        data=redact(data or {}),
    )
    db.add(entry)
