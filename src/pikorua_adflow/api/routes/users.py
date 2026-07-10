"""User account management API — list/create/approve/reject.

Every path under /api/users is admin-only, enforced by the AuthMiddleware
admin-path gate in main.py (not re-checked here — same trust boundary as
every other route module in this codebase, which relies on the middleware
for session/role checks rather than per-route dependencies).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services import auth, user_store

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


@router.delete("/{user_id}")
def delete_user(user_id: int, request: Request):
    payload = auth.verify_session_token(request.cookies.get(auth.COOKIE_NAME)) or {}
    if payload.get("uid") == user_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    user = user_store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user["role"] == "admin" and user_store.count_admins() <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last remaining admin.")
    user_store.delete_user(user_id)
    return {"deleted": True}
