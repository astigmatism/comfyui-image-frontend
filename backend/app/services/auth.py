from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import AppError
from ..models import AuditLog, LoginThrottle, User, UserPreference, UserRole, UserState
from ..models import Session as UserSession
from ..security import (
    hash_password,
    keyed_hash,
    new_csrf_token,
    new_session_token,
    normalize_username,
    password_needs_rehash,
    session_expiry,
    validate_password,
    verify_password,
)


class AuthService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def ensure_bootstrap_admin(self, session: Session) -> User | None:
        count = session.scalar(select(func.count()).select_from(User)) or 0
        if count:
            return None
        username = self.settings.bootstrap_admin_username
        configured_password = self.settings.bootstrap_admin_temporary_password
        if not username or configured_password is None:
            raise RuntimeError(
                "Empty database requires CIF_BOOTSTRAP_ADMIN_USERNAME and "
                "CIF_BOOTSTRAP_ADMIN_TEMPORARY_PASSWORD."
            )
        normalized = normalize_username(username)
        password = configured_password.get_secret_value()
        validate_password(password)
        user = User(
            username=username.strip(),
            username_normalized=normalized,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            must_change_password=True,
            is_bootstrap=True,
        )
        session.add(user)
        session.flush()
        session.add(UserPreference(user_id=user.id, gallery_scale=45))
        session.add(
            AuditLog(
                actor_user_id=None,
                target_type="user",
                target_id=user.id,
                action="bootstrap_admin_created",
                metadata_json={"username": user.username},
            )
        )
        session.commit()
        return user

    def authenticate(
        self,
        session: Session,
        *,
        username: str,
        password: str,
        client_ip: str,
        user_agent: str | None,
    ) -> tuple[User, str, UserSession]:
        try:
            normalized = normalize_username(username)
        except AppError:
            normalized = username.strip().casefold()[:64]
        key = keyed_hash(f"{normalized}|{client_ip}", self.settings)
        throttle = session.get(LoginThrottle, key)
        now = datetime.now(UTC)
        if throttle and throttle.blocked_until and _as_utc(throttle.blocked_until) > now:
            raise AppError(
                "login_throttled",
                "Too many login attempts. Try again later.",
                status_code=429,
            )
        user = session.scalar(select(User).where(User.username_normalized == normalized))
        valid = bool(
            user
            and user.state == UserState.ACTIVE
            and verify_password(user.password_hash, password)
        )
        if not valid:
            self._record_failed_attempt(session, key, throttle, now)
            raise AppError(
                "invalid_credentials", "Username or password is incorrect.", status_code=401
            )
        assert user is not None
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        if throttle:
            session.delete(throttle)
        raw_token = new_session_token()
        stored = UserSession(
            id_hash=keyed_hash(raw_token, self.settings),
            user_id=user.id,
            csrf_token=new_csrf_token(),
            session_epoch=user.session_epoch,
            expires_at=session_expiry(self.settings),
            ip_hash=keyed_hash(client_ip, self.settings),
            user_agent=(user_agent or "")[:300] or None,
        )
        session.add(stored)
        session.commit()
        return user, raw_token, stored

    def _record_failed_attempt(
        self,
        session: Session,
        key: str,
        throttle: LoginThrottle | None,
        now: datetime,
    ) -> None:
        window = timedelta(seconds=self.settings.login_window_seconds)
        if throttle is None or now - _as_utc(throttle.window_started_at) > window:
            if throttle:
                session.delete(throttle)
                session.flush()
            throttle = LoginThrottle(key_hash=key, attempts=0, window_started_at=now)
            session.add(throttle)
        throttle.attempts += 1
        if throttle.attempts >= self.settings.login_max_attempts:
            throttle.blocked_until = now + timedelta(seconds=self.settings.login_block_seconds)
        session.commit()

    def resolve_session(
        self, session: Session, raw_token: str | None
    ) -> tuple[User, UserSession] | None:
        if not raw_token:
            return None
        stored = session.get(UserSession, keyed_hash(raw_token, self.settings))
        if stored is None or stored.revoked_at is not None:
            return None
        now = datetime.now(UTC)
        if _as_utc(stored.expires_at) <= now:
            session.delete(stored)
            session.commit()
            return None
        user = session.get(User, stored.user_id)
        if (
            user is None
            or user.state != UserState.ACTIVE
            or stored.session_epoch != user.session_epoch
        ):
            stored.revoked_at = now
            session.commit()
            return None
        if now - _as_utc(stored.last_seen_at) > timedelta(minutes=5):
            stored.last_seen_at = now
            session.commit()
        return user, stored

    def logout(self, session: Session, raw_token: str | None) -> None:
        if not raw_token:
            return
        stored = session.get(UserSession, keyed_hash(raw_token, self.settings))
        if stored and stored.revoked_at is None:
            stored.revoked_at = datetime.now(UTC)
            session.commit()

    def change_password(
        self,
        session: Session,
        *,
        user: User,
        current_password: str | None,
        new_password: str,
        current_session_hash: str,
    ) -> None:
        if not user.must_change_password and (
            not current_password or not verify_password(user.password_hash, current_password)
        ):
            raise AppError(
                "invalid_current_password",
                "Current password is incorrect.",
                status_code=403,
                fields={"current_password": "Incorrect password."},
            )
        validate_password(new_password)
        if verify_password(user.password_hash, new_password):
            raise AppError(
                "password_reused",
                "New password must differ from the current password.",
                fields={"new_password": "Choose a different password."},
            )
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        user.session_epoch += 1
        session.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.id_hash != current_session_hash)
            .values(revoked_at=datetime.now(UTC))
        )
        current = session.get(UserSession, current_session_hash)
        if current:
            current.session_epoch = user.session_epoch
        session.add(
            AuditLog(
                actor_user_id=user.id,
                target_type="user",
                target_id=user.id,
                action="password_changed",
                metadata_json={},
            )
        )
        session.commit()

    def create_user(
        self, session: Session, *, actor: User, username: str, temporary_password: str
    ) -> User:
        self._require_admin(actor)
        normalized = normalize_username(username)
        if session.scalar(select(User.id).where(User.username_normalized == normalized)):
            raise AppError(
                "username_exists",
                "That username is already in use.",
                status_code=409,
                fields={"username": "Already exists."},
            )
        validate_password(temporary_password)
        user = User(
            username=username.strip(),
            username_normalized=normalized,
            password_hash=hash_password(temporary_password),
            role=UserRole.USER,
            must_change_password=True,
        )
        session.add(user)
        session.flush()
        session.add(UserPreference(user_id=user.id, gallery_scale=45))
        session.add(
            AuditLog(
                actor_user_id=actor.id,
                target_type="user",
                target_id=user.id,
                action="user_created",
                metadata_json={"username": user.username},
            )
        )
        session.commit()
        return user

    def reset_password(
        self,
        session: Session,
        *,
        actor: User,
        target: User,
        temporary_password: str,
    ) -> None:
        self._require_admin(actor)
        self._require_ordinary_target(target)
        validate_password(temporary_password)
        target.password_hash = hash_password(temporary_password)
        target.must_change_password = True
        target.session_epoch += 1
        session.execute(delete(UserSession).where(UserSession.user_id == target.id))
        session.add(
            AuditLog(
                actor_user_id=actor.id,
                target_type="user",
                target_id=target.id,
                action="password_reset",
                metadata_json={},
            )
        )
        session.commit()

    def revoke_user_sessions(self, session: Session, user: User) -> None:
        user.session_epoch += 1
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
        session.flush()

    @staticmethod
    def _require_admin(user: User) -> None:
        if user.role != UserRole.ADMIN:
            raise AppError("forbidden", "Administrator access is required.", status_code=403)

    @staticmethod
    def _require_ordinary_target(user: User) -> None:
        if user.role != UserRole.USER or user.is_bootstrap:
            raise AppError(
                "forbidden",
                "The bootstrap administrator cannot be modified here.",
                status_code=403,
            )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
