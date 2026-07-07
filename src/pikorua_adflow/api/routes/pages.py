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


@router.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/portal")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/portal"):
    return templates.TemplateResponse(request, "login.html", {"next": next})


@router.post("/login")
def login_submit(request: Request, password: str = Form(...), next: str = Form("/portal")):
    if not auth.check_password(password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect password.", "next": next},
            status_code=status.HTTP_401_UNAUTHORIZED)
    response = RedirectResponse(url=next or "/portal", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        auth.COOKIE_NAME, auth.create_session_token(),
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14,
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@router.get("/portal", response_class=HTMLResponse)
def portal(request: Request):
    return templates.TemplateResponse(request, "index.html", {"active": "new"})


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request):
    return templates.TemplateResponse(request, "campaigns.html", {"active": "runs"})


@router.get("/results/{run_id}", response_class=HTMLResponse)
def results_page(request: Request, run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = RUNS[run_id]
    if run.get("status") != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or no review folder.")
    return templates.TemplateResponse(
        request, "campaign_detail.html", {"active": "runs", "run_id": run_id})


@router.get("/crm-dashboard", response_class=HTMLResponse)
def crm_dashboard(request: Request):
    return templates.TemplateResponse(request, "lead_insights.html", {"active": "leads"})


@router.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
