"""
AutoOptimiser routes — the self-optimising campaign brain.

GET  /autooptimiser     → the page (3 zones: one number / what I did / needs your call)
GET  /autooptimiser-data → evaluate all active campaigns (cached), the 3-zone payload
POST /autooptimiser-apply → apply one queued decision
POST /autooptimiser-undo  → revert an auto-applied fix
POST /autooptimiser-run   → force a full background pass (also called by the daily cron)
POST /autooptimiser-strategist-approve → approve a risky LLM strategist suggestion
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..config import TEMPLATES_DIR
from ..services import autooptimiser

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


@router.get("/autooptimiser", response_class=HTMLResponse)
def autooptimiser_page(request: Request):
    return templates.TemplateResponse(request, "autooptimiser.html", {"active": "autooptimiser"})


@router.get("/autooptimiser-data")
def autooptimiser_data(force: bool = False):
    """The 3-zone payload. Auto-applies safe fixes on each pass (unless DRY_RUN)."""
    now = datetime.now(timezone.utc)
    cached = _CACHE.get("data")
    at = _CACHE.get("at")
    fresh = (not force and cached is not None and at is not None
             and (now - at).total_seconds() < _CACHE_TTL_SECS)
    if not fresh:
        cached = autooptimiser.run_autooptimiser(apply_safe=True)
        _CACHE.update({"data": cached, "at": now})
    # The applied log is cheap + always-fresh (read from disk).
    cached = {**cached, "applied_log": autooptimiser.get_applied_log()}
    return cached


@router.post("/autooptimiser-apply")
def autooptimiser_apply(req: ApplyFixReq):
    """Apply one queued human-decision fix. Re-evaluates to locate the fix payload."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    data = _CACHE.get("data") or autooptimiser.run_autooptimiser(apply_safe=False)
    # Search per-campaign decisions AND account-level actions
    all_fixes = list(data.get("all_decisions", [])) + list(data.get("account_actions", []))
    fix = next((f for f in all_fixes
                if f["campaign_id"] == req.campaign_id and f["fix_type"] == req.fix_type), None)
    if not fix:
        raise HTTPException(status_code=404, detail="That recommendation is no longer current.")
    res = autooptimiser.apply_fix(fix, auto=False)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Apply failed."))
    _CACHE["data"] = None  # invalidate so the next load reflects the change
    return res


@router.post("/autooptimiser-undo")
def autooptimiser_undo(req: UndoFixReq):
    res = autooptimiser.undo_fix(req.campaign_id, req.fix_type)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Undo failed."))
    _CACHE["data"] = None
    return res


@router.post("/autooptimiser-run")
def autooptimiser_run():
    """Force a full pass (auto-applies safe fixes). Used by the daily cron."""
    data = autooptimiser.run_autooptimiser(apply_safe=True)
    _CACHE.update({"data": data, "at": datetime.now(timezone.utc)})
    return {"ok": True, "auto_applied": len(data.get("auto_applied", [])),
            "decisions": len(data.get("all_decisions", []))}


class StrategistApproveReq(BaseModel):
    """Approve one risky LLM strategist suggestion by its index in the suggestions list."""
    suggestion_index: int = Field(..., ge=0, description="Index into strategist.suggestions[]")


@router.post("/autooptimiser-retarget-all")
def autooptimiser_retarget_all():
    """
    Rung 12 — refresh targeting on all active campaigns against the current
    CLIENTELE_TARGETING_MAP. Called by APScheduler every 30 days; also
    triggerable manually from the AutoOptimiser page.
    """
    result = autooptimiser.periodic_retarget_all()
    return result


@router.post("/autooptimiser-strategist-approve")
def autooptimiser_strategist_approve(req: StrategistApproveReq):
    """
    Apply a risky LLM strategist suggestion that the user has approved.

    Locates the suggestion at the given index in the cached strategist payload,
    validates it is tagged 'risky' (safe ones are auto-applied), and routes its
    embedded fix dict through the existing Tier-1 apply_fix() path — ensuring
    all Meta writes remain deterministic, reversible, and cooldown-guarded.
    """
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")

    cached = _CACHE.get("data") or {}
    suggestions = (cached.get("strategist") or {}).get("suggestions", [])

    if req.suggestion_index >= len(suggestions):
        raise HTTPException(status_code=404,
                            detail="Suggestion index out of range — reload the page.")

    suggestion = suggestions[req.suggestion_index]

    # Only risky suggestions need manual approval — safe ones auto-apply.
    if suggestion.get("risk") != "risky":
        raise HTTPException(status_code=400,
                            detail="This suggestion is safe-rated and should already be applied.")

    fix = suggestion.get("fix")
    if not fix:
        raise HTTPException(status_code=400,
                            detail="Suggestion has no executable fix payload.")

    res = autooptimiser.apply_fix(fix, auto=False)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Apply failed."))

    _CACHE["data"] = None  # invalidate so the next load reflects the change
    return {**res, "suggestion_title": suggestion.get("title", "")}

