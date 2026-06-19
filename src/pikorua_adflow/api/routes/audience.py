"""Audience targeting + CRM→Meta audience sync routes."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import AUDIENCES_REGISTRY_PATH
from ..models import AudienceSave, CRMAudienceRequest
from ..services import campaign_service as cs

router = APIRouter()


@router.get("/audience/{run_id}")
def get_audience(run_id: str):
    """Current ad-set audience for a run (seeds the curated default on first call)."""
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = cs.effective_audience(review_folder, run.get("brief", {}))
    from pikorua_adflow.tools import meta_targeting as _mt
    return {"run_id": run_id, "audience": audience, "summary": _mt.audience_summary(audience)}


@router.post("/audience/{run_id}")
def save_audience(run_id: str, payload: AudienceSave):
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = payload.model_dump()
    cs.save_audience(review_folder, audience)
    from pikorua_adflow.tools import meta_targeting as _mt
    return {"run_id": run_id, "audience": audience, "summary": _mt.audience_summary(audience)}


@router.get("/audience-search")
def audience_search(q: str, type: str = "interest"):
    """Typeahead proxy to Meta's read-only Targeting Search (for the add-chip UI)."""
    from pikorua_adflow.tools import meta_targeting as _mt
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        return {"results": [], "error": "META_ACCESS_TOKEN not set"}
    q = (q or "").strip()
    if len(q) < 2:
        return {"results": []}
    try:
        if type == "city":
            return {"results": _mt.search_cities(q, token)}
        if type == "behaviour":
            return {"results": _mt.search_behaviours(q, token)}
        return {"results": _mt.search_interests(q, token)}
    except Exception as exc:
        return {"results": [], "error": str(exc)}


@router.get("/meta-saved-audiences")
def meta_saved_audiences():
    """Fetch custom audiences from the Meta ad account for the audience picker."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    if not ad_account_id:
        raise HTTPException(status_code=503, detail="META_AD_ACCOUNT_ID not set.")
    from pikorua_adflow.tools import meta_tool as _mtt
    try:
        data = _mtt._get(
            f"act_{ad_account_id}/customaudiences",
            token,
            params={"fields": "id,name,subtype,approximate_count_lower_bound", "limit": "100"},
        )
        rows = sorted(
            data.get("data", []),
            key=lambda x: (x.get("subtype") != "LOOKALIKE", x.get("name", "").lower()),
        )
        return {"audiences": [
            {"id": str(a["id"]), "name": a.get("name", ""), "subtype": a.get("subtype", ""),
             "approximate_count": a.get("approximate_count_lower_bound", 0)}
            for a in rows
        ]}
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/upload-crm-audience")
def upload_crm_audience(req: CRMAudienceRequest):
    """Upload qualified CRM leads to Meta as a Custom Audience + Lookalike."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set — Phase 3 prerequisite.")
    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    if not ad_account_id:
        raise HTTPException(status_code=503, detail="META_AD_ACCOUNT_ID not set in .env.")

    from pikorua_adflow.tools.meta_audience_tool import upload_crm_lookalike, upload_crm_split_audiences
    if req.split:
        result = upload_crm_split_audiences(ad_account_id=ad_account_id, target_countries=req.target_countries)
    else:
        result = upload_crm_lookalike(ad_account_id=ad_account_id, target_countries=req.target_countries)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    try:
        existing: list[dict] = json.loads(AUDIENCES_REGISTRY_PATH.read_text()) if AUDIENCES_REGISTRY_PATH.exists() else []
    except (ValueError, OSError):
        existing = []
    new_entries: list[dict] = []
    for key in ("custom_audience_id", "lookalike_id",
                "good_custom_audience_id", "good_lookalike_id", "bad_custom_audience_id"):
        aid = result.get(key)
        name = result.get(key.replace("_id", "_name"), key.replace("_id", "").replace("_", " ").title())
        if aid and not any(e.get("id") == str(aid) for e in existing):
            entry = {"id": str(aid), "name": str(name), "subtype": "LOOKALIKE" if "lookalike" in key else "CUSTOM"}
            existing.append(entry)
            new_entries.append(entry)
    try:
        AUDIENCES_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIENCES_REGISTRY_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    except OSError:
        pass
    result["registry_saved"] = len(new_entries)
    return result
