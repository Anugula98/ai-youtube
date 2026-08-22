"""Password hashing + JWT issuing/verification.

Kept separate from deps.py (which wires this into FastAPI's dependency
system) so the pure crypto logic here has no framework dependency and is
trivially unit-testable on its own.
"""
from __future__ import annotations
import datetime as dt
from typing import Any

from jose import jwt, JWTError
from passlib.context import CryptContext

from .config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def _create_token(subject: str, expires_delta: dt.timedelta, token_type: str) -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    return _create_token(
        str(user_id),
        dt.timedelta(minutes=settings.jwt_access_token_expire_minutes),
        "access",
    )


def create_refresh_token(user_id: int) -> str:
    settings = get_settings()
    return _create_token(
        str(user_id),
        dt.timedelta(minutes=settings.jwt_refresh_token_expire_minutes),
        "refresh",
    )


class InvalidTokenError(Exception):
    pass


def decode_token(token: str, expected_type: str = "access") -> int:
    """Returns the user id encoded in the token, or raises InvalidTokenError.
    Checked `type` explicitly so a leaked refresh token can't be replayed as
    an access token (and vice versa) -- they're structurally identical JWTs
    otherwise, and that distinction is the only thing stopping that misuse."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise InvalidTokenError(f"expected a {expected_type} token")
    try:
        return int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError("token missing a valid subject") from exc