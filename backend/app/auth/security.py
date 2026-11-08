"""Password hashing and session tokens.

The session is a signed JWT in an httpOnly cookie: page scripts cannot read it (so an XSS
bug cannot steal it), and SameSite=Lax keeps other sites from sending it with POST requests.
The token carries the user's session_version; bumping that number in the database (password
change, deactivation) instantly invalidates every token issued before.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from fastapi import Response
from pwdlib import PasswordHash

from app.config import get_settings

COOKIE_NAME = "shiori_session"
_ALGORITHM = "HS256"
_hasher = PasswordHash.recommended()  # Argon2id
# Verified when the email is unknown, so a wrong email takes as long as a wrong password
# and response times don't reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False
    return _hasher.verify(password, password_hash)


@lru_cache
def _secret() -> str:
    secret = get_settings().secret_key
    if not secret or len(secret) < 32:
        raise RuntimeError(
            "SECRET_KEY is missing or too short (min 32 characters). Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))" and add it to .env'
        )
    return secret


@dataclass
class SessionClaims:
    user_id: uuid.UUID
    session_version: int


def create_session_token(user_id: uuid.UUID, session_version: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "ver": session_version,
        "iat": now,
        "exp": now + timedelta(days=get_settings().session_days),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def read_session_token(token: str) -> SessionClaims | None:
    """The token's claims, or None if it is invalid, tampered with or expired."""
    try:
        payload = jwt.decode(
            token, _secret(), algorithms=[_ALGORITHM], options={"require": ["sub", "exp", "ver"]}
        )
        return SessionClaims(uuid.UUID(payload["sub"]), int(payload["ver"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


def set_session_cookie(response: Response, user_id: uuid.UUID, session_version: int) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user_id, session_version),
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
