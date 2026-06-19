"""Campaign lifecycle + copy editing routes."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import litellm
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..config import TREND_HOOKS_PATH, TREND_TTL_SECONDS
from ..models import (ApproveRequest, CampaignBrief, ContentEdit,
                      RescoreVariantPayload, RewriteCopyPayload)
from ..services import campaign_service as cs
from ..state import RUNS, RUNS_LOCK, save_runs

router = APIRouter()


@router.post("/launch-campaign")
def launch_campaign(brief: CampaignBrief):
    """Queue a pipeline run in the background; return a run_id to poll."""
    run_id = str(uuid.uuid4())[:8]
    with RUNS_LOCK:
        RUNS[run_id] = {
            "status": "queued",
            "brief": brief.model_dump(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "review_folder": None,
        }
    save_runs()
    threading.Thread(target=cs.run_pipeline, args=(run_id, brief), daemon=True,
                     name=f"pipeline-{run_id}").start()
    return JSONResponse(status_code=202, content={
        "status": "queued", "run_id": run_id,
        "message": "Pipeline started. Poll /status/{run_id} for progress.",
        "poll_url": f"/status/{run_id}",
    })


@router.get("/status/{run_id}")
def get_status(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return RUNS[run_id]


@router.get("/results-data/{run_id}")
def results_data(run_id: str):
    """Structured JSON for the campaign-detail page (all tabs)."""
    return cs.get_run_detail(run_id)


@router.get("/runs/json")
def list_runs_json():
    return sorted(RUNS.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)


@router.get("/trend-status")
def trend_status():
    """Age + staleness of cached trend hooks (portal warns the user when stale)."""
    if not TREND_HOOKS_PATH.exists():
        return {"exists": False, "stale": True, "age_hours": None, "last_updated": None}
    age_seconds = datetime.now().timestamp() - TREND_HOOKS_PATH.stat().st_mtime
    age_hours = round(age_seconds / 3600, 1)
    return {
        "exists": True,
        "stale": age_seconds > TREND_TTL_SECONDS,
        "age_hours": age_hours,
        "last_updated": datetime.fromtimestamp(TREND_HOOKS_PATH.stat().st_mtime).strftime("%d %b, %I:%M %p"),
    }


@router.post("/approve/{run_id}")
def approve_run(run_id: str, req: ApproveRequest = None):
    """Mark a completed run approved and store it in vector memory."""
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = RUNS[run_id]
    if run["status"] != "complete":
        raise HTTPException(status_code=400, detail="Only completed runs can be approved.")
    if not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="No review folder found for this run.")
    selected = (req.selected_variants if req else []) or []
    from pikorua_adflow.tools.memory_tool import approve_and_store
    message = approve_and_store(
        run_id=run_id, brief=run.get("brief", {}),
        review_folder=Path(run["review_folder"]),
        scorecard_summary=run.get("copy_scorecard_summary"),
    )
    with RUNS_LOCK:
        RUNS[run_id]["approved"] = True
        RUNS[run_id]["selected_variants"] = selected
    save_runs()
    return {"status": "approved", "run_id": run_id, "message": message, "selected_variants": selected}


@router.delete("/run/{run_id}")
def delete_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    status = RUNS[run_id].get("status", "")
    if status.startswith("running_") or status == "queued":
        raise HTTPException(status_code=400, detail="Cannot delete a run that is currently in progress.")
    with RUNS_LOCK:
        del RUNS[run_id]
    save_runs()
    return {"status": "deleted", "run_id": run_id}


@router.post("/rerun/{run_id}")
def rerun_campaign(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = RUNS[run_id]
    if run.get("status") != "failed":
        raise HTTPException(status_code=400, detail="Only failed runs can be re-run.")
    brief_data = run.get("brief", {})
    brief = CampaignBrief(**brief_data)
    new_run_id = str(uuid.uuid4())[:8]
    with RUNS_LOCK:
        RUNS[new_run_id] = {
            "status": "queued", "brief": brief_data,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "review_folder": None, "rerun_of": run_id,
        }
    save_runs()
    threading.Thread(target=cs.run_pipeline, args=(new_run_id, brief), daemon=True).start()
    return {"status": "queued", "run_id": new_run_id, "rerun_of": run_id}


# ── Content editing overlay ──────────────────────────────────────────────────
@router.post("/edit-content/{run_id}")
def edit_content(run_id: str, payload: ContentEdit):
    run = cs.require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = cs.load_edits(rf)
    ch = payload.channel
    if ch == "meta":
        if payload.variant is None:
            raise HTTPException(status_code=400, detail="variant is required for channel=meta")
        m = edits.setdefault("meta", {})
        cur = m.get(str(payload.variant), {})
        if payload.headline is not None:
            cur["headline"] = payload.headline
        if payload.body is not None:
            cur["body"] = payload.body
        cur.setdefault("added", cur.get("added", False))
        m[str(payload.variant)] = cur
    elif ch in ("google", "whatsapp", "email"):
        edits[ch] = payload.text or ""
    else:
        raise HTTPException(status_code=400, detail=f"Unknown channel '{ch}'")
    cs.save_edits(rf, edits)
    return {"ok": True}


@router.post("/revert-content/{run_id}")
def revert_content(run_id: str, payload: ContentEdit):
    run = cs.require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = cs.load_edits(rf)
    ch = payload.channel
    if ch == "meta":
        m = edits.get("meta", {})
        key = str(payload.variant)
        was_added = bool(m.get(key, {}).get("added"))
        m.pop(key, None)
        edits["meta"] = m
        cs.save_edits(rf, edits)
        if was_added:
            return {"ok": True, "removed": True}
        base = cs.base_meta(rf).get(payload.variant, {})
        return {"ok": True, "removed": False,
                "headline": base.get("headline", ""), "body": base.get("body", "")}
    if ch in ("google", "whatsapp", "email"):
        edits.pop(ch, None)
        cs.save_edits(rf, edits)
        text, _ = cs.effective_channel(rf, ch)
        return {"ok": True, "text": text}
    raise HTTPException(status_code=400, detail=f"Unknown channel '{ch}'")


@router.post("/add-variant/{run_id}")
def add_variant(run_id: str):
    run = cs.require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = cs.load_edits(rf)
    nums = set(cs.base_meta(rf).keys()) | {int(k) for k in edits.get("meta", {})}
    new_num = (max(nums) + 1) if nums else 1
    m = edits.setdefault("meta", {})
    m[str(new_num)] = {"headline": "", "body": "", "added": True}
    edits["deleted_variants"] = [d for d in edits.get("deleted_variants", []) if d != new_num]
    cs.save_edits(rf, edits)
    return {"ok": True, "variant": new_num}


@router.post("/duplicate-variant/{run_id}")
def duplicate_variant(run_id: str, payload: ContentEdit):
    run = cs.require_complete(run_id)
    rf = Path(run["review_folder"])
    src = cs.effective_meta(rf).get(payload.variant, {})
    edits = cs.load_edits(rf)
    nums = set(cs.base_meta(rf).keys()) | {int(k) for k in edits.get("meta", {})}
    new_num = (max(nums) + 1) if nums else 1
    m = edits.setdefault("meta", {})
    m[str(new_num)] = {"headline": src.get("headline", ""), "body": src.get("body", ""), "added": True}
    cs.save_edits(rf, edits)
    return {"ok": True, "variant": new_num}


@router.post("/delete-variant/{run_id}")
def delete_variant(run_id: str, payload: ContentEdit):
    run = cs.require_complete(run_id)
    rf = Path(run["review_folder"])
    v = payload.variant
    edits = cs.load_edits(rf)
    m = edits.get("meta", {})
    if m.get(str(v), {}).get("added"):
        m.pop(str(v), None)
        edits["meta"] = m
    else:
        d = set(edits.get("deleted_variants", []))
        d.add(v)
        edits["deleted_variants"] = sorted(d)
    if "selected_variants" in run:
        run["selected_variants"] = [s for s in run["selected_variants"] if s != v]
    cs.save_edits(rf, edits)
    save_runs()
    return {"ok": True}


@router.post("/restore-variant/{run_id}")
def restore_variant(run_id: str, payload: ContentEdit):
    run = cs.require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = cs.load_edits(rf)
    edits["deleted_variants"] = [d for d in edits.get("deleted_variants", []) if d != payload.variant]
    cs.save_edits(rf, edits)
    return {"ok": True}


# ── AI copy rewrite / rescore ────────────────────────────────────────────────
@router.post("/rewrite-copy/{run_id}")
async def rewrite_copy(run_id: str, payload: RewriteCopyPayload):
    if payload.field not in ("headline", "body"):
        raise HTTPException(status_code=400, detail="field must be 'headline' or 'body'")
    run = RUNS.get(run_id)
    if not run or run.get("status") != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or not found.")
    review_folder = Path(run["review_folder"])
    eff = cs.effective_meta(review_folder)
    variant = eff.get(payload.variant_num)
    if not variant:
        raise HTTPException(status_code=404, detail=f"Variant {payload.variant_num} not found.")
    headline = variant.get("headline", "")
    body = variant.get("body", "")
    brief = run.get("brief", {})
    property_name = brief.get("property_name", "")
    property_type = brief.get("property_type", "")
    city = brief.get("city", "")
    locality = brief.get("locality", "")
    price_cr = brief.get("price_cr", "")
    standout = brief.get("standout_feature", "")
    field_label = "headline" if payload.field == "headline" else "body"
    other_label = "body" if payload.field == "headline" else "headline"
    other_text = body if payload.field == "headline" else headline
    current_text = headline if payload.field == "headline" else body
    limits = {"headline": "under 40 characters", "body": "under 125 characters"}
    system_prompt = f"""You are a luxury real-estate copywriter for PIKORUA, a premium property consultancy.

Campaign context:
- Property: {property_name} ({property_type})
- Location: {locality + ", " if locality else ""}{city}
- Price: ₹{price_cr} Cr
- Standout feature: {standout or "not specified"}

HARD RULES (never break):
1. No invented scarcity: never write unit counts or "limited availability" unless it is literally in the brief.
2. No single-word possessive closers: never end a fragment sequence with "Yours.", "Home.", "Done.", "Claimed.", "Earned." — end on a property truth instead.
3. Luxury restraint: no exclamation marks, no ALL CAPS, no hyperbole like "one-of-a-kind" or "dream home".
4. PIKORUA is a broker — never name the developer's project. Keep it neighbourhood/lifestyle anchored.

The {other_label} for this variant is: "{other_text}"
Keep the new {field_label} coherent with the {other_label} above.
Length: {limits[payload.field]}.

Output ONLY the rewritten {field_label} text — no label, no quotes, no explanation."""
    user_msg = f"""Rewrite this {field_label}:

{current_text}"""
    model = os.getenv("CREATIVE_MODEL", "gemini/gemini-2.5-flash")
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_msg}],
            temperature=0.85, max_tokens=100,
        )
        new_text = resp.choices[0].message.content.strip().strip('"').strip("'")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")
    return {payload.field: new_text}


@router.post("/rescore-variant/{run_id}")
async def rescore_variant(run_id: str, payload: RescoreVariantPayload):
    run = RUNS.get(run_id)
    if not run or run.get("status") != "complete":
        raise HTTPException(status_code=400, detail="Run not complete or not found.")
    brief = run.get("brief", {})
    property_type = brief.get("property_type", "")
    city = brief.get("city", "")
    locality = brief.get("locality", "")
    price_cr = brief.get("price_cr", "")
    company_name = brief.get("company_name", "")
    system_prompt = f"""You are a luxury real-estate ad evaluator for PIKORUA, a premium property consultancy.

Campaign context:
- Property type: {property_type}
- Location: {(locality + ", ") if locality else ""}{city}
- Price: ₹{price_cr} Cr
- Company name: {company_name or "(none — unbranded run)"}

Score the following Meta ad copy on FOUR dimensions, each 0–10:
1. Brand Voice: calm authority, restraint, no hollow superlatives, no exclamation marks.
2. Platform Fit: concise, scroll-stopping, works as paid social.
3. Specificity: grounded in real location/lifestyle details, no vague claims.
4. Luxury Signal: creates genuine desire without pressure. Sophisticated aspiration.

HARD FAILS (return score 0 and note in flag):
- Invented facts (unit counts, floor counts, percentages not in brief)
- Names the developer's project explicitly
- Invents scarcity ("limited units", "only X left", "fewer than N")
- Single-word possessive closers: "Yours.", "Home.", "Done.", "Claimed.", "Earned."

Return ONLY this JSON (no markdown fence, no extra text):
{{"brand_voice":<0-10>,"platform_fit":<0-10>,"specificity":<0-10>,"luxury_signal":<0-10>,"flag":"PASS or reason"}}"""
    user_msg = f"Headline: {payload.headline}\nBody: {payload.body}"
    model = os.getenv("CREATIVE_MODEL", "gemini/gemini-2.5-flash")
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_msg}],
            temperature=0.2, max_tokens=120,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
        data = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Rescore failed: {exc}")
    dim_keys = ("brand_voice", "platform_fit", "specificity", "luxury_signal")
    scores = {k: max(0, min(10, round(float(data.get(k, 0))))) for k in dim_keys}
    avg = round(sum(scores.values()) / len(scores), 1)
    return {"scores": scores, "avg": avg, "flag": data.get("flag", "PASS")}
