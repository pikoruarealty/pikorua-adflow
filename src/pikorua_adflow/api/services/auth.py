"""Single shared-password gate for the whole portal — no user accounts.

The password lives in .env (PORTAL_PASSWORD). A correct login issues a JWT
signed with JWT_SECRET (.env), stored as an httpOnly cookie. AuthMiddleware
in main.py checks that cookie on every request and redirects to /login if
it's missing, expired, or invalid.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt

COOKIE_NAME = "pikorua_session"
_ALGORITHM = "HS256"
_SESSION_HOURS = 24 * 14  # 14 days


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set in .env — required for portal login.")
    return secret


def portal_password() -> str:
    pw = os.getenv("PORTAL_PASSWORD")
    if not pw:
        raise RuntimeError("PORTAL_PASSWORD is not set in .env — required for portal login.")
    return pw


def check_password(candidate: str) -> bool:
    return bool(candidate) and candidate == portal_password()


def create_session_token() -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": "pikorua-portal", "iat": now, "exp": now + timedelta(hours=_SESSION_HOURS)}
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def verify_session_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
        return True
    except jwt.PyJWTError:
        return False
