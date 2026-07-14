from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .config import Settings
from .errors import AppError

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
PASSWORD_MIN_LENGTH = 12
_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def normalize_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.fullmatch(username):
        raise AppError(
            "invalid_username",
            "Username must be 3-32 characters using letters, numbers, dot, underscore, or hyphen.",
            fields={"username": "Use a conservative local username."},
        )
    return username.casefold()


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AppError(
            "weak_password",
            f"Password must contain at least {PASSWORD_MIN_LENGTH} characters.",
            fields={"password": "Choose a longer password."},
        )
    if len(password) > 1024:
        raise AppError(
            "invalid_password", "Password is too long.", fields={"password": "Too long."}
        )


def hash_password(password: str) -> str:
    validate_password(password)
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def keyed_hash(value: str, settings: Settings) -> str:
    return hmac.new(
        settings.session_secret.get_secret_value().encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def stable_secret_hash(value: str, settings: Settings) -> str:
    return keyed_hash(value, settings)


def session_expiry(settings: Settings) -> datetime:
    return datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)


def secure_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def new_login_csrf(settings: Settings) -> str:
    nonce = secrets.token_urlsafe(24)
    signature = keyed_hash(f"login-csrf:{nonce}", settings)
    return f"{nonce}.{signature}"


def verify_login_csrf(token: str, settings: Settings) -> bool:
    try:
        nonce, signature = token.rsplit(".", 1)
    except ValueError:
        return False
    expected = keyed_hash(f"login-csrf:{nonce}", settings)
    return secure_compare(signature, expected)
