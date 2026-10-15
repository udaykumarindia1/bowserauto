"""FastAPI dependencies: authentication, organization scoping, CSRF and rate limiting."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import PermissionDeniedError, UnauthorizedError
from app.core.ratelimit import get_rate_limiter
from app.core.security import hash_token, verify_csrf_token
from app.models import AuthSession, Organization, OrganizationMember, OrgRole, User

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ROLE_ORDER = {
    OrgRole.viewer.value: 0,
    OrgRole.member.value: 1,
    OrgRole.admin.value: 2,
    OrgRole.owner.value: 3,
}


@dataclass
class AuthContext:
    user: User
    organization: Organization
    role: str
    auth_session: AuthSession

    @property
    def org_id(self) -> uuid.UUID:
        return self.organization.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def require_role(self, minimum: str) -> None:
        if ROLE_ORDER.get(self.role, -1) < ROLE_ORDER.get(minimum, 99):
            raise PermissionDeniedError(f"This action requires the {minimum} role")

    @property
    def can_write(self) -> bool:
        return ROLE_ORDER.get(self.role, -1) >= ROLE_ORDER[OrgRole.member.value]


def client_ip(request: Request | WebSocket) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


async def _load_auth(db: AsyncSession, token: str | None, *, touch: bool = True) -> AuthContext | None:
    if not token:
        return None
    token_hash = hash_token(token)
    now = datetime.now(UTC)
    result = await db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash))
    auth_session = result.scalar_one_or_none()
    if auth_session is None or auth_session.revoked_at is not None:
        return None
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        return None
    user = await db.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        return None
    org_id = user.current_organization_id
    membership = None
    if org_id is not None:
        membership = (
            await db.execute(
                select(OrganizationMember).where(
                    OrganizationMember.user_id == user.id, OrganizationMember.organization_id == org_id
                )
            )
        ).scalar_one_or_none()
    if membership is None:
        membership = (
            (
                await db.execute(
                    select(OrganizationMember)
                    .where(OrganizationMember.user_id == user.id)
                    .order_by(OrganizationMember.created_at)
                )
            )
            .scalars()
            .first()
        )
        if membership is None:
            return None
        user.current_organization_id = membership.organization_id
    organization = await db.get(Organization, membership.organization_id)
    if organization is None:
        return None
    if touch and (
        auth_session.last_seen_at is None or (now - _aware(auth_session.last_seen_at)) > timedelta(minutes=5)
    ):
        auth_session.last_seen_at = now
        await db.commit()
    return AuthContext(user=user, organization=organization, role=membership.role, auth_session=auth_session)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def get_optional_auth(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> AuthContext | None:
    settings = get_settings()
    token = request.cookies.get(settings.cookie_name)
    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    return await _load_auth(db, token)


async def get_current_auth(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> AuthContext:
    auth = await get_optional_auth(request, db)
    if auth is None:
        raise UnauthorizedError("Authentication required")
    if request.method not in SAFE_METHODS:
        _enforce_csrf(request, auth)
    request.state.auth = auth
    return auth


def _enforce_csrf(request: Request, auth: AuthContext) -> None:
    """Double-submit CSRF check for cookie-authenticated mutating requests."""
    settings = get_settings()
    # Requests authenticated via Authorization header are not vulnerable to CSRF.
    if request.headers.get("authorization", "").lower().startswith("bearer ") and not request.cookies.get(
        settings.cookie_name
    ):
        return
    provided = request.headers.get("x-csrf-token")
    if not verify_csrf_token(auth.auth_session.token_hash, provided):
        raise PermissionDeniedError("CSRF token missing or invalid", code="csrf_failed")
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin, request, settings):
        raise PermissionDeniedError("Cross-origin request blocked", code="origin_blocked")


def _origin_allowed(origin: str, request: Request | WebSocket, settings: Settings) -> bool:
    origin = origin.rstrip("/")
    if origin in settings.allowed_origins:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        proto = request.headers.get("x-forwarded-proto") or (
            "https" if origin.startswith("https") else "http"
        )
        if origin == f"{proto}://{host}" or origin == f"https://{host}" or origin == f"http://{host}":
            return True
    if settings.app_env in ("development", "test") and origin.startswith(
        ("http://localhost", "http://127.0.0.1")
    ):
        return True
    return False


async def get_ws_auth(websocket: WebSocket, db: AsyncSession) -> AuthContext | None:
    """Authenticate a WebSocket handshake with the session cookie and validate its Origin."""
    settings = get_settings()
    origin = websocket.headers.get("origin")
    if origin and not _origin_allowed(origin, websocket, settings):
        return None
    token = websocket.cookies.get(settings.cookie_name)
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    return await _load_auth(db, token, touch=False)


def require_role(minimum: str):
    async def dependency(auth: Annotated[AuthContext, Depends(get_current_auth)]) -> AuthContext:
        auth.require_role(minimum)
        return auth

    return dependency


async def rate_limit_general(request: Request) -> None:
    settings = get_settings()
    allowed, retry_after = await get_rate_limiter().hit(
        f"general:{client_ip(request)}", settings.rate_limit_per_minute
    )
    if not allowed:
        from app.core.errors import LimitExceededError

        raise LimitExceededError("Too many requests", details={"retry_after": retry_after})


async def rate_limit_auth(request: Request) -> None:
    settings = get_settings()
    allowed, retry_after = await get_rate_limiter().hit(
        f"auth:{client_ip(request)}", settings.auth_rate_limit_per_minute
    )
    if not allowed:
        from app.core.errors import LimitExceededError

        raise LimitExceededError(
            "Too many authentication attempts, slow down", details={"retry_after": retry_after}
        )


CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]
DB = Annotated[AsyncSession, Depends(get_db)]
WriterAuth = Annotated[AuthContext, Depends(require_role(OrgRole.member.value))]
AdminAuth = Annotated[AuthContext, Depends(require_role(OrgRole.admin.value))]
