"""
Autopilot routes — the self-optimising campaign brain.

GET  /autopilot          → the page (3 zones: one number / what I did / needs your call)
GET  /autopilot-data     → evaluate all active campaigns (cached), the 3-zone payload
POST /autopilot-apply    → apply one queued decision
POST /autopilot-undo     → revert an auto-applied fix
POST /autopilot-run      → force a full background pass (also called by the daily cron)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..config import TEMPLATES_DIR
from ..services import autopilot

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Short in-process cache so opening the tab doesn't re-hit the Graph API every time.
_CACHE: dict = {"data": None, "at": None}
_CACHE_TTL_SECS = 30 * 60


class ApplyFixReq(BaseModel):
    campaign_id: str
    fix_type: str


class UndoFixReq(BaseModel):
    campaign_id: str
    fix_type: str


@router.get("/autopilot", response_class=HTMLResponse)
def autopilot_page(request: Request):
    return templates.TemplateResponse(request, "autopilot.html", {"active": "autopilot"})


@router.get("/autopilot-data")
def autopilot_data(force: bool = False):
    """The 3-zone payload. Auto-applies safe fixes on each pass (unless DRY_RUN)."""
    now = datetime.now(timezone.utc)
    cached = _CACHE.get("data")
    at = _CACHE.get("at")
    fresh = (not force and cached is not None and at is not None
             and (now - at).total_seconds() < _CACHE_TTL_SECS)
    if not fresh:
        cached = autopilot.run_autopilot(apply_safe=True)
        _CACHE.update({"data": cached, "at": now})
    # The applied log is cheap + always-fresh (read from disk).
    cached = {**cached, "applied_log": autopilot.get_applied_log()}
    return cached


@router.post("/autopilot-apply")
def autopilot_apply(req: ApplyFixReq):
    """Apply one queued human-decision fix. Re-evaluates to locate the fix payload."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    data = _CACHE.get("data") or autopilot.run_autopilot(apply_safe=False)
    # Search per-campaign decisions AND account-level actions
    all_fixes = list(data.get("all_decisions", [])) + list(data.get("account_actions", []))
    fix = next((f for f in all_fixes
                if f["campaign_id"] == req.campaign_id and f["fix_type"] == req.fix_type), None)
    if not fix:
        raise HTTPException(status_code=404, detail="That recommendation is no longer current.")
    res = autopilot.apply_fix(fix, auto=False)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Apply failed."))
    _CACHE["data"] = None  # invalidate so the next load reflects the change
    return res


@router.post("/autopilot-undo")
def autopilot_undo(req: UndoFixReq):
    res = autopilot.undo_fix(req.campaign_id, req.fix_type)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Undo failed."))
    _CACHE["data"] = None
    return res


@router.post("/autopilot-run")
def autopilot_run():
    """Force a full pass (auto-applies safe fixes). Used by the daily cron."""
    data = autopilot.run_autopilot(apply_safe=True)
    _CACHE.update({"data": data, "at": datetime.now(timezone.utc)})
    return {"ok": True, "auto_applied": len(data.get("auto_applied", [])),
            "decisions": len(data.get("all_decisions", []))}
