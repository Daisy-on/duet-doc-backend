import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    return password_hash.verify(password, encoded_hash)


def verify_and_update_password(password: str, encoded_hash: str) -> tuple[bool, str | None]:
    return password_hash.verify_and_update(password, encoded_hash)


def create_access_token(user_id: UUID, session_id: UUID, settings: Settings) -> tuple[str, int]:
    lifetime = timedelta(minutes=settings.auth_access_token_minutes)
    now = datetime.now(UTC)
    expires_at = now + lifetime
    token = jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "type": "access",
            "iat": now,
            "exp": expires_at,
            "iss": settings.auth_jwt_issuer,
            "aud": settings.auth_jwt_audience,
        },
        settings.auth_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str, settings: Settings) -> tuple[UUID, UUID]:
    try:
        payload = jwt.decode(
            token,
            settings.auth_jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=settings.auth_jwt_audience,
            issuer=settings.auth_jwt_issuer,
            options={"require": ["sub", "sid", "type", "iat", "exp"]},
        )
        if payload["type"] != "access":
            raise ValueError("Unexpected token type")
        return UUID(payload["sub"]), UUID(payload["sid"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid access token") from exc


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
