"""Meta deploy, post-deploy intelligence, optimisation, recommendations, webhook."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..config import REFERENCE_IMAGES_DIR
from ..models import (AdvantageToggleReq, ApplyRecommendationReq, CboToggleReq,
                      MetaOptimizeReq)
from ..services import campaign_service as cs
from ..services import crm_service
from ..services import deploy_service as ds
from ..services import image_service as imgs
from ..state import RUNS, RUNS_LOCK, save_runs

router = APIRouter()


@router.post("/deploy-to-meta/{run_id}")
def deploy_to_meta(run_id: str):
    """Deploy selected variants to Meta Ads as OUTCOME_LEADS campaigns (all PAUSED)."""
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    brief = run.get("brief", {})

    meta_copy = cs.effective_meta(review_folder)
    selected = run.get("selected_variants") or sorted(meta_copy.keys())
    selected = [v for v in selected if v in meta_copy]
    if not selected:
        raise HTTPException(status_code=400, detail="No ad copy variants found in review folder.")

    from pikorua_adflow.tools.meta_tool import create_campaign, deploy_ad

    campaign_name = brief.get("property_name", "Pikorua Campaign")
    city = brief.get("city", "India")
    landing_page_url = brief.get("landing_page_url", "https://pikorua.in/")
    daily_budget_inr = int(brief.get("daily_budget_inr", 1000))
    cta = brief.get("cta", "GET_QUOTE")

    from pikorua_adflow.tools import meta_targeting as _mt
    audience = cs.effective_audience(review_folder, brief)
    targeting_spec = _mt.build_targeting_spec(audience)
    audience_label = _mt.audience_summary(audience)
    end_time = audience.get("end_time", "")

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    shared_campaign_id = ""
    _token = os.getenv("META_ACCESS_TOKEN", "")
    if not dry_run:
        _account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
        try:
            shared_campaign_id = create_campaign(campaign_name=campaign_name, token=_token, ad_account_id=_account_id)
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log("Meta deploy — create campaign", exc)
            return {"run_id": run_id, "deployed": [],
                    "errors": [{"variant": None, "error": friendly["message"], "fixable": friendly["fixable"]}],
                    "dropped_locations": []}

    results = []
    errors = []
    for variant_num in selected:
        copy = meta_copy.get(variant_num, {})
        headline = copy.get("headline", "")
        body_text = copy.get("body", "")
        _v_edits = cs.load_edits(review_folder).get("meta", {}).get(str(variant_num), {})
        _assigned = _v_edits.get("image_num")
        if _assigned and (review_folder / "images" / f"image_{_assigned}.png").exists():
            image_path = review_folder / "images" / f"image_{_assigned}.png"
        elif (review_folder / "images" / f"image_{variant_num}.png").exists():
            image_path = review_folder / "images" / f"image_{variant_num}.png"
        else:
            image_path = None
        try:
            result = deploy_ad(
                variant=variant_num, headline=headline, body=body_text, image_path=image_path,
                campaign_name=campaign_name, city=city, landing_page_url=landing_page_url,
                daily_budget_inr=daily_budget_inr, cta=cta, targeting_spec=targeting_spec,
                audience_label=audience_label, end_time=end_time, campaign_id=shared_campaign_id,
            )
            results.append(result)
            if not dry_run and image_path and image_path.exists():
                try:
                    import shutil as _shutil
                    REFERENCE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                    dest = REFERENCE_IMAGES_DIR / f"published_{run_id}_v{variant_num}.png"
                    _shutil.copy2(image_path, dest)
                    imgs.analyze_reference_image(dest)
                except Exception:
                    pass
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log(f"Meta deploy — variant {variant_num}", exc)
            errors.append({"variant": variant_num, "error": friendly["message"], "fixable": friendly["fixable"]})

    if shared_campaign_id and not results:
        from pikorua_adflow.tools.meta_tool import _delete
        _delete(shared_campaign_id, _token)

    dropped = sorted({loc for r in results for loc in r.get("dropped_locations", [])})

    with RUNS_LOCK:
        if results:
            RUNS[run_id]["meta_ads"] = results
        if errors:
            RUNS[run_id]["meta_deploy_errors"] = errors
        if dropped:
            RUNS[run_id]["meta_dropped_locations"] = dropped
        else:
            RUNS[run_id].pop("meta_dropped_locations", None)
    save_runs()
    return {"run_id": run_id, "deployed": results, "errors": errors, "dropped_locations": dropped}


@router.get("/meta-previews/{run_id}")
def meta_previews(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    ads = ds.real_meta_ads(run)
    if not ads:
        return {"previews": [], "note": "No live ads to preview yet."}
    from pikorua_adflow.tools.meta_tool import fetch_ad_previews
    token = os.getenv("META_ACCESS_TOKEN", "")
    out = []
    for a in ads:
        previews = fetch_ad_previews(a["ad_id"], token)
        out.append({"variant": a.get("variant"), "previews": previews})
    return {"previews": out}


@router.get("/meta-signals/{run_id}")
def meta_signals(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    ads = ds.real_meta_ads(run)
    if not ads:
        return {"reach": {}, "delivery": [], "note": "No live ad sets to estimate yet."}
    from pikorua_adflow.tools import meta_targeting as _mt
    from pikorua_adflow.tools.meta_tool import fetch_delivery_estimate, fetch_reach_estimate
    rf = Path(run["review_folder"])
    brief = run.get("brief", {})
    audience = cs.effective_audience(rf, brief)
    spec = _mt.build_targeting_spec(audience)
    token = os.getenv("META_ACCESS_TOKEN", "")
    account = os.getenv("META_AD_ACCOUNT_ID", "")
    reach = fetch_reach_estimate(account, spec, token)
    mau = reach.get("estimate_mau", 0)
    label, color = ds.reach_status(mau)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_delivery(a: dict) -> dict:
        de = fetch_delivery_estimate(a["adset_id"], token)
        daily = ""
        curve = de.get("daily_outcomes_curve") or []
        if curve:
            reaches = [pt.get("reach", 0) for pt in curve if pt.get("reach")]
            if reaches:
                daily = f"{min(reaches):,}–{max(reaches):,}/day"
        return {"variant": a.get("variant"), "daily_range": daily,
                "estimate_ready": de.get("estimate_ready", False)}

    with ThreadPoolExecutor(max_workers=len(ads) or 1) as pool:
        futures = {pool.submit(_fetch_delivery, a): a for a in ads}
        results = {f.result()["variant"]: f.result() for f in as_completed(futures)}
    delivery = [results[a.get("variant")] for a in ads if a.get("variant") in results]

    return {
        "reach": {"estimate_mau": mau, "estimate_dau": reach.get("estimate_dau", 0),
                  "status_label": label, "color": color,
                  "audience_summary": _mt.audience_summary(audience)},
        "delivery": delivery,
    }


@router.get("/meta-performance/{run_id}")
def meta_performance(run_id: str):
    """Per-variant performance + Meta-signal and CRM-driven optimisation chips."""
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    ads = ds.real_meta_ads(run)
    if not ads:
        return {"variants": [], "crm_signals": [], "note": "Publish live ads first to see performance."}

    from pikorua_adflow.tools import meta_targeting as _mt
    from pikorua_adflow.tools.meta_tool import (fetch_insights, fetch_reach_estimate,
                                                fetch_relevance_diagnostics)
    from pikorua_adflow.analytics import optimization_tracker as _tracker
    token = os.getenv("META_ACCESS_TOKEN", "")
    account = os.getenv("META_AD_ACCOUNT_ID", "")
    rf = Path(run["review_folder"])
    brief = run.get("brief", {})
    base_audience = cs.effective_audience(rf, brief)
    base_budget = int(brief.get("daily_budget_inr", 1000))

    reach = fetch_reach_estimate(account, _mt.build_targeting_spec(base_audience), token)
    reach_mau = reach.get("estimate_mau", 0)
    base_spec = _mt.build_targeting_spec(base_audience)
    diagnostics = fetch_relevance_diagnostics([a["ad_id"] for a in ads], token)

    def _spec_with(**changes) -> dict:
        aud = dict(base_audience)
        if "radius_delta" in changes:
            cur = int(aud.get("radius_km") or _mt.DEFAULT_RADIUS_KM)
            aud["radius_km"] = max(_mt._RADIUS_MIN_KM, min(_mt._RADIUS_MAX_KM, cur + changes["radius_delta"]))
        if changes.get("add_nri"):
            aud["nri_countries"] = list(dict.fromkeys((aud.get("nri_countries") or []) + ["AE", "US", "GB"]))
        if changes.get("add_interests"):
            resolved = list(aud.get("interests") or [])
            for nm in changes["add_interests"]:
                try:
                    hits = _mt.search_interests(nm, token, limit=1)
                except Exception:
                    hits = []
                if hits:
                    resolved.append({"id": hits[0]["id"], "name": hits[0]["name"]})
            aud["interests"] = resolved
        return _mt.build_targeting_spec(aud)

    def _attach_impact(rec: dict) -> None:
        if rec["action"] == "targeting" and rec["params"].get("targeting_spec"):
            ts = rec["params"]["targeting_spec"]
            basis, raw = ds.targeting_basis(base_spec, ts)
            pred = _tracker.predict(basis, raw, reach_mau)
            has_custom = bool(ts.get("custom_audiences") or ts.get("excluded_custom_audiences"))
            rec["impact"] = {
                "metric": "reach", "before": reach_mau, "measurable_now": True, **pred,
                **({"custom_audience_note": "Custom audience adds ad relevance — actual CPL "
                    "improvement may exceed reach estimate"} if has_custom else {}),
            }
        elif rec["action"] == "budget":
            new_b = int(rec["params"].get("daily_budget_inr", base_budget))
            pred = _tracker.predict("budget_linear", new_b / max(base_budget, 1), None)
            rec["impact"] = {"metric": "leads", "before": None, "measurable_now": False, **pred}

    campaign_recs: list[dict] = []
    first_variant = ads[0].get("variant", 1) if ads else 1
    if reach_mau and reach_mau < 100_000:
        rec = {"source": "meta", "action": "targeting", "severity": "red",
               "label": "Broaden audience (+15km)",
               "detail": f"Audience is only ~{reach_mau:,} people — widen the radius for all variants.",
               "params": {"targeting_spec": _spec_with(radius_delta=15)}, "apply_to_variant": first_variant}
        _attach_impact(rec)
        campaign_recs.append(rec)
    elif reach_mau and reach_mau > 4_000_000:
        rec = {"source": "meta", "action": "targeting", "severity": "amber",
               "label": "Narrow audience (−10km)",
               "detail": f"Audience is ~{reach_mau:,} people — tighten the radius for all variants.",
               "params": {"targeting_spec": _spec_with(radius_delta=-10)}, "apply_to_variant": first_variant}
        _attach_impact(rec)
        campaign_recs.append(rec)

    _raw: list[dict] = []
    for a in ads:
        insights = fetch_insights(a["ad_id"], token)
        metrics = ds.metrics_from_insight(insights[0]) if insights else {}
        _raw.append({"variant": a.get("variant"), "ad_id": a["ad_id"],
                     "adset_id": a.get("adset_id", ""), "metrics": metrics,
                     "diag": diagnostics.get(a["ad_id"], {})})

    _with_spend = [r for r in _raw if (r["metrics"].get("impressions") or 0) > 0]
    avg_cpl = avg_ctr = None
    best_cpl_v = best_ctr_v = None
    if len(_with_spend) >= 2:
        cpl_pairs = [(r["variant"], r["metrics"]["cpl"]) for r in _with_spend if r["metrics"].get("cpl") is not None]
        ctr_pairs = [(r["variant"], float(r["metrics"].get("ctr") or 0)) for r in _with_spend]
        if cpl_pairs:
            avg_cpl = sum(c[1] for c in cpl_pairs) / len(cpl_pairs)
            best_cpl_v = min(cpl_pairs, key=lambda x: x[1])[0]
        if ctr_pairs:
            avg_ctr = sum(c[1] for c in ctr_pairs) / len(ctr_pairs)
            best_ctr_v = max(ctr_pairs, key=lambda x: x[1])[0]

    variants_out: list[dict] = []
    for r in _raw:
        vnum = r["variant"]
        metrics = r["metrics"]
        quality = r["diag"].get("quality_ranking", "")
        cpl = metrics.get("cpl")
        ctr = float(metrics.get("ctr") or 0)
        freq = float(metrics.get("frequency") or 0)
        has_spend = (metrics.get("impressions") or 0) > 0
        recs: list[dict] = []
        rank_label: str | None = None
        cpl_rec_added = False

        if len(_with_spend) >= 2 and has_spend:
            if avg_cpl is not None and cpl is not None:
                if cpl > 2.0 * avg_cpl:
                    recs.append({"source": "comparative", "action": "pause", "severity": "red",
                                 "label": f"₹{round(cpl)} CPL — {round(cpl / avg_cpl, 1)}× campaign average",
                                 "detail": (f"V{vnum} costs ₹{round(cpl)} per enquiry vs ₹{round(avg_cpl)} average."
                                            + (f" Reallocate budget to V{best_cpl_v}." if best_cpl_v and best_cpl_v != vnum else "")),
                                 "params": {}})
                    rank_label = "Underperforming"
                    cpl_rec_added = True
                elif cpl < 0.65 * avg_cpl and quality in ("ABOVE_AVERAGE",):
                    recs.append({"source": "comparative", "action": "budget", "severity": "green",
                                 "label": "Best CPL — scale up 20%",
                                 "detail": f"V{vnum} at ₹{round(cpl)}/enquiry is {round(avg_cpl / cpl, 1)}× better than average.",
                                 "params": {"daily_budget_inr": int(base_budget * 1.2), "base_budget": base_budget}})
                    rank_label = "Top performer"
                    cpl_rec_added = True
            if avg_ctr is not None and avg_ctr > 0 and not rank_label and ctr > 0:
                if ctr < 0.5 * avg_ctr and vnum != best_ctr_v:
                    recs.append({"source": "comparative", "action": "note", "severity": "amber",
                                 "label": f"CTR {round(ctr, 2)}% — {round(avg_ctr / ctr, 1)}× below average",
                                 "detail": (f"V{vnum} click-through ({ctr}%) is well below the campaign average "
                                            f"({round(avg_ctr, 2)}%). Swap the image or headline."),
                                 "params": {}})

        if quality in ("BELOW_AVERAGE_10", "BELOW_AVERAGE"):
            recs.append({"source": "meta", "action": "note", "severity": "amber", "label": "Swap the creative",
                         "detail": "Quality ranking is below average — try a fresh image/headline "
                                   "from the Image Prompts tab, then re-publish.", "params": {}})
        if freq > 3.0:
            recs.append({"source": "meta", "action": "targeting", "severity": "amber",
                         "label": "Expand to NRI countries",
                         "detail": f"Frequency is {freq} — the same people are seeing it too often. Widen the audience.",
                         "params": {"targeting_spec": _spec_with(add_nri=True)}})
        if cpl is not None and cpl > 500 and not cpl_rec_added:
            recs.append({"source": "meta", "action": "pause", "severity": "red", "label": "Pause this variant",
                         "detail": f"Cost per enquiry is ₹{cpl} — above the ₹500 ceiling.", "params": {}})
        if quality in ("ABOVE_AVERAGE",) and cpl is not None and cpl < 300 and not cpl_rec_added:
            recs.append({"source": "meta", "action": "budget", "severity": "green", "label": "Scale up 20%",
                         "detail": f"Strong quality and ₹{cpl} per enquiry — give it more budget.",
                         "params": {"daily_budget_inr": int(base_budget * 1.2), "base_budget": base_budget}})

        for rec in recs:
            _attach_impact(rec)
        variants_out.append({"variant": vnum, "ad_id": r["ad_id"], "adset_id": r["adset_id"],
                             "metrics": metrics, "diagnostics": r["diag"],
                             "recommendations": recs, "rank_label": rank_label})

    return {"variants": variants_out, "campaign_recs": campaign_recs,
            "crm_signals": crm_service.crm_optimisation_signals(),
            "reach_mau": reach_mau, "learning": _tracker.history(run_id)}


@router.post("/meta-optimize/{run_id}")
def meta_optimize(run_id: str, req: MetaOptimizeReq):
    """Apply one optimisation action to a published variant.
    variant=0 means apply to all live variants (targeting/budget changes only).
    """
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    lookup = ds.variant_lookup(run)

    # variant=0 → apply to all live variants; resolve to a list
    if req.variant == 0:
        all_ads = list(lookup.values())
        if not all_ads:
            raise HTTPException(status_code=404, detail="No live variants found for this run.")
        # Apply to each variant; collect results; surface any errors
        results = []
        for _ad in all_ads:
            _req = req.model_copy(update={"variant": _ad["variant"]})
            try:
                results.append(meta_optimize(run_id, _req))
            except HTTPException as _he:
                results.append({"ok": False, "variant": _ad["variant"], "error": _he.detail})
        return {"ok": True, "applied_to": [r.get("variant", r) for r in results], "results": results}

    ad = lookup.get(req.variant)
    if not ad:
        raise HTTPException(status_code=404, detail=f"Version {req.variant} isn't published live.")

    from pikorua_adflow.tools import meta_tool as _mtool
    from pikorua_adflow.tools.errors import explain_and_log
    from pikorua_adflow.analytics import optimization_tracker as _tracker
    token = os.getenv("META_ACCESS_TOKEN", "")
    account = os.getenv("META_AD_ACCOUNT_ID", "")
    ad_id = ad["ad_id"]
    adset_id = ad.get("adset_id", "")
    impact = None

    try:
        if req.action == "pause":
            _mtool.pause_variant(ad_id, token)
            ad["status"] = "PAUSED"
        elif req.action == "resume":
            _mtool.resume_variant(ad_id, token)
            ad["status"] = "ACTIVE"
        elif req.action == "add_interests":
            from pikorua_adflow.tools import meta_targeting as _mt
            from pikorua_adflow.tools.meta_tool import fetch_reach_estimate
            rf = Path(run["review_folder"])
            brief = run.get("brief", {})
            base_audience = dict(cs.effective_audience(rf, brief))
            special = req.params.get("action", "")
            interests = req.params.get("add_interests", req.params.get("interests", []))
            if special == "add_nri":
                base_audience["nri_countries"] = list(dict.fromkeys((base_audience.get("nri_countries") or []) + ["AE", "US", "GB"]))
                label = "Expand to NRI countries (UAE/US/GB)"
            elif special == "broaden_radius":
                cur = int(base_audience.get("radius_km") or _mt.DEFAULT_RADIUS_KM)
                base_audience["radius_km"] = min(_mt._RADIUS_MAX_KM, cur + 15)
                label = "Broaden geo radius +15km"
            elif interests:
                resolved = list(base_audience.get("interests") or [])
                for nm in interests:
                    try:
                        hits = _mt.search_interests(nm, token, limit=1)
                    except Exception:
                        hits = []
                    if hits:
                        resolved.append({"id": hits[0]["id"], "name": hits[0]["name"]})
                base_audience["interests"] = resolved
                label = "Add interests: " + ", ".join(interests)
            else:
                raise HTTPException(status_code=400, detail="No interests supplied.")
            spec = _mt.build_targeting_spec(base_audience)
            live_spec = ds.live_adset_targeting(adset_id, token)
            before_reach = 0
            try:
                before_reach = fetch_reach_estimate(account, live_spec if live_spec else spec, token).get("estimate_mau", 0)
            except Exception:
                pass
            basis, raw = ds.targeting_basis(live_spec if live_spec else spec, spec)
            pred = _tracker.predict(basis, raw, before_reach)
            rid = _tracker.open_record(run_id=run_id, variant=req.variant, action="targeting",
                                       basis=basis, metric="reach", label=label,
                                       before=before_reach, raw_multiplier=raw, expected=pred)
            apply_error = None
            try:
                _mtool.update_adset_targeting(adset_id, spec, token)
            except Exception as _ae:
                from pikorua_adflow.tools.errors import humanize as _humanize
                apply_error = _humanize(_ae)["message"]
            after_reach = None
            if not apply_error:
                try:
                    after_reach = fetch_reach_estimate(account, spec, token).get("estimate_mau", 0)
                except Exception:
                    pass
            if after_reach is not None:
                _tracker.settle(rid, after_reach)
            impact = {"metric": "reach", "measurable_now": True, "before": before_reach,
                      "actual_after": after_reach, "predicted_pct": pred["expected_pct"], "apply_error": apply_error}
        elif req.action == "budget":
            budget = int(req.params.get("daily_budget_inr", 0))
            if not budget and req.params.get("change_pct") is not None:
                live_base = ds.live_adset_budget_inr(adset_id, token)
                base = live_base if live_base else int(run.get("brief", {}).get("daily_budget_inr", 1000))
                budget = round(base * (1 + float(req.params["change_pct"]) / 100))
            if budget <= 0:
                raise HTTPException(status_code=400, detail="No budget supplied.")
            pred = _tracker.predict("budget_linear", budget / max(int(req.params.get("base_budget", budget)), 1), None)
            _tracker.open_record(run_id=run_id, variant=req.variant, action="budget", basis="budget_linear",
                                 metric="leads", label=req.params.get("label", "Adjust budget"),
                                 before=None, raw_multiplier=pred["raw_multiplier"], expected=pred)
            _mtool.update_adset_budget(adset_id, budget, token)
            ad["daily_budget_inr"] = budget
            impact = {"metric": "leads", "measurable_now": False, "predicted_pct": pred["expected_pct"],
                      "note": "Effect on enquiries shows once the ad runs."}
        elif req.action == "targeting":
            spec = req.params.get("targeting_spec")
            if not spec:
                raise HTTPException(status_code=400, detail="No targeting supplied.")
            live_spec = ds.live_adset_targeting(adset_id, token)
            before_reach = 0
            try:
                est = _mtool.fetch_reach_estimate(account, live_spec if live_spec else spec, token)
                before_reach = est.get("estimate_mau", 0)
            except Exception:
                pass
            basis_hint = req.params.get("basis_hint", "")
            raw_hint = req.params.get("raw_multiplier_hint")
            if basis_hint and raw_hint is not None:
                basis, raw = basis_hint, float(raw_hint)
            else:
                basis, raw = ds.targeting_basis(live_spec if live_spec else spec, spec)
            pred = _tracker.predict(basis, raw, before_reach)
            rid = _tracker.open_record(run_id=run_id, variant=req.variant, action="targeting",
                                       basis=basis, metric="reach", label=req.params.get("label", "Adjust targeting"),
                                       before=before_reach, raw_multiplier=raw, expected=pred)
            apply_error = None
            try:
                _mtool.update_adset_targeting(adset_id, spec, token)
            except Exception as _ae:
                from pikorua_adflow.tools.errors import humanize as _humanize
                apply_error = _humanize(_ae)["message"]
            after_reach = None
            if not apply_error:
                try:
                    after_reach = _mtool.fetch_reach_estimate(account, spec, token).get("estimate_mau", 0)
                except Exception:
                    after_reach = None
            settled = _tracker.settle(rid, after_reach) if (after_reach is not None) else None
            impact = {"metric": "reach", "measurable_now": True, "before": before_reach,
                      "actual_after": after_reach, "predicted_after": pred["expected_after"],
                      "predicted_pct": pred["expected_pct"], "actual_pct": (settled or {}).get("actual_pct"),
                      "prediction_error_pp": (settled or {}).get("prediction_error_pp"),
                      "basis": basis, "n_samples": pred["n_samples"], "apply_error": apply_error}
        elif req.action == "swap_creative":
            spec = req.params.get("object_story_spec")
            if not spec:
                raise HTTPException(status_code=400, detail="No creative supplied.")
            result = _mtool.swap_ad_creative(ad_id, account, spec, token)
            ad["creative_id"] = result["creative_id"]
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action '{req.action}'.")
    except HTTPException:
        raise
    except Exception as exc:
        friendly = explain_and_log(f"Meta optimise — {req.action} V{req.variant}", exc)
        raise HTTPException(status_code=400, detail=friendly["message"])

    RUNS[run_id]["meta_ads"] = run.get("meta_ads", [])
    save_runs()
    return {"ok": True, "action": req.action, "variant": req.variant, "impact": impact}


@router.get("/meta-optimize-history/{run_id}")
def meta_optimize_history(run_id: str):
    from pikorua_adflow.analytics import optimization_tracker as _tracker
    return _tracker.history(run_id)


@router.get("/meta-recommendations/{run_id}")
def meta_recommendations_endpoint(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    ads = ds.real_meta_ads(run)
    if not ads:
        return {"recommendations": [], "note": "Publish live ads first to fetch recommendations."}
    token = os.getenv("META_ACCESS_TOKEN", "")
    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    if not token or not ad_account_id:
        raise HTTPException(status_code=503, detail="META credentials not configured.")
    from pikorua_adflow.tools.meta_tool import _get as _mt_get, fetch_recommendations
    adset_ids = [a["adset_id"] for a in ads if a.get("adset_id")]
    campaign_ids = list({a["campaign_id"] for a in ads if a.get("campaign_id")})
    recs = fetch_recommendations(ad_account_id, token, adset_ids)
    advantage_on = False
    if adset_ids:
        try:
            td = _mt_get(adset_ids[0], token, {"fields": "targeting_automation"})
            advantage_on = td.get("targeting_automation", {}).get("advantage_audience", 0) == 1
        except Exception:
            pass
    cbo_on = False
    if campaign_ids:
        try:
            cd = _mt_get(campaign_ids[0], token, {"fields": "is_adset_budget_sharing_enabled"})
            cbo_on = bool(cd.get("is_adset_budget_sharing_enabled", False))
        except Exception:
            pass
    return {"recommendations": recs, "advantage_audience_on": advantage_on, "cbo_on": cbo_on,
            "adset_ids": adset_ids, "campaign_ids": campaign_ids}


@router.post("/meta-apply-recommendation/{run_id}")
def meta_apply_recommendation(run_id: str, req: ApplyRecommendationReq):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    from pikorua_adflow.tools.meta_tool import apply_recommendation
    ok, data = apply_recommendation(req.recommendation_id, token)
    if ok:
        return {"ok": True}
    err = data.get("error", data)
    raise HTTPException(status_code=400, detail=err.get("message", json.dumps(err)))


@router.post("/meta-toggle-advantage/{run_id}")
def meta_toggle_advantage(run_id: str, req: AdvantageToggleReq):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    from pikorua_adflow.tools.meta_tool import toggle_advantage_audience
    ok = toggle_advantage_audience(req.adset_id, req.enable, token)
    if ok:
        return {"ok": True, "advantage_audience": req.enable}
    raise HTTPException(status_code=400, detail="Failed to toggle Advantage+ Audience.")


@router.post("/meta-toggle-cbo/{run_id}")
def meta_toggle_cbo(run_id: str, req: CboToggleReq):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set.")
    from pikorua_adflow.tools.meta_tool import toggle_cbo
    ok = toggle_cbo(req.campaign_id, req.enable, token)
    if ok:
        return {"ok": True, "cbo": req.enable}
    raise HTTPException(status_code=400, detail="Failed to toggle CBO.")


# ── Meta lead-form webhook ───────────────────────────────────────────────────
@router.get("/meta-lead-webhook")
async def meta_lead_webhook_verify(request: Request):
    p = request.query_params
    verify_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "")
    if not verify_token:
        raise HTTPException(status_code=500, detail="META_WEBHOOK_VERIFY_TOKEN not set in .env")
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == verify_token and p.get("hub.challenge"):
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/meta-lead-webhook")
async def meta_lead_webhook_receive(request: Request):
    from pikorua_adflow.tools.errors import explain_and_log
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "ok"})
    token = os.getenv("META_ACCESS_TOKEN", "")
    inserted = []
    errors = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            val = change.get("value", {})
            leadgen_id = str(val.get("leadgen_id", ""))
            if not leadgen_id:
                continue
            try:
                raw = crm_service.fetch_lead_fields(leadgen_id, token)
            except Exception as exc:
                errors.append(f"Graph API fetch failed for {leadgen_id}: {exc}")
                continue
            field_map: dict[str, str] = {}
            for fd in raw.get("field_data", []):
                name = (fd.get("name") or "").lower().replace(" ", "_")
                values = fd.get("values") or []
                field_map[name] = values[0] if values else ""
            full_name = (
                field_map.get("full_name") or field_map.get("name")
                or field_map.get("first_name", "")
                + (" " + field_map.get("last_name", "") if field_map.get("last_name") else "")
            ).strip()
            phone = (field_map.get("phone_number") or field_map.get("phone")
                     or field_map.get("mobile_number", "")).strip()
            email = field_map.get("email", "").strip()
            city = field_map.get("city", "").strip()
            lead_row = {
                "full_name": full_name or None, "phone": phone or None, "email": email or None,
                "city": city or None,
                "campaign_name": raw.get("campaign_name") or val.get("campaign_id") or None,
                "source": "Meta Lead Form", "status": "Unassigned",
                "received_at": datetime.now(timezone.utc).isoformat(),
                "form_id": str(raw.get("form_id") or val.get("form_id") or ""),
                "ad_id": str(raw.get("ad_id") or val.get("ad_id") or ""),
                "form_data": json.dumps(raw.get("field_data", [])),
            }
            try:
                new_id = crm_service.insert_lead_supabase(lead_row)
                if new_id:
                    inserted.append({"leadgen_id": leadgen_id, "supabase_id": new_id, "name": full_name})
                    crm_service.invalidate_crm_cache()
                else:
                    errors.append(f"Supabase insert returned no ID for {leadgen_id}")
            except Exception as exc:
                explain_and_log(exc, context=f"webhook Supabase insert {leadgen_id}")
                errors.append(str(exc))
    print(f"[webhook] inserted={inserted} errors={errors}")
    return JSONResponse({"status": "ok", "inserted": len(inserted), "errors": errors})
