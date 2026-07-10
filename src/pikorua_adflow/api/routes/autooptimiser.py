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

import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..config import AUTOOPTIMISER_CACHE_PATH, AUTOOPTIMISER_CACHE_TTL_SECS, TEMPLATES_DIR
from ..services import autooptimiser

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# In-process cache so opening the tab doesn't re-hit the Graph API every time.
# Also mirrored to disk (AUTOOPTIMISER_CACHE_PATH) so a server restart — including
# uvicorn --reload restarting because run_autooptimiser() wrote to outputs/ — doesn't
# force a full re-evaluation on the very next tab open.
_CACHE: dict = {"data": None, "at": None}
_CACHE_TTL_SECS = AUTOOPTIMISER_CACHE_TTL_SECS


def _load_disk_cache() -> None:
    """Warm the in-process cache from disk on first use after a fresh process start."""
    if _CACHE["data"] is not None or not AUTOOPTIMISER_CACHE_PATH.exists():
        return
    try:
        raw = json.loads(AUTOOPTIMISER_CACHE_PATH.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(raw["at"])
        _CACHE.update({"data": raw["data"], "at": at})
    except Exception:
        pass  # corrupt/missing cache file — next call just re-evaluates


def _save_disk_cache() -> None:
    try:
        AUTOOPTIMISER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUTOOPTIMISER_CACHE_PATH.write_text(
            json.dumps({"data": _CACHE["data"], "at": _CACHE["at"].isoformat()}),
            encoding="utf-8",
        )
    except Exception:
        pass  # disk cache is a resilience nicety, never block the response on it


def _invalidate_cache() -> None:
    """Drop both the in-process and disk cache after a write (apply/undo/approve)."""
    _CACHE.update({"data": None, "at": None})
    try:
        AUTOOPTIMISER_CACHE_PATH.unlink(missing_ok=True)
    except Exception:
        pass


class ApplyFixReq(BaseModel):
    campaign_id: str
    fix_type: str


class UndoFixReq(BaseModel):
    campaign_id: str
    fix_type: str


@router.get("/autooptimiser", response_class=HTMLResponse)
def autooptimiser_page(request: Request):
    from ..services import auth
    payload = auth.verify_session_token(request.cookies.get(auth.COOKIE_NAME)) or {}
    is_admin = payload.get("role") == "admin"
    return templates.TemplateResponse(
        request, "autooptimiser.html", {"active": "autooptimiser", "is_admin": is_admin})


@router.get("/autooptimiser-data")
def autooptimiser_data(force: bool = False):
    """The 3-zone payload. Auto-applies safe fixes on each pass (unless DRY_RUN)."""
    _load_disk_cache()
    now = datetime.now(timezone.utc)
    cached = _CACHE.get("data")
    at = _CACHE.get("at")
    fresh = (not force and cached is not None and at is not None
             and (now - at).total_seconds() < _CACHE_TTL_SECS)
    if not fresh:
        cached = autooptimiser.run_autooptimiser(apply_safe=True)
        _CACHE.update({"data": cached, "at": now})
        _save_disk_cache()
    # The applied log is cheap + always-fresh (read from disk).
    cached = {**cached, "applied_log": autooptimiser.get_applied_log()}
    return cached


@router.post("/autooptimiser-apply")
def autooptimiser_apply(req: ApplyFixReq):
    """Apply one queued human-decision fix. Re-evaluates to locate the fix payload."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    _load_disk_cache()
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
    _invalidate_cache()  # next load reflects the change
    return res


@router.post("/autooptimiser-undo")
def autooptimiser_undo(req: UndoFixReq):
    res = autooptimiser.undo_fix(req.campaign_id, req.fix_type)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Undo failed."))
    _invalidate_cache()
    return res


@router.post("/autooptimiser-run")
def autooptimiser_run():
    """Force a full pass (auto-applies safe fixes). Used by the daily cron."""
    data = autooptimiser.run_autooptimiser(apply_safe=True)
    _CACHE.update({"data": data, "at": datetime.now(timezone.utc)})
    _save_disk_cache()
    return {"ok": True, "auto_applied": len(data.get("auto_applied", [])),
            "decisions": len(data.get("all_decisions", []))}


class StrategistApproveReq(BaseModel):
    """Approve one risky LLM strategist suggestion by its index in the suggestions list."""
    suggestion_index: int = Field(..., ge=0, description="Index into strategist.suggestions[]")


@router.post("/autooptimiser-retarget-all")
def autooptimiser_retarget_all():
    """
    Rung 12 — refresh targeting on all active campaigns against the current
    CLIENTELE_TARGETING_MAP. Called by APScheduler monthly; also triggerable
    manually from the AutoOptimiser page (manual = force past the recency guard).
    """
    result = autooptimiser.periodic_retarget_all(force=True)
    return result


class ApplyLiveRetargetReq(BaseModel):
    campaign_id: str
    field: str = Field(..., description="interests | behaviours | work_positions | industries")
    id: str
    name: str = ""
    op: str = Field(..., description="add | remove")


def _live_audience_from_adsets(adsets: list[dict]) -> dict:
    """Collapse the flexible_spec segments across a campaign's ad sets into an
    audience-shaped dict the smart-retarget analyzer understands."""
    interests, behaviours, work_positions, industries = {}, {}, {}, {}
    for adset in adsets:
        for group in ((adset.get("targeting") or {}).get("flexible_spec") or []):
            for e in group.get("interests", []) or []:
                interests[str(e.get("id"))] = {"id": str(e.get("id")), "name": e.get("name", "")}
            for e in group.get("behaviors", []) or []:
                behaviours[str(e.get("id"))] = {"id": str(e.get("id")), "name": e.get("name", "")}
            for e in group.get("work_positions", []) or []:
                work_positions[str(e.get("id"))] = {"id": str(e.get("id")), "name": e.get("name", "")}
            for e in group.get("industries", []) or []:
                industries[str(e.get("id"))] = {"id": str(e.get("id")), "name": e.get("name", "")}
    return {"interests": list(interests.values()), "behaviours": list(behaviours.values()),
            "work_positions": list(work_positions.values()), "industries": list(industries.values())}


@router.get("/targeting-health")
def targeting_health(refresh: bool = False):
    """Targeting health: (1) NEW Meta targeting parameters not yet in our pools — so the
    user can adopt useful ones; (2) the write-deprecated params we auto-strip at publish,
    with reasons. The 'new params' diff comes from targeting_pool_refresh (cached to disk;
    pass refresh=true to re-query Meta)."""
    import pathlib
    from pikorua_adflow.tools import targeting_deprecations as _dep

    deprecations = [{"field": e["field"], "reason": e["reason"],
                     "substitute": (e.get("substitute") or {}).get("name", "")}
                    for e in _dep.WRITE_DEPRECATED]

    report = None
    report_path = pathlib.Path("outputs") / "targeting_pool_report.json"
    token = os.getenv("META_ACCESS_TOKEN", "")
    if refresh and token:
        try:
            from pikorua_adflow.tools import targeting_pool_refresh as _tpr
            report = _tpr.generate_report(token)
        except Exception as exc:
            report = {"error": str(exc)}
    if report is None and report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = None

    new_params = []
    if report and isinstance(report.get("classes"), dict):
        for cls, info in report["classes"].items():
            for e in (info.get("new") or [])[:25]:
                new_params.append({"class": cls, "id": e.get("id", ""),
                                   "name": e.get("name", ""),
                                   "audience_size": e.get("audience_size", 0)})

    return {
        "deprecations": deprecations,
        "new_params": new_params,
        "report_generated_at": (report or {}).get("generated_at"),
        "has_report": report is not None,
        "token_configured": bool(token),
    }


@router.get("/campaign-retarget-suggestions")
def campaign_retarget_suggestions(campaign_id: str, clientele_type: str = ""):
    """Smart-retarget suggestions for a LIVE campaign — keep current targeting, propose
    segments to add (CRM-proven / profile) and remove (irrelevant). Read-only."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    from pikorua_adflow.tools.meta_tool import fetch_campaign_adsets
    adsets = fetch_campaign_adsets(campaign_id, token)
    if not adsets:
        return {"campaign_id": campaign_id, "add": [], "remove": [], "kept": 0,
                "note": "No ad sets found for this campaign."}
    current = _live_audience_from_adsets(adsets)
    crm_leads: list[dict] = []
    try:
        from pikorua_adflow.analytics import crm_analytics as _ca
        crm_leads, _src = _ca.get_leads()
    except Exception:
        crm_leads = []
    from pikorua_adflow.analytics import targeting_intelligence as _ti
    result = _ti.suggest_targeting_changes(current, clientele_type=clientele_type,
                                           crm_leads=crm_leads, token=token)
    return {"campaign_id": campaign_id, **result}


@router.post("/campaign-apply-retarget")
def campaign_apply_retarget(req: ApplyLiveRetargetReq):
    """Apply one add/remove suggestion across a live campaign's ad sets."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    from pikorua_adflow.tools.meta_tool import apply_segment_to_campaign
    try:
        result = apply_segment_to_campaign(
            req.campaign_id, req.field, {"id": req.id, "name": req.name}, req.op,
            token, dry_run=dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result["errors"] and not any(u.get("ok") for u in result["updated"]):
        raise HTTPException(status_code=502, detail=f"All ad sets failed: {result['errors']}")
    _invalidate_cache()
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

    _load_disk_cache()
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

    _invalidate_cache()  # next load reflects the change
    return {**res, "suggestion_title": suggestion.get("title", "")}

