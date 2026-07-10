"""Per-user login — accounts live in SQLite (services/user_store.py).

A correct username/password issues a JWT signed with JWT_SECRET (.env),
carrying the username, user id, and role, stored as an httpOnly cookie.
AuthMiddleware in main.py checks that cookie on every request and further
requires role == "admin" for admin-only paths (/users, /api/users/*).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt

from . import user_store

COOKIE_NAME = "pikorua_session"
_ALGORITHM = "HS256"
_SESSION_HOURS = 24 * 14  # 14 days


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set in .env — required for portal login.")
    return secret


def authenticate(username: str, password: str) -> tuple[dict | None, str | None]:
    """Returns (user, None) on success, or (None, error_message) on failure."""
    user = user_store.verify_credentials(username, password)
    if user is None:
        return None, "Incorrect username or password."
    if user["status"] == "pending":
        return None, "Your account is awaiting admin approval."
    if user["status"] == "rejected":
        return None, "Your account request was rejected. Contact an admin."
    return user, None


def create_session_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["username"], "uid": user["id"], "role": user["role"],
        "iat": now, "exp": now + timedelta(hours=_SESSION_HOURS),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def verify_session_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
