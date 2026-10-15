"""Authentication & account services."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, PermissionDeniedError, UnauthorizedError, ValidationFailed
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    session_expiry,
    validate_password_strength,
    verify_password,
)
from app.models import AuthSession, Organization, OrganizationMember, OrgRole, User
from app.services.templates import ensure_builtin_templates


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "org"


async def _unique_slug(db: AsyncSession, base: str) -> str:
    slug = _slugify(base)
    candidate = slug
    for _ in range(50):
        exists = (
            await db.execute(select(Organization.id).where(Organization.slug == candidate))
        ).scalar_one_or_none()
        if exists is None:
            return candidate
        candidate = f"{slug}-{secrets.token_hex(2)}"
    return f"{slug}-{secrets.token_hex(4)}"


async def register_user(
    db: AsyncSession, *, email: str, password: str, full_name: str = "", organization_name: str | None = None
) -> tuple[User, Organization]:
    settings = get_settings()
    email = email.strip().lower()
    if not settings.allow_registration:
        existing_users = (await db.execute(select(User.id).limit(1))).scalar_one_or_none()
        if existing_users is not None:
            raise PermissionDeniedError("Registration is disabled")
    weakness = validate_password_strength(password)
    if weakness:
        raise ValidationFailed(weakness)
    exists = (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
    if exists is not None:
        raise ConflictError("An account with this email already exists")
    first_user = (await db.execute(select(User.id).limit(1))).scalar_one_or_none() is None
    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        is_superuser=first_user,
    )
    db.add(user)
    await db.flush()
    org_name = (organization_name or "").strip() or (
        f"{full_name.strip()}'s Workspace" if full_name.strip() else f"{email.split('@')[0]}'s Workspace"
    )
    organization = Organization(name=org_name[:200], slug=await _unique_slug(db, org_name))
    db.add(organization)
    await db.flush()
    db.add(OrganizationMember(organization_id=organization.id, user_id=user.id, role=OrgRole.owner.value))
    user.current_organization_id = organization.id
    await ensure_builtin_templates(db)
    await db.flush()
    return user, organization


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    email = email.strip().lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        # Constant-ish time: hashing happens inside verify_password in both branches when the user exists;
        # for unknown users run a dummy verify to avoid trivially leaking account existence via timing.
        if user is None:
            verify_password(password, hash_password("dummy-password-for-timing"))
        raise UnauthorizedError("Invalid email or password")
    if not user.is_active:
        raise PermissionDeniedError("This account is disabled")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    return user


async def create_auth_session(
    db: AsyncSession, user: User, *, user_agent: str = "", ip_address: str = ""
) -> tuple[AuthSession, str]:
    token = generate_session_token()
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=session_expiry(),
        user_agent=user_agent[:400],
        ip_address=ip_address[:64],
        last_seen_at=datetime.now(UTC),
    )
    db.add(auth_session)
    await db.flush()
    return auth_session, token


async def revoke_auth_session(db: AsyncSession, auth_session: AuthSession) -> None:
    auth_session.revoked_at = datetime.now(UTC)


async def revoke_all_sessions(
    db: AsyncSession, user_id: uuid.UUID, except_id: uuid.UUID | None = None
) -> int:
    sessions = (
        (
            await db.execute(
                select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    count = 0
    now = datetime.now(UTC)
    for s in sessions:
        if except_id and s.id == except_id:
            continue
        s.revoked_at = now
        count += 1
    return count


async def change_password(db: AsyncSession, user: User, *, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise UnauthorizedError("Current password is incorrect")
    weakness = validate_password_strength(new_password)
    if weakness:
        raise ValidationFailed(weakness)
    user.password_hash = hash_password(new_password)


async def list_memberships(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[Organization, str]]:
    rows = (
        await db.execute(
            select(Organization, OrganizationMember.role)
            .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
            .where(OrganizationMember.user_id == user_id)
            .order_by(Organization.created_at)
        )
    ).all()
    return [(org, role) for org, role in rows]


async def switch_organization(db: AsyncSession, user: User, organization_id: uuid.UUID) -> Organization:
    membership = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user.id, OrganizationMember.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise PermissionDeniedError("You are not a member of that organization")
    user.current_organization_id = organization_id
    org = await db.get(Organization, organization_id)
    assert org is not None
    return org
