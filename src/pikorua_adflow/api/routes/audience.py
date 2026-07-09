"""Audience targeting + CRM→Meta audience sync routes."""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..config import AUDIENCES_REGISTRY_PATH
from ..models import AudienceSave, CRMAudienceRequest, RetargetSuggestionApply
from ..services import campaign_service as cs

router = APIRouter()


@router.get("/audience-retarget-suggestions/{run_id}")
def audience_retarget_suggestions(run_id: str):
    """Smart-retarget suggestions for a draft campaign's audience: keep what's there,
    propose CRM-proven / profile segments to ADD and irrelevant ones to REMOVE. Read-only
    — the user approves each one via /apply-retarget-suggestion."""
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    brief = run.get("brief", {})
    audience = cs.effective_audience(review_folder, brief)
    token = os.getenv("META_ACCESS_TOKEN", "")
    crm_leads: list[dict] = []
    try:
        from pikorua_adflow.analytics import crm_analytics as _ca
        crm_leads, _src = _ca.get_leads()
    except Exception:
        crm_leads = []
    from pikorua_adflow.analytics import targeting_intelligence as _ti
    result = _ti.suggest_targeting_changes(
        audience,
        clientele_type=brief.get("clientele_type", "") or "",
        crm_leads=crm_leads,
        token=token,
        property_type=brief.get("property_type", "") or "",
    )
    return {"run_id": run_id, **result}


@router.post("/apply-retarget-suggestion/{run_id}")
def apply_retarget_suggestion(run_id: str, payload: RetargetSuggestionApply):
    """Apply one add/remove suggestion to a draft campaign's audience overlay."""
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = dict(cs.effective_audience(review_folder, run.get("brief", {})))
    from pikorua_adflow.analytics import targeting_intelligence as _ti
    try:
        audience = _ti.apply_suggestion(audience, payload.field, payload.id, payload.name, payload.op)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    cs.save_audience(review_folder, audience)
    from pikorua_adflow.tools import meta_targeting as _mt
    return {"run_id": run_id, "audience": audience, "summary": _mt.audience_summary(audience)}


@router.get("/audience/{run_id}")
def get_audience(run_id: str):
    """Current ad-set audience for a run (seeds the curated default on first call)."""
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = cs.effective_audience(review_folder, run.get("brief", {}))
    from pikorua_adflow.tools import meta_targeting as _mt
    return {"run_id": run_id, "audience": audience, "summary": _mt.audience_summary(audience),
            "creative_mode": cs.get_creative_mode(review_folder)}


@router.post("/audience/{run_id}")
def save_audience(run_id: str, payload: AudienceSave):
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = payload.model_dump()
    cs.save_audience(review_folder, audience)
    from pikorua_adflow.tools import meta_targeting as _mt
    return {"run_id": run_id, "audience": audience, "summary": _mt.audience_summary(audience)}


@router.get("/audience-search")
def audience_search(q: str, type: str = "interest", region: str = "", home: str = "",
                    city: str = ""):
    """Typeahead proxy to Meta's read-only Targeting Search (for the add-chip UI).

    type='area' is the unified locality search — neighbourhoods AND pincodes in one
    list (the user shouldn't have to know whether "Bopal" is a Meta neighbourhood or
    reach for a separate pincode box). Each result carries a `kind` so the caller
    stores it in the right bucket; when `home` (the property's pincode) is passed,
    results are ranked nearest-first."""
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
        if type == "neighborhood":
            return {"results": _mt.search_neighborhoods(q, token, region=region)}
        if type == "zip":
            return {"results": _mt.search_zips(q, token)}
        if type == "area":
            merged: list[dict] = []
            # Pincode-looking queries lead with pincodes; name queries lead with
            # neighbourhoods — but always include both so the two are interchangeable.
            digits = "".join(c for c in q if c.isdigit())
            seen_names: set[str] = set()
            for n in _mt.search_neighborhoods(q, token, region=region, limit=8):
                merged.append({"kind": "neighborhood", "key": n["key"], "name": n["name"],
                               "region": n.get("region", "")})
                seen_names.add(n["name"].strip().lower())
            if digits:
                for z in _mt.search_zips(q, token, limit=10):
                    merged.append({"kind": "zip", "key": z["key"], "name": z["name"],
                                   "area": z.get("area", ""), "primary_city": z.get("primary_city", "")})
            if home:
                merged = _mt._proximity_sort(merged, home)
            # Google-Maps-style fallback: any real place (even one Meta has no named
            # target for, e.g. "Science City") resolves via geocoding and is offered
            # as a dropped-pin custom location. Appended AFTER Meta's exact targets.
            city_hint = city if not digits else ""
            gq = q + (f", {city_hint}" if city_hint and city_hint.lower() not in q.lower() else "")
            for p in _mt.geocode_search(gq, limit=6):
                nm = p["name"].strip()
                if nm.lower() in seen_names:
                    continue
                seen_names.add(nm.lower())
                merged.append({"kind": "place", "name": nm, "lat": p["lat"], "lng": p["lng"],
                               "pincode": p.get("pincode", ""), "display_name": p.get("display_name", "")})
            return {"results": merged}
        return {"results": _mt.search_interests(q, token)}
    except Exception as exc:
        return {"results": [], "error": str(exc)}


@router.get("/geocode")
def geocode(q: str):
    """Free-text place → {lat,lng,pincode} via OpenStreetMap Nominatim (best-effort).
    Powers the map picker's 'search a place' box. Read-only, no Meta token needed."""
    from pikorua_adflow.tools import meta_targeting as _mt
    hit = _mt.geocode_place((q or "").strip())
    return {"result": hit}


@router.get("/audience-geo-suggest/{run_id}")
def audience_geo_suggest(run_id: str):
    """Suggested areas near the property for the quick-add row + map picker seed.

    Returns neighbourhoods + pincodes for the campaign's city, ranked by proximity
    to the property's own locality/pincode ('Near {locality}', not just 'anywhere in
    the city'). Also returns the map centre + home pincode so the map picker can
    open on the exact property. Each entry carries city_key so the saved audience
    can map an area back to its city."""
    from pikorua_adflow.tools import meta_targeting as _mt
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        return {"neighborhoods": [], "zips": [], "error": "META_ACCESS_TOKEN not set"}
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = cs.effective_audience(review_folder, run.get("brief", {}))
    brief = run.get("brief", {})
    city = audience.get("city") or brief.get("city", "")
    region = audience.get("region", "")
    city_key = audience.get("city_key") or ""
    locality = audience.get("locality") or brief.get("locality", "")
    home_pincode = audience.get("home_pincode", "")
    center = None
    mp = audience.get("map_point") or {}
    if mp.get("lat") is not None:
        center = {"lat": mp["lat"], "lng": mp["lng"]}
    try:
        # Recover the property centre/pincode if the saved audience predates this
        # (e.g. was seeded before locality resolution existed).
        if (not center or not home_pincode) and locality and city:
            geo = _mt.geocode_place(f"{locality}, {city}, India")
            if geo:
                center = center or {"lat": geo["lat"], "lng": geo["lng"]}
                home_pincode = home_pincode or geo.get("pincode", "")
        nbh = _mt.suggest_neighborhoods_for_city(city, region, city_key, token)
        zips = _mt.suggest_zips_for_city(city, city_key, region, token,
                                         home_pincode=home_pincode)
        return {"city": city, "locality": locality, "home_pincode": home_pincode,
                "center": center, "neighborhoods": nbh, "zips": zips}
    except Exception as exc:
        return {"city": city, "locality": locality, "center": center,
                "neighborhoods": [], "zips": [], "error": str(exc)}


@router.post("/preselect-property-location/{run_id}")
def preselect_property_location(run_id: str):
    """Resolve the campaign's OWN property locality into ready-to-add geo targets:
    the matching neighbourhood, its nearest pincodes, and a dropped pin at the exact
    property (so it's targeted even when Meta has no named area for it). Powers the
    one-click 'Add property location' button — the fallback for campaigns created
    before auto-preselect existed, or where geocoding was momentarily unavailable at
    seed time. Read-only; the client merges the result and the user still saves."""
    from pikorua_adflow.tools import meta_targeting as _mt
    token = os.getenv("META_ACCESS_TOKEN", "")
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = cs.effective_audience(review_folder, run.get("brief", {}))
    brief = run.get("brief", {})
    city = audience.get("city") or brief.get("city", "")
    region = audience.get("region", "")
    city_key = str(audience.get("city_key") or "")
    locality = audience.get("locality") or brief.get("locality", "")
    if not locality:
        raise HTTPException(status_code=400, detail="This campaign has no property locality set.")

    neighborhoods: list[dict] = []
    zips: list[dict] = []
    center = None
    home_pincode = audience.get("home_pincode", "")
    if city_key and token:
        try:
            pre = _mt.preselect_locality_areas(city, region, city_key, locality, token)
            neighborhoods = pre.get("neighborhoods", [])
            zips = pre.get("zips", [])
            center = pre.get("center")
            home_pincode = home_pincode or pre.get("home_pincode", "")
        except Exception:
            pass
    # A pin at the exact property guarantees coverage even if Meta has no named area.
    pin = None
    if not center:
        geo = _mt.geocode_place(f"{locality}, {city}, India")
        if geo:
            center = {"lat": geo["lat"], "lng": geo["lng"]}
            home_pincode = home_pincode or geo.get("pincode", "")
    if center:
        pin = {"name": locality, "lat": center["lat"], "lng": center["lng"],
               "radius_km": 5, "city_key": city_key}

    if not (neighborhoods or zips or pin):
        raise HTTPException(status_code=404,
                            detail=f"Couldn't locate “{locality}”. Add areas manually or drop a pin on the map.")
    return {"run_id": run_id, "locality": locality, "home_pincode": home_pincode,
            "center": center, "neighborhoods": neighborhoods, "zips": zips, "pin": pin}


@router.get("/audience-pool")
def audience_pool():
    """Verified, fixed option lists for targeting axes with no live Meta search
    (job titles, income clusters, industries) — the add-UI picks from these
    instead of free-text, so nothing unverified reaches the Graph API. Also serves
    the relationship + NRI-diaspora suggestion lists for click-to-add chips."""
    from pikorua_adflow.tools import meta_targeting as _mt
    return {
        "work_positions": _mt.WORK_POSITION_POOL,
        "income_clusters": _mt._INCOME_TOP_10,
        "industries": _mt._INDUSTRIES_ENTERPRISE,
        "relationship_statuses": _mt.RELATIONSHIP_STATUS_POOL,
        "nri_suggestions": _mt.NRI_DIASPORA_SUGGESTIONS,
    }


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
        # Join our registry so the UI can say WHY an audience exists (role:
        # "seed" = CRM contacts, "lookalike" = expansion → suggest Include,
        # "exclusion" = bad leads/brokers → suggest Exclude).
        try:
            registry = (json.loads(AUDIENCES_REGISTRY_PATH.read_text(encoding="utf-8"))
                        if AUDIENCES_REGISTRY_PATH.exists() else [])
        except (ValueError, OSError):
            registry = []
        role_by_id = {str(r.get("id")): r.get("role", "") for r in registry}
        return {"audiences": [
            {"id": str(a["id"]), "name": a.get("name", ""), "subtype": a.get("subtype", ""),
             "approximate_count": a.get("approximate_count_lower_bound", 0),
             "role": role_by_id.get(str(a["id"]), "")}
            for a in rows
        ]}
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/saved-target-audiences")
def saved_target_audiences():
    """List Meta's true Saved Audience objects (targeting-spec-only, reusable across
    ad sets) — a distinct picker from /meta-saved-audiences (which despite its name
    actually serves Custom/Lookalike audiences and must stay unchanged)."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    if not ad_account_id:
        raise HTTPException(status_code=503, detail="META_AD_ACCOUNT_ID not set.")
    from pikorua_adflow.tools import meta_tool as _mtt
    try:
        rows = _mtt.list_saved_audiences(ad_account_id, token)
        return {"audiences": [
            {"id": str(a["id"]), "name": a.get("name", ""),
             "approximate_count": a.get("approximate_count_lower_bound", 0),
             "targeting": a.get("targeting") or {}}
            for a in rows
        ]}
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/apply-saved-audience/{run_id}")
def apply_saved_audience(run_id: str, payload: dict):
    """Apply a Meta Saved Audience's targeting spec to this run's audience.
    payload: {"id": "<saved_audience_id>"}. The spec is reverse-mapped onto the
    editable audience (interests, behaviours, ages, geo, platform, custom
    audiences); anything the spec doesn't carry keeps its current value."""
    saved_id = str((payload or {}).get("id", "")).strip()
    if not saved_id:
        raise HTTPException(status_code=400, detail="A saved audience id is required.")
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    from pikorua_adflow.tools import meta_targeting as _mt
    from pikorua_adflow.tools import meta_tool as _mtt
    try:
        rows = _mtt.list_saved_audiences(ad_account_id, token)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    match = next((a for a in rows if str(a.get("id")) == saved_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Saved audience not found on this ad account.")
    base = cs.effective_audience(review_folder, run.get("brief", {}))
    # Snapshot the pre-apply audience (minus any previous snapshot/id bookkeeping)
    # so a later Undo can restore exactly what was there before this Apply.
    snapshot = {k: v for k, v in base.items() if k not in ("_pre_apply_audience", "applied_saved_audience_id")}
    audience = _mt.audience_from_targeting_spec(match.get("targeting") or {}, base)
    audience["applied_saved_audience_id"] = saved_id
    audience["_pre_apply_audience"] = snapshot
    cs.save_audience(review_folder, audience)
    return {"run_id": run_id, "applied": match.get("name", saved_id),
            "audience": audience, "summary": _mt.audience_summary(audience)}


@router.post("/undo-saved-audience/{run_id}")
def undo_saved_audience(run_id: str):
    """Revert the audience to what it was immediately before the last
    apply-saved-audience call (single-level undo)."""
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    from pikorua_adflow.tools import meta_targeting as _mt
    current = cs.effective_audience(review_folder, run.get("brief", {}))
    snapshot = current.get("_pre_apply_audience")
    if not snapshot:
        raise HTTPException(status_code=400, detail="Nothing to undo.")
    cs.save_audience(review_folder, snapshot)
    return {"run_id": run_id, "audience": snapshot, "summary": _mt.audience_summary(snapshot)}


@router.post("/save-target-audience/{run_id}")
def save_target_audience(run_id: str, payload: dict):
    """Save this run's current ad-set targeting spec as a reusable Meta Saved
    Audience, named by the caller (payload: {"name": "..."})."""
    name = (payload or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="A name is required.")
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    if not ad_account_id:
        raise HTTPException(status_code=503, detail="META_AD_ACCOUNT_ID not set.")
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = cs.effective_audience(review_folder, run.get("brief", {}))
    from pikorua_adflow.tools import meta_targeting as _mt
    from pikorua_adflow.tools import meta_tool as _mtt
    targeting_spec = _mt.build_targeting_spec(audience)
    try:
        saved_id = _mtt.create_saved_audience(ad_account_id, token, name, targeting_spec)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"run_id": run_id, "id": saved_id, "name": name}


@router.get("/lookalike-audiences")
def lookalike_audiences():
    """Registry metadata (built_at/seed_size) + staleness for lookalike audiences.

    The picker calls /meta-saved-audiences for the live list and this route for the
    extra context (when it was built, how big the seed was, whether it needs a
    refresh) — matched client-side by audience id.
    """
    try:
        registry_rows: list[dict] = (
            json.loads(AUDIENCES_REGISTRY_PATH.read_text(encoding="utf-8"))
            if AUDIENCES_REGISTRY_PATH.exists() else []
        )
    except (ValueError, OSError):
        registry_rows = []

    lookalike_rows = [r for r in registry_rows if r.get("subtype") == "LOOKALIKE"]

    from pikorua_adflow.utils import crm_source
    from pikorua_adflow.analytics import lookalike_health as _lh
    try:
        crm_leads, _src = crm_source.fetch_rows()
        current_crm_count = len(crm_leads)
    except Exception:
        current_crm_count = 0

    staleness = _lh.check_staleness(registry_rows, current_crm_count)
    return {"audiences": lookalike_rows, "staleness": staleness}


@router.get("/admin/refresh-targeting-pool")
def refresh_targeting_pool():
    """Query Meta's Targeting Search API for categories not already hardcoded in
    meta_targeting.py's pools (work_positions, demographics, life_events) and
    return a diff report. Read-only — never modifies the hardcoded pools; a human
    reviews the report and edits meta_targeting.py by hand if a category is relevant.
    """
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    from pikorua_adflow.tools import targeting_pool_refresh
    return targeting_pool_refresh.generate_report(token)


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
        existing: list[dict] = json.loads(AUDIENCES_REGISTRY_PATH.read_text(encoding="utf-8")) if AUDIENCES_REGISTRY_PATH.exists() else []
    except (ValueError, OSError):
        existing = []

    # Map each result id key → (role, subtype, default-name). `role` is what the
    # autopilot reads: "lookalike" wires into custom_audiences (rung 3), "exclusion"
    # wires into excluded_custom_audiences (rung 2). Keys cover BOTH the single-upload
    # (custom_audience_id / lookalike_audience_id) and split-upload result shapes —
    # previously the split keys were mismatched, so split audiences never registered.
    _ID_KEYS = [
        ("custom_audience_id", "seed", "CUSTOM", "PIKORUA CRM — All Contacts"),
        ("lookalike_audience_id", "lookalike", "LOOKALIKE", "PIKORUA Lookalike — All Contacts"),
        ("good_leads_audience_id", "seed", "CUSTOM", "PIKORUA CRM — Good Leads (Hot/Warm)"),
        ("good_leads_lookalike_id", "lookalike", "LOOKALIKE", "PIKORUA Lookalike — Good Leads"),
        ("bad_leads_audience_id", "exclusion", "CUSTOM", "PIKORUA CRM — Bad Leads (Exclusion)"),
    ]
    _NAME_KEYS = {
        "good_leads_lookalike_id": "good_lookalike_name",
        "bad_leads_audience_id": "bad_custom_audience_name",
    }
    new_entries: list[dict] = []
    for key, role, subtype, default_name in _ID_KEYS:
        aid = result.get(key)
        if not aid:
            continue
        name = result.get(_NAME_KEYS.get(key, ""), default_name)
        # Refresh the entry if the id already exists (id is stable on reuse now), else add.
        match = next((e for e in existing if e.get("id") == str(aid)), None)
        now_iso = datetime.now(timezone.utc).isoformat()
        seed_size = result.get("total_leads") or result.get("leads_uploaded") or 0
        entry = {"id": str(aid), "name": str(name), "subtype": subtype, "role": role,
                 "built_at": now_iso, "seed_size": int(seed_size)}
        if match:
            match.update(entry)
        else:
            existing.append(entry)
            new_entries.append(entry)
    try:
        AUDIENCES_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIENCES_REGISTRY_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    result["registry_saved"] = len(new_entries)
    return result


@router.get("/download-crm-leads")
def download_crm_leads(type: str = Query(..., pattern="^(good|bad|unclassified)$")):
    """
    Stream an Excel file of good, bad, or unclassified CRM leads.

    ?type=good          → explicitly warm / interested leads
    ?type=bad           → cold / not-interested / lost leads
    ?type=unclassified  → no buying or client status set — neutral, no signal either way
    """
    from pikorua_adflow.tools.meta_audience_tool import (
        _SITE_VISIT_CONFIRMED,
        _categorise,
        _get_raw,
    )
    from pikorua_adflow.utils import crm_source
    import openpyxl

    rows, _src = crm_source.fetch_rows()
    if not rows:
        raise HTTPException(status_code=503, detail="No CRM data available.")

    categorised: list[dict] = []
    for row in rows:
        raw_client = (
            _get_raw(row, "Client Status", "ClientStatus", "client_status")
            or _get_raw(row, "Status", "status")
        )
        raw_buying = _get_raw(row, "Buying Status", "BuyingStatus", "buying_status")
        raw_svisit = _get_raw(row, "Site Visit Status", "SiteVisitStatus", "site_visit_status").lower()

        is_site_visitor = any(v in raw_svisit for v in _SITE_VISIT_CONFIRMED)
        category = _categorise(raw_buying, raw_client)
        if is_site_visitor and category not in ("bad", "broker"):
            category = "good"

        categorised.append({**row, "_category": category})

    _FILE_MAP = {
        "good":         ("pikorua_good_leads.xlsx",         "Good Leads",         {"good"}),
        "bad":          ("pikorua_bad_leads.xlsx",          "Bad Leads",          {"bad"}),
        "unclassified": ("pikorua_unclassified_leads.xlsx", "Unclassified Leads", {"unclassified"}),
    }
    filename, sheet_title, keep = _FILE_MAP[type]
    filtered = [r for r in categorised if r["_category"] in keep]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title

    # Build columns: all original keys + Category
    if filtered:
        original_cols = [k for k in filtered[0].keys() if k != "_category"]
    else:
        original_cols = list(categorised[0].keys()) if categorised else []
        original_cols = [k for k in original_cols if k != "_category"]

    headers = original_cols + ["Category"]
    ws.append(headers)

    for row in filtered:
        ws.append([row.get(c, "") for c in original_cols] + [row["_category"]])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
