"""HTML page routes — serve Jinja2 templates; all dynamic data is fetched by JS."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette import status

from ..config import TEMPLATES_DIR
from ..services import auth
from ..state import RUNS

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _user_ctx(request: Request) -> dict:
    """Session info for nav rendering (admin-only links, etc)."""
    payload = auth.verify_session_token(request.cookies.get(auth.COOKIE_NAME)) or {}
    return {"username": payload.get("sub"), "is_admin": payload.get("role") == "admin",
            "uid": payload.get("uid")}


@router.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/portal")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/portal"):
    return templates.TemplateResponse(request, "login.html", {"next": next})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...),
                  next: str = Form("/portal")):
    user, error = auth.authenticate(username, password)
    if error:
        return templates.TemplateResponse(
            request, "login.html", {"error": error, "next": next, "username": username},
            status_code=status.HTTP_401_UNAUTHORIZED)
    response = RedirectResponse(url=next or "/portal", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        auth.COOKIE_NAME, auth.create_session_token(user),
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14,
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse(request, "register.html", {})


@router.post("/register", response_class=HTMLResponse)
def register_submit(request: Request, username: str = Form(...), password: str = Form(...),
                     confirm_password: str = Form(...)):
    if password != confirm_password:
        return templates.TemplateResponse(
            request, "register.html", {"error": "Passwords do not match.", "username": username},
            status_code=status.HTTP_400_BAD_REQUEST)
    try:
        from ..services import user_store
        # Self-registration is always role='user' and status='pending' — only an
        # admin (via the /users dashboard) can create an admin account or an
        # already-approved account.
        user_store.create_user(username, password, role="user", status="pending")
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "register.html", {"error": str(exc), "username": username},
            status_code=status.HTTP_409_CONFLICT)
    return templates.TemplateResponse(request, "register.html", {"registered": True})


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    return templates.TemplateResponse(request, "users.html", {"active": "users", **_user_ctx(request)})


@router.get("/portal", response_class=HTMLResponse)
def portal(request: Request):
    return templates.TemplateResponse(request, "index.html", {"active": "new", **_user_ctx(request)})


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request):
    return templates.TemplateResponse(request, "campaigns.html", {"active": "runs", **_user_ctx(request)})


@router.get("/results/{run_id}", response_class=HTMLResponse)
def results_page(request: Request, run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = RUNS[run_id]
    if run.get("status") != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or no review folder.")
    return templates.TemplateResponse(
        request, "campaign_detail.html", {"active": "runs", "run_id": run_id, **_user_ctx(request)})


@router.get("/crm-dashboard", response_class=HTMLResponse)
def crm_dashboard(request: Request):
    return templates.TemplateResponse(request, "lead_insights.html", {"active": "leads", **_user_ctx(request)})


@router.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
