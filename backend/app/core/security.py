"""Security primitives: password hashing, session tokens, CSRF, secret encryption, signed URLs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_password_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


# ----------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except Exception:  # pragma: no cover - defensive
        return False


def validate_password_strength(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters long"
    if password.lower() == password or password.upper() == password:
        return "Password must mix upper and lower case letters"
    if not any(ch.isdigit() for ch in password):
        return "Password must contain at least one digit"
    return None


# ------------------------------------------------------------------------- session tokens
def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=get_settings().session_ttl_hours)


# ------------------------------------------------------------------------------- CSRF
def csrf_token_for(token_hash: str) -> str:
    """Derive a CSRF token bound to the current login session (double submit cookie pattern)."""
    mac = hmac.new(get_settings().secret_key.encode(), f"csrf:{token_hash}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def verify_csrf_token(token_hash: str, provided: str | None) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(csrf_token_for(token_hash), provided)


# --------------------------------------------------------------------- secret encryption
def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key)


def encrypt_secret(plaintext: str | bytes) -> str:
    data = plaintext.encode() if isinstance(plaintext, str) else plaintext
    return _fernet().encrypt(data).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def decrypt_secret_bytes(ciphertext: str) -> bytes:
    return _fernet().decrypt(ciphertext.encode())


def try_decrypt_secret(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return decrypt_secret(ciphertext)
    except InvalidToken:
        return None


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "••••"
    return f"{value[:3]}••••{value[-3:]}"


# -------------------------------------------------------------------------- signed URLs
def sign_payload(payload: dict[str, Any], ttl_seconds: int | None = None) -> str:
    settings = get_settings()
    now = int(time.time())
    data = {**payload, "iat": now, "exp": now + (ttl_seconds or settings.signed_url_ttl_seconds)}
    return jwt.encode(data, settings.secret_key, algorithm="HS256")


def verify_signed_payload(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode()


if __name__ == "__main__":  # pragma: no cover - CLI helper
    if len(sys.argv) > 1 and sys.argv[1] == "gen-key":
        print(generate_fernet_key())
    elif len(sys.argv) > 1 and sys.argv[1] == "gen-secret":
        print(secrets.token_urlsafe(48))
    else:
        print("usage: python -m app.core.security [gen-key|gen-secret]")
