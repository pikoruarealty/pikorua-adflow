"""User account management API — list/create/approve/reject.

Every path under /api/users is admin-only, enforced by the AuthMiddleware
admin-path gate in main.py (not re-checked here — same trust boundary as
every other route module in this codebase, which relies on the middleware
for session/role checks rather than per-route dependencies).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import user_store

router = APIRouter(prefix="/api/users")


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


@router.get("")
def list_users():
    return {"users": user_store.list_users()}


@router.post("")
def create_user(body: CreateUserRequest):
    role = body.role if body.role in ("user", "admin") else "user"
    try:
        # Admin-created accounts are approved immediately: creating the
        # account here *is* the approval.
        user = user_store.create_user(body.username, body.password, role=role, status="approved")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"user": user}


@router.post("/{user_id}/approve")
def approve_user(user_id: int):
    user = user_store.approve_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"user": user}


@router.post("/{user_id}/reject")
def reject_user(user_id: int):
    user = user_store.reject_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"user": user}
