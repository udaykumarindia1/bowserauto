"""Authentication endpoints: register, login, logout, session info, password, org switching."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import (
    DB,
    AuthContext,
    CurrentAuth,
    _load_auth,
    client_ip,
    get_optional_auth,
    rate_limit_auth,
)
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.security import csrf_token_for
from app.models import AuthSession, User
from app.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    MembershipOut,
    OrganizationOut,
    RegisterRequest,
    UserOut,
    UserUpdate,
)
from app.services import auth_service
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def set_auth_cookies(response: Response, token: str, token_hash: str) -> str:
    settings = get_settings()
    max_age = settings.session_ttl_hours * 3600
    csrf = csrf_token_for(token_hash)
    common: dict = {"max_age": max_age, "secure": settings.cookies_secure, "samesite": "lax", "path": "/"}
    if settings.cookie_domain:
        common["domain"] = settings.cookie_domain
    response.set_cookie(settings.cookie_name, token, httponly=True, **common)
    # The CSRF cookie is intentionally readable by the frontend (double-submit pattern).
    response.set_cookie(settings.csrf_cookie_name, csrf, httponly=False, **common)
    return csrf


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (settings.cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, path="/", domain=settings.cookie_domain)


async def _auth_response(db, auth: AuthContext, csrf: str) -> AuthResponse:  # noqa: ANN001
    memberships = await auth_service.list_memberships(db, auth.user_id)
    return AuthResponse(
        user=UserOut.model_validate(auth.user),
        organization=OrganizationOut.model_validate(auth.organization),
        role=auth.role,
        csrf_token=csrf,
        organizations=[
            MembershipOut(organization=OrganizationOut.model_validate(org), role=role)
            for org, role in memberships
        ],
    )


@router.get("/status")
async def auth_status(db: DB) -> dict:
    """Public: tells the login page whether registration is open."""
    settings = get_settings()
    count = (await db.execute(select(func.count(User.id)))).scalar_one()
    return {
        "registration_open": settings.allow_registration or count == 0,
        "has_users": count > 0,
        "app_name": settings.app_name,
        "environment": settings.app_env,
    }


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
)
async def register(payload: RegisterRequest, request: Request, response: Response, db: DB) -> AuthResponse:
    user, org = await auth_service.register_user(
        db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        organization_name=payload.organization_name,
    )
    auth_session, token = await auth_service.create_auth_session(
        db, user, user_agent=request.headers.get("user-agent", ""), ip_address=client_ip(request)
    )
    await audit(
        db,
        "auth.register",
        request=request,
        user_id=user.id,
        org_id=org.id,
        resource_type="user",
        resource_id=user.id,
    )
    await db.commit()
    csrf = set_auth_cookies(response, token, auth_session.token_hash)
    auth = await _load_auth(db, token, touch=False)
    assert auth is not None
    return await _auth_response(db, auth, csrf)


@router.post("/login", response_model=AuthResponse, dependencies=[Depends(rate_limit_auth)])
async def login(payload: LoginRequest, request: Request, response: Response, db: DB) -> AuthResponse:
    try:
        user = await auth_service.authenticate(db, email=payload.email, password=payload.password)
    except UnauthorizedError:
        await audit(db, "auth.login_failed", request=request, data={"email": payload.email})
        await db.commit()
        raise
    auth_session, token = await auth_service.create_auth_session(
        db, user, user_agent=request.headers.get("user-agent", ""), ip_address=client_ip(request)
    )
    await audit(
        db,
        "auth.login",
        request=request,
        user_id=user.id,
        org_id=user.current_organization_id,
        resource_type="user",
        resource_id=user.id,
    )
    await db.commit()
    csrf = set_auth_cookies(response, token, auth_session.token_hash)
    auth = await _load_auth(db, token, touch=False)
    assert auth is not None
    return await _auth_response(db, auth, csrf)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, db: DB, auth: Annotated[AuthContext | None, Depends(get_optional_auth)]
) -> Response:
    if auth is not None:
        await auth_service.revoke_auth_session(db, auth.auth_session)
        await audit(db, "auth.logout", request=request, user_id=auth.user_id, org_id=auth.org_id)
        await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response)
    return response


@router.get("/me", response_model=AuthResponse)
async def me(auth: CurrentAuth, db: DB) -> AuthResponse:
    return await _auth_response(db, auth, csrf_token_for(auth.auth_session.token_hash))


@router.patch("/me", response_model=UserOut)
async def update_me(payload: UserUpdate, auth: CurrentAuth, db: DB) -> UserOut:
    if payload.full_name is not None:
        auth.user.full_name = payload.full_name
    if payload.preferences is not None:
        auth.user.preferences = {**(auth.user.preferences or {}), **payload.preferences}
    await db.commit()
    return UserOut.model_validate(auth.user)


@router.post(
    "/change-password", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit_auth)]
)
async def change_password(
    payload: ChangePasswordRequest, request: Request, auth: CurrentAuth, db: DB
) -> Response:
    await auth_service.change_password(
        db, auth.user, current_password=payload.current_password, new_password=payload.new_password
    )
    await auth_service.revoke_all_sessions(db, auth.user_id, except_id=auth.auth_session.id)
    await audit(db, "auth.password_changed", request=request, user_id=auth.user_id, org_id=auth.org_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/switch-organization/{organization_id}", response_model=AuthResponse)
async def switch_organization(
    organization_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB
) -> AuthResponse:
    await auth_service.switch_organization(db, auth.user, organization_id)
    await audit(db, "auth.switch_org", request=request, user_id=auth.user_id, org_id=organization_id)
    await db.commit()
    settings = get_settings()
    token = request.cookies.get(settings.cookie_name)
    if not token:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
    fresh = await _load_auth(db, token, touch=False)
    assert fresh is not None
    return await _auth_response(db, fresh, csrf_token_for(fresh.auth_session.token_hash))


@router.get("/sessions")
async def list_login_sessions(auth: CurrentAuth, db: DB) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(AuthSession)
                .where(AuthSession.user_id == auth.user_id, AuthSession.revoked_at.is_(None))
                .order_by(AuthSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(s.id),
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "expires_at": s.expires_at,
            "user_agent": s.user_agent,
            "ip_address": s.ip_address,
            "current": s.id == auth.auth_session.id,
        }
        for s in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_login_session(
    session_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB
) -> Response:
    row = await db.get(AuthSession, session_id)
    if row is None or row.user_id != auth.user_id:
        raise NotFoundError("Session not found")
    await auth_service.revoke_auth_session(db, row)
    await audit(
        db,
        "auth.session_revoked",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="auth_session",
        resource_id=session_id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
