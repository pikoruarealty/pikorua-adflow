"""
Campaign Autopilot — the self-optimising brain.

This is NOT a second Ads Manager. It reads each ACTIVE Meta campaign, scores its
lead quality against the CRM (the signal Meta is blind to), and walks a decision
ladder where targeting/audience fixes come first and PAUSE is the last resort.

Provably-safe fixes (remove wrong-city geo, wire the CRM bad-leads exclusion, add a
same-clientele Lookalike, enable Advantage+ on saturation) apply automatically and
are logged with an Undo. Anything that changes spend or pauses a campaign is queued
as a one-click decision for the human.

Cross-campaign learning is CLIENTELE-SCOPED: a bungalow campaign only ever inherits
audiences/interests proven on other bungalow campaigns, never from a flat campaign —
the buyers are different people.

State (applied log, cooldowns, last digest) persists in outputs/autopilot_state.json.
All Meta writes go through pikorua_adflow.tools.meta_tool; predictions are tracked by
analytics.optimization_tracker so estimates self-calibrate over time.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import AUDIENCES_REGISTRY_PATH, OUTPUT_DIR
from ..state import RUNS, RUNS_LOCK

# ── Tunables ──────────────────────────────────────────────────────────────────
BENCHMARK_CPL = 85          # best-ever CPL (₹) — the anchor the autopilot aims at
FREQ_SATURATED = 3.0        # Meta audience-fatigue threshold
FREQ_EXHAUSTED = 5.0        # pause only considered above this
CPL_CEILING = 500           # ₹ — a campaign above this is bleeding
CPL_RISING_RATIO = 1.3      # 7d/30d CPL ratio that counts as "getting worse"
QUALITY_LEAD_MIN = 5        # matched quality leads needed to trust real quality-CPL
COOLDOWN_DAYS = 5           # wait for Meta's learning phase before stacking changes

_STATE_PATH = OUTPUT_DIR / "autopilot_state.json"
_NRI_DEFAULT = ["AE", "GB", "US", "SG"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── State ─────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        data.setdefault("campaigns", {})   # campaign_id -> {applied: [...], cooldowns: {fix_type: iso}}
        data.setdefault("last_run", None)
        return data
    except Exception:
        return {"campaigns": {}, "last_run": None}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _camp_state(state: dict, campaign_id: str) -> dict:
    return state["campaigns"].setdefault(campaign_id, {"applied": [], "cooldowns": {}})


def _in_cooldown(camp_state: dict, fix_type: str) -> bool:
    iso = camp_state.get("cooldowns", {}).get(fix_type)
    if not iso:
        return False
    try:
        when = datetime.fromisoformat(iso)
        return (_now() - when) < timedelta(days=COOLDOWN_DAYS)
    except Exception:
        return False


# ── Audience registry (exclusion / lookalike ids) ─────────────────────────────
def _registry() -> dict:
    """Return {exclusion_id, lookalike_id} from the CRM-audience registry, or {}."""
    try:
        rows = json.loads(AUDIENCES_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for r in rows:
        role = r.get("role")
        if role == "exclusion" and "exclusion_id" not in out:
            out["exclusion_id"] = r["id"]
        elif role == "lookalike" and "lookalike_id" not in out:
            out["lookalike_id"] = r["id"]
    return out


# ── Brief / clientele lookup ──────────────────────────────────────────────────
def _run_for_campaign(campaign_name: str) -> tuple[str | None, dict | None]:
    """Find the (run_id, brief) of the tool-created run whose property_name matches
    this Meta campaign. Prefers the most recent match. (None, None) if no match —
    e.g. a legacy campaign created outside the tool."""
    with RUNS_LOCK:
        runs = list(RUNS.items())
    cn = (campaign_name or "").strip().lower()
    best = None  # (created_at, run_id, brief)
    for rid, run in runs:
        b = run.get("brief") or {}
        pn = (b.get("property_name") or "").strip().lower()
        if pn and (pn in cn or cn in pn):
            if best is None or (run.get("created_at", "") > best[0]):
                best = (run.get("created_at", ""), rid, b)
    return (best[1], best[2]) if best else (None, None)


def _brief_for_campaign(campaign_name: str) -> dict | None:
    """Brief for a Meta campaign (or None). Thin wrapper over _run_for_campaign."""
    return _run_for_campaign(campaign_name)[1]


# ── Health from insights ──────────────────────────────────────────────────────
def _campaign_metrics(campaign_id: str, token: str) -> dict:
    """7d + 30d headline metrics for a campaign (frequency, cpl, ctr, spend, leads)."""
    from pikorua_adflow.tools.meta_tool import fetch_insights
    from . import deploy_service as ds

    def _agg(rows: list[dict]) -> dict:
        # campaign insights at level=ad → sum across ads
        spend = leads = impressions = 0.0
        ctr_w = 0.0
        freq_max = 0.0
        for row in rows:
            m = ds.metrics_from_insight(row)
            spend += m["spend"]
            leads += m["leads"]
            impressions += m["impressions"]
            ctr_w += (m["ctr"] * m["impressions"])
            freq_max = max(freq_max, m["frequency"])
        cpl = round(spend / leads, 1) if leads else None
        ctr = round(ctr_w / impressions, 2) if impressions else 0.0
        return {"spend": round(spend, 1), "leads": int(leads), "cpl": cpl,
                "ctr": ctr, "frequency": round(freq_max, 2), "impressions": int(impressions)}

    d7 = _agg(fetch_insights(campaign_id, token, "last_7d"))
    d30 = _agg(fetch_insights(campaign_id, token, "last_30d"))
    rising = bool(d7["cpl"] and d30["cpl"] and d7["cpl"] / d30["cpl"] > CPL_RISING_RATIO)
    return {"d7": d7, "d30": d30, "cpl_rising": rising}


# ── Adaptive north-star ─────────────────────────────────────────────────────
def adaptive_quality(campaign_name: str, spend_7d: float, leads_7d: int,
                     crm_leads: list[dict], crm_report: dict) -> dict:
    """
    Pick the quality metric for a campaign.

    Real quality-CPL (₹ per exploring/warm/hot CRM lead) once >= QUALITY_LEAD_MIN
    matched quality leads exist; otherwise the profile-match score (how much the
    campaign's leads resemble the best historical converters). Returns a dict the
    UI renders verbatim.
    """
    from pikorua_adflow.analytics import crm_analytics as ca

    matched = ca.match_meta_leads(crm_leads, campaign_name)
    n_quality = sum(1 for r in matched if ca._is_quality(ca._normalize([r])[0]))

    if n_quality >= QUALITY_LEAD_MIN and spend_7d > 0:
        quality_cpl = round(spend_7d / n_quality)
        return {
            "metric_used": "quality_cpl",
            "label": "Cost per quality lead",
            "value": quality_cpl, "unit": "₹",
            "n_quality": n_quality, "n_matched": len(matched),
            "building": False,
        }

    profiles = crm_report.get("top_profiles", []) if crm_report else []
    pm = ca.profile_match_score(matched, profiles)
    # Plain CPL still shown as the headline number while quality scoring builds.
    plain_cpl = round(spend_7d / leads_7d) if leads_7d else None
    return {
        "metric_used": "profile_match",
        "label": "Cost per lead",
        "value": plain_cpl, "unit": "₹",
        "profile_match_pct": pm["score"], "n_quality": n_quality,
        "n_matched": len(matched),
        "building": True,
    }


# ── The decision ladder ───────────────────────────────────────────────────────
def _audience_from_targeting(targeting: dict) -> dict:
    """Reconstruct a light audience dict from a live ad-set targeting spec, so geo
    reach probes layer the campaign's OWN intent/age rather than a generic default."""
    geo = (targeting or {}).get("geo_locations", {}) or {}
    fs = targeting.get("flexible_spec") or [{}]
    cities = geo.get("cities") or []
    return {
        "country": (geo.get("countries") or ["IN"])[0],
        "radius_km": (cities[0].get("radius") if cities else None),
        "age_min": targeting.get("age_min"),
        "age_max": targeting.get("age_max"),
        "interests": (fs[0].get("interests", []) if fs else []),
        "behaviours": (fs[0].get("behaviors", []) if fs else []),
    }


def _ladder(campaign: dict, adsets: list[dict], ads: list[dict], metrics: dict,
            brief: dict | None, registry: dict, camp_state: dict,
            crm_leads: list[dict] | None = None, run_id: str | None = None) -> list[dict]:
    """
    Produce the ranked list of fixes for one campaign. Each fix:
      {id, rung, mode: 'auto'|'approve', title, detail, action, params, fix_type}
    Targeting/audience first, budget next, pause last. Cooldowns suppress repeats.
    """
    crm_leads = crm_leads or []
    fixes: list[dict] = []
    cid = campaign["id"]
    d7, d30 = metrics["d7"], metrics["d30"]
    freq = d7["frequency"]
    cpl = d7["cpl"]
    ctr = d7["ctr"]
    rising = metrics["cpl_rising"]
    # Act on the first live ad set (variants share targeting in this account's setup).
    live_adsets = [a for a in adsets if a.get("effective_status") in ("ACTIVE", None)
                   or a.get("status") == "ACTIVE"] or adsets
    adset = live_adsets[0] if live_adsets else (adsets[0] if adsets else None)
    if not adset:
        return fixes
    adset_id = adset["id"]
    targeting = adset.get("targeting", {}) or {}
    geo = targeting.get("geo_locations", {}) or {}

    def add(fix_type, rung, mode, title, detail, action, params):
        if _in_cooldown(camp_state, fix_type):
            return
        fixes.append({"id": f"{cid}:{fix_type}", "campaign_id": cid,
                      "campaign_name": campaign.get("name", ""), "adset_id": adset_id,
                      "fix_type": fix_type, "rung": rung, "mode": mode,
                      "title": title, "detail": detail, "action": action, "params": params})

    # Rung 1 (APPROVE, NEVER auto) — dynamic geo opportunity.
    # Out-of-city buyers are often the highest-value segment (investors, diaspora,
    # relocators), so geo is NEVER changed automatically. The engine computes live
    # signals (CRM performance + Meta reach) and surfaces two kinds of decision:
    #   • trim — a targeted city with leads but zero quality conversions (review only)
    #   • add  — a high-opportunity city your CRM proves but you're not targeting
    # See analytics.geo_intelligence — there is no hardcoded city-wealth table.
    from pikorua_adflow.analytics import geo_intelligence as _geoi
    base_aud = _audience_from_targeting(targeting)
    all_city_keys = [str(c.get("key")) for c in (geo.get("cities") or [])]
    try:
        georecs = _geoi.geo_recommendations(campaign, targeting, brief, crm_leads,
                                            base_audience=base_aud)
    except Exception:
        georecs = {"trim": [], "add": []}
    for t in georecs.get("trim", []):
        keep = [k for k in all_city_keys if k != t["key"]]
        if not keep:
            continue  # never strip the last targeted city
        add(f"review_geo_{t['key']}", 1, "approve",
            f"Review {t['city']} — leads but no quality enquiry yet",
            t["reason"],
            "remove_geo", {"adset_id": adset_id, "keep_city_keys": keep,
                           "keep_countries": geo.get("countries", [])})
    for a in georecs.get("add", []):
        slug = a["city"].lower().replace(" ", "_")
        add(f"add_geo_{slug}", 1, "approve",
            f"Add {a['city']} — proven buyers you're not targeting",
            a["reason"],
            "add_geo_city", {"adset_id": adset_id, "city_name": a["city"]})

    # Rung 2 (AUTO) — wire the CRM bad-leads exclusion if it exists and isn't attached.
    excl_id = registry.get("exclusion_id")
    attached_excl = {a.get("id") for a in (targeting.get("excluded_custom_audiences") or [])}
    if excl_id and excl_id not in attached_excl:
        add("add_exclusion", 2, "auto",
            "Stop wasting budget on known dead-end leads",
            "Your CRM has a list of people who said they're not interested. "
            "Excluding them from this campaign saves spend.",
            "add_exclusion", {"adset_id": adset_id, "exclude_ids": [excl_id]})

    # Rung 3 (AUTO) — add the CRM Lookalike (include) if not present.
    lal_id = registry.get("lookalike_id")
    attached_inc = {a.get("id") for a in (targeting.get("custom_audiences") or [])}
    if lal_id and lal_id not in attached_inc:
        add("add_lookalike", 3, "auto",
            "Find more buyers like your best leads",
            "Adding a look-alike of your strongest CRM leads helps Meta reach similar buyers.",
            "add_lookalike", {"adset_id": adset_id, "include_ids": [lal_id]})

    # Rung 4 (AUTO) — Advantage+ on saturation.
    adv_on = (targeting.get("targeting_automation", {}) or {}).get("advantage_audience", 0) == 1
    if freq > FREQ_SATURATED and rising and not adv_on:
        add("enable_advantage", 4, "auto",
            "Let Meta find fresh buyers (your local audience is tired)",
            f"The same people have seen this ad {freq}× and the cost is climbing. "
            "Meta's audience expansion brings in new look-alikes.",
            "enable_advantage", {"adset_id": adset_id})

    # Rung 5 (APPROVE) — NRI layer.
    countries = set(geo.get("countries", []) or [])
    has_nri = bool(countries - {"IN"})
    if freq > FREQ_SATURATED and rising and not has_nri:
        add("add_nri", 5, "approve",
            "Bring in NRI buyers (UAE / UK / US / Singapore)",
            f"Local buyers are saturated (seen {freq}×). NRI diaspora is a fresh, "
            "high-value audience for this price tier.",
            "add_nri", {"adset_id": adset_id, "iso_codes": _NRI_DEFAULT})

    # Rung 6 (APPROVE) — broaden radius.
    cur_radius = (geo.get("cities") or [{}])[0].get("radius") if geo.get("cities") else None
    if freq > FREQ_SATURATED and cur_radius and cur_radius < 35:
        add("broaden_radius", 6, "approve",
            "Widen the search area by 15 km",
            f"The audience within {cur_radius} km is getting over-shown. A wider radius "
            "reaches more of the right buyers.",
            "broaden_radius", {"adset_id": adset_id, "delta_km": 15})

    # Rung 7 (APPROVE) — interests from CRM top profiles (same clientele only).
    n_interests = len((targeting.get("flexible_spec") or [{}])[0].get("interests", [])) \
        if targeting.get("flexible_spec") else 0
    if n_interests < 5:
        add("add_interests", 7, "approve",
            "Target the buyer profile that actually converts",
            "Your CRM shows which kind of buyer converts best for this type of property. "
            "Add their interests so Meta leans toward them.",
            "add_interests", {"adset_id": adset_id,
                              "clientele_type": (brief or {}).get("clientele_type", "")})

    # Rung 8 (APPROVE) — fresh creative (human makes it).
    stale = ctr and ctr < 0.8
    if stale and rising:
        add("fresh_creative", 8, "approve",
            "Refresh the ad images & copy",
            f"Click-through is low ({ctr}%) and cost is rising — the creative is tired. "
            + ("Open this campaign to regenerate and push fresh creative onto the live ads."
               if run_id else "Generate a new set in Ad Flow."),
            "fresh_creative", {"campaign_id": cid, "run_id": run_id})

    # Rung 9 (APPROVE) — reduce budget to sustainable level.
    if freq > 3.5 and cpl and cpl > CPL_CEILING:
        budget_paise = campaign.get("daily_budget")
        cur_budget = int(int(budget_paise) / 100) if budget_paise else None
        if cur_budget:
            new_budget = max(int(cur_budget * 0.7), 300)
            add("reduce_budget", 9, "approve",
                f"Lower daily spend to ₹{new_budget:,}",
                f"At ₹{cur_budget:,}/day the audience is over-saturated (seen {freq}×) and "
                f"each enquiry costs ₹{round(cpl):,}. A lower budget eases the pressure while "
                "the targeting fixes take effect.",
                "reduce_budget", {"campaign_id": cid, "daily_budget_inr": new_budget,
                                  "base_budget": cur_budget})

    # Rung 10 (APPROVE, LAST RESORT) — pause.
    tried_levers = any(
        camp_state.get("cooldowns", {}).get(ft)
        for ft in ("add_nri", "broaden_radius", "add_interests", "fresh_creative", "reduce_budget")
    )
    if freq > FREQ_EXHAUSTED and cpl and cpl > 2 * BENCHMARK_CPL and tried_levers:
        add("pause", 10, "approve",
            "Pause this campaign (last resort)",
            f"Every targeting and budget lever has been tried, yet the audience is fully "
            f"exhausted (seen {freq}×) and enquiries still cost ₹{round(cpl):,}. Pausing "
            "stops the bleed until a fresh campaign is built.",
            "pause", {"campaign_id": cid})

    fixes.sort(key=lambda f: f["rung"])
    return fixes


# ── Evaluate one campaign ─────────────────────────────────────────────────────
def evaluate_campaign(campaign: dict, token: str, crm_leads: list[dict],
                      crm_report: dict, state: dict) -> dict:
    """Read live state for a campaign and return its fixes + quality + raw numbers."""
    from pikorua_adflow.tools.meta_tool import fetch_campaign_adsets, fetch_ads_with_age

    cid = campaign["id"]
    name = campaign.get("name", "")
    run_id, brief = _run_for_campaign(name)
    adsets = fetch_campaign_adsets(cid, token)
    ads = fetch_ads_with_age(cid, token)
    metrics = _campaign_metrics(cid, token)
    registry = _registry()
    camp_state = _camp_state(state, cid)

    quality = adaptive_quality(name, metrics["d7"]["spend"], metrics["d7"]["leads"],
                               crm_leads, crm_report)
    fixes = _ladder(campaign, adsets, ads, metrics, brief, registry, camp_state,
                    crm_leads=crm_leads, run_id=run_id)
    return {
        "campaign_id": cid, "campaign_name": name, "run_id": run_id,
        "clientele_type": (brief or {}).get("clientele_type", ""),
        "quality": quality, "metrics": metrics, "fixes": fixes,
        "has_brief": brief is not None,
    }


# ── Apply / undo ──────────────────────────────────────────────────────────────
def apply_fix(fix: dict, *, auto: bool = False) -> dict:
    """
    Execute one fix against Meta. Captures an `undo` payload so the action can be
    reverted, logs it to state, and returns {ok, impact, undo_token}.
    """
    from pikorua_adflow.tools import meta_tool as mt
    from pikorua_adflow.tools import meta_targeting as _mt
    from pikorua_adflow.analytics import optimization_tracker as tracker

    action = fix["action"]
    params = fix.get("params", {})

    # fresh_creative is human-in-the-loop: it only returns a deep-link to the campaign
    # page (regenerate + push fresh creative). No Meta write, so no token required.
    if action == "fresh_creative":
        rid = params.get("run_id")
        return {"ok": True, "deep_link": True,
                "impact": {"link": f"/results/{rid}" if rid else "/portal"}}

    token = os.getenv("META_ACCESS_TOKEN", "")
    account = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    if not token:
        return {"ok": False, "error": "META_ACCESS_TOKEN not set."}

    adset_id = params.get("adset_id", "")
    impact: dict = {}
    undo: dict = {}

    try:
        if action == "remove_geo":
            from . import deploy_service as ds
            before = ds.live_adset_targeting(adset_id, token)
            res = mt.remove_geo_locations(adset_id, token,
                                          keep_city_keys=params.get("keep_city_keys"),
                                          keep_countries=params.get("keep_countries"))
            undo = {"action": "restore_targeting", "adset_id": adset_id, "targeting": before}
            impact = {"summary": f"Removed: {', '.join(res.get('removed_cities', []))}"}
        elif action == "add_exclusion":
            from . import deploy_service as ds
            before = ds.live_adset_targeting(adset_id, token)
            mt.add_custom_audiences(adset_id, token, exclude_ids=params.get("exclude_ids"))
            undo = {"action": "restore_targeting", "adset_id": adset_id, "targeting": before}
        elif action == "add_lookalike":
            from . import deploy_service as ds
            before = ds.live_adset_targeting(adset_id, token)
            mt.add_custom_audiences(adset_id, token, include_ids=params.get("include_ids"))
            undo = {"action": "restore_targeting", "adset_id": adset_id, "targeting": before}
        elif action == "enable_advantage":
            mt.toggle_advantage_audience(adset_id, True, token)
            undo = {"action": "toggle_advantage", "adset_id": adset_id, "enable": False}
        elif action == "add_geo_city":
            from . import deploy_service as ds
            before = ds.live_adset_targeting(adset_id, token)
            mt.add_geo_city(adset_id, params["city_name"], token)
            undo = {"action": "restore_targeting", "adset_id": adset_id, "targeting": before}
            impact = {"summary": f"Added {params['city_name']} to the geo"}
        elif action == "add_nri":
            from . import deploy_service as ds
            before = ds.live_adset_targeting(adset_id, token)
            before_reach = mt.fetch_reach_estimate(account, before or {}, token).get("estimate_mau", 0)
            mt.add_geo_countries(adset_id, params.get("iso_codes", _NRI_DEFAULT), token)
            after = ds.live_adset_targeting(adset_id, token)
            after_reach = mt.fetch_reach_estimate(account, after or {}, token).get("estimate_mau", 0)
            undo = {"action": "restore_targeting", "adset_id": adset_id, "targeting": before}
            impact = {"metric": "reach", "before": before_reach, "after": after_reach}
        elif action == "broaden_radius":
            from . import deploy_service as ds
            before = ds.live_adset_targeting(adset_id, token)
            new_t = dict(before)
            cities = (new_t.get("geo_locations", {}) or {}).get("cities") or []
            if cities:
                cur = cities[0].get("radius") or _mt.DEFAULT_RADIUS_KM
                cities[0]["radius"] = min(_mt._RADIUS_MAX_KM, cur + int(params.get("delta_km", 15)))
                new_t.setdefault("geo_locations", {})["cities"] = cities
                mt.update_adset_targeting(adset_id, new_t, token)
            undo = {"action": "restore_targeting", "adset_id": adset_id, "targeting": before}
        elif action == "add_interests":
            from . import deploy_service as ds
            before = ds.live_adset_targeting(adset_id, token)
            new_interests = _resolve_clientele_interests(params.get("clientele_type", ""), token)
            new_t = dict(before)
            fs = new_t.get("flexible_spec") or [{}]
            cur = fs[0].get("interests", []) if fs else []
            ids = {i.get("id") for i in cur}
            fs[0]["interests"] = cur + [i for i in new_interests if i["id"] not in ids]
            new_t["flexible_spec"] = fs
            mt.update_adset_targeting(adset_id, new_t, token)
            undo = {"action": "restore_targeting", "adset_id": adset_id, "targeting": before}
            impact = {"summary": f"Added {len(new_interests)} interests"}
        elif action == "reduce_budget":
            cid = params["campaign_id"]
            # Budget may be at campaign or ad-set level; try ad set first if provided.
            new_b = int(params["daily_budget_inr"])
            target = adset_id or cid
            mt.update_adset_budget(target, new_b, token)
            undo = {"action": "set_budget", "target": target,
                    "daily_budget_inr": params.get("base_budget", new_b)}
            tracker.open_record(run_id=f"autopilot:{cid}", variant=0, action="budget",
                                basis="budget_linear", metric="leads", label="Autopilot budget cut",
                                before=None,
                                raw_multiplier=new_b / max(params.get("base_budget", new_b), 1),
                                expected=tracker.predict("budget_linear",
                                                         new_b / max(params.get("base_budget", new_b), 1), None))
            impact = {"metric": "budget", "after": new_b}
        elif action == "pause":
            mt.toggle_campaign_status(params["campaign_id"], False, token) \
                if hasattr(mt, "toggle_campaign_status") else mt._patch(
                    params["campaign_id"], {"status": "PAUSED"}, token)
            undo = {"action": "resume_campaign", "campaign_id": params["campaign_id"]}
        else:
            return {"ok": False, "error": f"Unknown action '{action}'."}
    except Exception as exc:
        from pikorua_adflow.tools.errors import explain_and_log
        friendly = explain_and_log(f"Autopilot — {action}", exc)
        return {"ok": False, "error": friendly["message"]}

    # Log to state with cooldown.
    state = _load_state()
    cs = _camp_state(state, fix["campaign_id"])
    entry = {
        "fix_type": fix["fix_type"], "title": fix["title"], "detail": fix["detail"],
        "applied_at": _now().isoformat(), "auto": auto, "undo": undo,
        "impact": impact, "undone": False,
    }
    cs["applied"].insert(0, entry)
    cs.setdefault("cooldowns", {})[fix["fix_type"]] = _now().isoformat()
    _save_state(state)
    return {"ok": True, "impact": impact}


def undo_fix(campaign_id: str, fix_type: str) -> dict:
    """Revert the most recent applied fix of this type on a campaign."""
    from pikorua_adflow.tools import meta_tool as mt
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ACCESS_TOKEN not set."}
    state = _load_state()
    cs = _camp_state(state, campaign_id)
    entry = next((e for e in cs["applied"] if e["fix_type"] == fix_type and not e.get("undone")), None)
    if not entry:
        return {"ok": False, "error": "Nothing to undo."}
    undo = entry.get("undo", {})
    try:
        ua = undo.get("action")
        if ua == "restore_targeting":
            mt.update_adset_targeting(undo["adset_id"], undo["targeting"], token)
        elif ua == "toggle_advantage":
            mt.toggle_advantage_audience(undo["adset_id"], undo["enable"], token)
        elif ua == "set_budget":
            mt.update_adset_budget(undo["target"], undo["daily_budget_inr"], token)
        elif ua == "resume_campaign":
            mt._patch(undo["campaign_id"], {"status": "ACTIVE"}, token)
        else:
            return {"ok": False, "error": "This action can't be undone automatically."}
    except Exception as exc:
        from pikorua_adflow.tools.errors import explain_and_log
        friendly = explain_and_log(f"Autopilot undo — {fix_type}", exc)
        return {"ok": False, "error": friendly["message"]}
    entry["undone"] = True
    cs.get("cooldowns", {}).pop(fix_type, None)
    _save_state(state)
    return {"ok": True}


def _resolve_clientele_interests(clientele_type: str, token: str) -> list[dict]:
    """Resolve the clientele's interest names to Meta {id,name} (autopilot rung 7)."""
    from pikorua_adflow.tools import meta_targeting as _mt
    profile = _mt.clientele_profile(clientele_type)
    cache = _mt._load_cache()
    out: list[dict] = []
    for nm in profile["interests"][:6]:
        try:
            hit = _mt._best_interest(nm, token, cache)
        except Exception:
            hit = None
        if hit and hit not in out:
            out.append(hit)
    _mt._save_cache(cache)
    return out


# ── Orchestration ─────────────────────────────────────────────────────────────
def run_autopilot(apply_safe: bool = True) -> dict:
    """
    Evaluate every ACTIVE campaign, auto-apply provably-safe fixes, and build the
    3-zone payload the page renders. Safe to call on a schedule or on tab open.
    """
    token = os.getenv("META_ACCESS_TOKEN", "")
    account = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if not token or not account:
        return {"error": "META credentials not configured.", "campaigns": [],
                "auto_applied": [], "decisions": []}

    from pikorua_adflow.tools.meta_tool import fetch_active_campaigns
    from . import crm_service

    crm_leads, _ = _safe_crm_leads()
    crm_report = _safe_crm_report(crm_service)
    campaigns = fetch_active_campaigns(account, token)
    state = _load_state()

    evals = []
    for c in campaigns:
        try:
            evals.append(evaluate_campaign(c, token, crm_leads, crm_report, state))
        except Exception:
            continue

    auto_applied: list[dict] = []
    decisions: list[dict] = []
    for ev in evals:
        for fix in ev["fixes"]:
            if fix["mode"] == "auto" and apply_safe and not dry_run:
                res = apply_fix(fix, auto=True)
                if res.get("ok"):
                    auto_applied.append({**fix, "impact": res.get("impact", {})})
            elif fix["mode"] == "auto":
                # dry-run: surface what WOULD be auto-applied without calling Meta
                auto_applied.append({**fix, "impact": {"dry_run": True}})
            else:
                decisions.append(fix)

    # Keep "Needs your call" to the highest-impact 2 per the product spec.
    decisions.sort(key=lambda f: f["rung"])
    state["last_run"] = _now().isoformat()
    _save_state(state)

    return {
        "campaigns": [_public_campaign(ev) for ev in evals],
        "auto_applied": auto_applied,
        "decisions": decisions[:2],
        "all_decisions": decisions,
        "last_run": state["last_run"],
        "benchmark_cpl": BENCHMARK_CPL,
    }


def _public_campaign(ev: dict) -> dict:
    """Trim an evaluation to what the page needs (the collapsed 'full numbers')."""
    m = ev["metrics"]["d7"]
    return {
        "campaign_id": ev["campaign_id"], "campaign_name": ev["campaign_name"],
        "clientele_type": ev["clientele_type"], "quality": ev["quality"],
        "spend_7d": m["spend"], "leads_7d": m["leads"], "cpl_7d": m["cpl"],
        "ctr_7d": m["ctr"], "frequency": m["frequency"],
        "cpl_rising": ev["metrics"]["cpl_rising"], "has_brief": ev["has_brief"],
    }


def get_applied_log() -> list[dict]:
    """Flatten the per-campaign applied log (newest first) for the 'What I did' zone."""
    state = _load_state()
    out = []
    for cid, cs in state["campaigns"].items():
        for e in cs.get("applied", []):
            if not e.get("undone"):
                out.append({**e, "campaign_id": cid})
    out.sort(key=lambda e: e.get("applied_at", ""), reverse=True)
    return out


# ── Defensive CRM reads (never let a CRM hiccup break the autopilot) ──────────
def _safe_crm_leads():
    try:
        from pikorua_adflow.analytics import crm_analytics
        return crm_analytics.get_leads()
    except Exception:
        return [], ""


def _safe_crm_report(crm_service) -> dict:
    try:
        return crm_service.crm_report()
    except Exception:
        return {}
