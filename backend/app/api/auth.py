"""Authentication endpoints: register, login, logout, session info, password, org switching."""

from __future__ import annotations

import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

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
from app.core.errors import ConflictError, LimitExceededError, NotFoundError, UnauthorizedError
from app.core.security import csrf_token_for, hash_token
from app.models import AuthSession, DevicePairing, User
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


class DevicePairingClaim(BaseModel):
    pairing_id: uuid.UUID
    code: str = Field(min_length=32, max_length=128)
    device_name: str = Field(default="Phone browser", max_length=100)


class DevicePairingPoll(BaseModel):
    pairing_id: uuid.UUID
    code: str = Field(min_length=32, max_length=128)


def _pairing_expired(pairing: DevicePairing, now: datetime) -> bool:
    expiry = pairing.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry <= now


def _pairing_status(pairing: DevicePairing, now: datetime) -> str:
    if pairing.status in {"pending", "claimed", "approved"} and _pairing_expired(pairing, now):
        return "expired"
    return pairing.status


def _pairing_public_state(pairing: DevicePairing, now: datetime) -> dict:
    return {
        "id": str(pairing.id),
        "status": _pairing_status(pairing, now),
        "expires_at": pairing.expires_at,
        "device_name": pairing.device_name,
        "user_agent": pairing.user_agent,
        "claimed_at": pairing.claimed_at,
        "approved_at": pairing.approved_at,
    }


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


@router.post(
    "/device-pairings",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
)
async def create_device_pairing(request: Request, auth: CurrentAuth, db: DB) -> dict:
    """Create a short-lived QR pairing code; the unhashed code is returned only once."""
    now = datetime.now(UTC)
    active = (
        await db.execute(
            select(func.count(DevicePairing.id)).where(
                DevicePairing.user_id == auth.user_id,
                DevicePairing.status.in_(["pending", "claimed", "approved"]),
                DevicePairing.expires_at > now,
            )
        )
    ).scalar_one()
    if active >= 3:
        raise LimitExceededError("You already have three active phone pairing requests")

    code = secrets.token_urlsafe(32)
    pairing = DevicePairing(
        user_id=auth.user_id,
        organization_id=auth.org_id,
        code_hash=hash_token(code),
        status="pending",
        expires_at=now + timedelta(minutes=2),
    )
    db.add(pairing)
    await db.flush()
    await audit(
        db,
        "auth.device_pairing_created",
        request=request,
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="device_pairing",
        resource_id=pairing.id,
    )
    await db.commit()
    return {"id": str(pairing.id), "code": code, "expires_at": pairing.expires_at}


@router.post(
    "/device-pairings/claim",
    dependencies=[Depends(rate_limit_auth)],
)
async def claim_device_pairing(payload: DevicePairingClaim, request: Request, db: DB) -> dict:
    """Mark the phone as waiting for confirmation on the already-authenticated device."""
    pairing = await db.get(DevicePairing, payload.pairing_id)
    supplied_hash = hash_token(payload.code)
    if pairing is None or not hmac.compare_digest(pairing.code_hash, supplied_hash):
        raise UnauthorizedError("Pairing code is invalid or expired")
    now = datetime.now(UTC)
    if _pairing_expired(pairing, now):
        return {"status": "expired"}

    if pairing.status == "pending":
        device_name = payload.device_name.strip()[:100] or "Phone browser"
        result = await db.execute(
            update(DevicePairing)
            .execution_options(synchronize_session=False)
            .where(
                DevicePairing.id == pairing.id,
                DevicePairing.code_hash == supplied_hash,
                DevicePairing.status == "pending",
                DevicePairing.expires_at > now,
            )
            .values(
                status="claimed",
                device_name=device_name,
                user_agent=request.headers.get("user-agent", "")[:400],
                claim_ip=client_ip(request),
                claimed_at=now,
            )
        )
        if result.rowcount == 1:
            await db.refresh(pairing)
            await audit(
                db,
                "auth.device_pairing_claimed",
                request=request,
                user_id=pairing.user_id,
                org_id=pairing.organization_id,
                resource_type="device_pairing",
                resource_id=pairing.id,
                data={"device_name": pairing.device_name},
            )
            await db.commit()
        else:
            await db.refresh(pairing)

    current_status = _pairing_status(pairing, datetime.now(UTC))
    if current_status not in {"claimed", "approved", "completed"}:
        return {"status": current_status}
    return {"status": current_status}


@router.post(
    "/device-pairings/poll",
    dependencies=[Depends(rate_limit_auth)],
)
async def poll_device_pairing(
    payload: DevicePairingPoll, request: Request, response: Response, db: DB
) -> dict:
    """Exchange an approved QR code for a normal HttpOnly app session exactly once."""
    pairing = await db.get(DevicePairing, payload.pairing_id)
    supplied_hash = hash_token(payload.code)
    if pairing is None or not hmac.compare_digest(pairing.code_hash, supplied_hash):
        raise UnauthorizedError("Pairing code is invalid or expired")

    now = datetime.now(UTC)
    current_status = _pairing_status(pairing, now)
    if current_status != "approved":
        return {"status": current_status, "authenticated": False}

    user = await db.get(User, pairing.user_id)
    if user is None or not user.is_active:
        await db.execute(
            update(DevicePairing)
            .where(DevicePairing.id == pairing.id, DevicePairing.status == "approved")
            .values(status="rejected")
        )
        await db.commit()
        return {"status": "rejected", "authenticated": False}

    # Compare-and-set works on PostgreSQL and SQLite: only one phone polling the same QR can
    # receive a new login session, even if multiple requests race after desktop approval.
    claimed = await db.execute(
        update(DevicePairing)
        .execution_options(synchronize_session=False)
        .where(
            DevicePairing.id == pairing.id,
            DevicePairing.code_hash == supplied_hash,
            DevicePairing.status == "approved",
            DevicePairing.completed_at.is_(None),
            DevicePairing.expires_at > now,
        )
        .values(status="completed", completed_at=now)
    )
    if claimed.rowcount != 1:
        await db.refresh(pairing)
        return {"status": _pairing_status(pairing, datetime.now(UTC)), "authenticated": False}

    auth_session, token = await auth_service.create_auth_session(
        db,
        user,
        user_agent=pairing.user_agent or request.headers.get("user-agent", ""),
        ip_address=client_ip(request),
    )
    await audit(
        db,
        "auth.device_pairing_completed",
        request=request,
        user_id=pairing.user_id,
        org_id=pairing.organization_id,
        resource_type="device_pairing",
        resource_id=pairing.id,
    )
    await db.commit()
    csrf = set_auth_cookies(response, token, auth_session.token_hash)
    return {"status": "completed", "authenticated": True, "csrf_token": csrf}


@router.get("/device-pairings/{pairing_id}")
async def get_device_pairing(pairing_id: uuid.UUID, auth: CurrentAuth, db: DB) -> dict:
    pairing = await db.get(DevicePairing, pairing_id)
    if pairing is None or pairing.user_id != auth.user_id:
        raise NotFoundError("Device pairing not found")
    return _pairing_public_state(pairing, datetime.now(UTC))


@router.post("/device-pairings/{pairing_id}/approve")
async def approve_device_pairing(pairing_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB) -> dict:
    pairing = await db.get(DevicePairing, pairing_id)
    if pairing is None or pairing.user_id != auth.user_id:
        raise NotFoundError("Device pairing not found")
    now = datetime.now(UTC)
    if _pairing_expired(pairing, now):
        raise ConflictError("This phone pairing request has expired")
    result = await db.execute(
        update(DevicePairing)
        .execution_options(synchronize_session=False)
        .where(
            DevicePairing.id == pairing.id,
            DevicePairing.user_id == auth.user_id,
            DevicePairing.status == "claimed",
            DevicePairing.expires_at > now,
        )
        .values(status="approved", approved_at=now)
    )
    if result.rowcount != 1:
        raise ConflictError("This pairing request is no longer waiting for approval")
    await db.refresh(pairing)
    await audit(
        db,
        "auth.device_pairing_approved",
        request=request,
        user_id=auth.user_id,
        org_id=pairing.organization_id,
        resource_type="device_pairing",
        resource_id=pairing.id,
    )
    await db.commit()
    return _pairing_public_state(pairing, datetime.now(UTC))


@router.post("/device-pairings/{pairing_id}/reject")
async def reject_device_pairing(pairing_id: uuid.UUID, request: Request, auth: CurrentAuth, db: DB) -> dict:
    pairing = await db.get(DevicePairing, pairing_id)
    if pairing is None or pairing.user_id != auth.user_id:
        raise NotFoundError("Device pairing not found")
    now = datetime.now(UTC)
    result = await db.execute(
        update(DevicePairing)
        .where(
            DevicePairing.id == pairing.id,
            DevicePairing.user_id == auth.user_id,
            DevicePairing.status.in_(["pending", "claimed", "approved"]),
            DevicePairing.completed_at.is_(None),
        )
        .values(status="rejected")
    )
    if result.rowcount != 1:
        await db.refresh(pairing)
        return _pairing_public_state(pairing, now)
    await db.refresh(pairing)
    await audit(
        db,
        "auth.device_pairing_rejected",
        request=request,
        user_id=auth.user_id,
        org_id=pairing.organization_id,
        resource_type="device_pairing",
        resource_id=pairing.id,
    )
    await db.commit()
    return _pairing_public_state(pairing, now)


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
