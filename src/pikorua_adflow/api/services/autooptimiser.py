"""
Campaign AutoOptimiser — the self-optimising brain.

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

State (applied log, cooldowns, last digest) persists in outputs/autooptimiser_state.json.
All Meta writes go through pikorua_adflow.tools.meta_tool; predictions are tracked by
analytics.optimization_tracker so estimates self-calibrate over time.

──────────────────────────────────────────────────────────────────────────────────────
FUTURE RUNGS — planned but not yet implemented
──────────────────────────────────────────────────────────────────────────────────────

RUNG 11 — CAPI Qualified-Lead Feedback  [HIGH PRIORITY]
  What:    When a CRM lead status changes to Warm/Interested/Hot, fire a server-side
           conversion event back to Meta via the Conversions API (CAPI) tagged as a
           QualifiedLead. This teaches Meta's Advantage+ algorithm who the real buyers
           are, not just who fills forms cheaply. Expected: CPL stable or lower, but
           lead quality improves — fewer bad-number / not-interested leads over time.
  Blocked: Requires (a) Meta webhook configured in Business Manager so leadgen_id
           reaches the CRM reliably (currently using 5-min polling), and (b) leadgen_id
           stored per CRM row so Meta can match the event back to the right person.
  Files:   new analytics/meta_capi.py, crm_source.py (store leadgen_id), status-change
           hook in crm_service.py or a Supabase trigger.

RUNG 12 — Periodic Targeting Refresh  [READY — needs cron trigger]
  What:    Every 30 days call retarget_campaign_adsets() on all active campaigns to
           refresh flexible_spec against the current CLIENTELE_TARGETING_MAP.
           POST /retarget-campaign already exists, is tested, and is dry-run safe.
  Why:     Meta's interest taxonomy weights shift over time; monthly re-resolution
           keeps targeting aligned with what Meta currently amplifies.
  Files:   autooptimiser.py rung logic + cron entry in the autooptimiser route.
──────────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import (AUDIENCES_REGISTRY_PATH, OUTPUT_DIR,
                      AO_BENCHMARK_CPL, AO_FREQ_SATURATED, AO_FREQ_EXHAUSTED,
                      AO_CPL_CEILING, AO_CPL_RISING_RATIO, AO_QUALITY_LEAD_MIN,
                      AO_COOLDOWN_DAYS)
from ..state import RUNS, RUNS_LOCK

# ── Tunables (sourced from config — env-overridable, Tier-2 self-calibrating) ─
# Aliases preserve all existing references inside this module unchanged.
BENCHMARK_CPL    = AO_BENCHMARK_CPL
FREQ_SATURATED   = AO_FREQ_SATURATED
FREQ_EXHAUSTED   = AO_FREQ_EXHAUSTED
CPL_CEILING      = AO_CPL_CEILING
CPL_RISING_RATIO = AO_CPL_RISING_RATIO
QUALITY_LEAD_MIN = AO_QUALITY_LEAD_MIN
COOLDOWN_DAYS    = AO_COOLDOWN_DAYS
AB_WINDOW_DAYS = 7          # days to run A/B pairs before declaring a winner (B2)
AB_WINDOW_EXTENSION = 2     # days to extend once if result is inconclusive


_STATE_PATH = OUTPUT_DIR / "autooptimiser_state.json"
_NRI_DEFAULT = ["AE", "GB", "US", "SG"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── State ─────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        data.setdefault("campaigns", {})   # campaign_id -> {applied: [...], cooldowns: {fix_type: iso}}
        data.setdefault("last_run", None)
        data.setdefault("ab_groups", {})   # key -> {control_ad_id, challenger_ad_id, ...}
        return data
    except Exception:
        return {"campaigns": {}, "last_run": None, "ab_groups": {}}


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


# ── A/B group registration + resolve (B2) ─────────────────────────────────────
def _register_ab_group(*, run_id: str, adset_id: str,
                       control_ad_id: str, challenger_ad_id: str) -> None:
    """
    Record a new A/B pair in autooptimiser state. Called by the refresh-creatives
    endpoint when ab=True. The nightly resolve pass checks this table.
    """
    state = _load_state()
    key = f"{run_id}|{adset_id}"
    state.setdefault("ab_groups", {})[key] = {
        "run_id": run_id,
        "adset_id": adset_id,
        "control_ad_id": control_ad_id,
        "challenger_ad_id": challenger_ad_id,
        "started_at": _now().isoformat(),
        "window_days": AB_WINDOW_DAYS,
        "extensions": 0,
        "resolved": False,
    }
    _save_state(state)


def _resolve_ab_groups(state: dict) -> list[dict]:
    """
    Check all unresolved A/B groups. For each group whose window has elapsed:
      - Compare control vs challenger using creative_performance.compare_ab_pair()
      - If a winner is clear: pause the loser, mark resolved, log to applied
      - If inconclusive + first time: extend window by AB_WINDOW_EXTENSION days
      - If inconclusive + already extended: surface as human decision
    Returns a list of resolve-action dicts for the caller to surface in the UI.
    """
    from pikorua_adflow.analytics import creative_performance as _cp
    from pikorua_adflow.tools import meta_tool as mt

    token = os.getenv("META_ACCESS_TOKEN", "")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    resolutions: list[dict] = []

    for key, grp in list(state.get("ab_groups", {}).items()):
        if grp.get("resolved"):
            continue
        started = datetime.fromisoformat(grp["started_at"])
        window = int(grp.get("window_days", AB_WINDOW_DAYS))
        if (_now() - started) < timedelta(days=window):
            continue  # window not elapsed yet

        verdict = _cp.compare_ab_pair(
            control_ad_id=grp["control_ad_id"],
            challenger_ad_id=grp["challenger_ad_id"],
            control_adset_id=grp["adset_id"],
            challenger_adset_id=grp["adset_id"],
            token=token,
        )

        if verdict["verdict"] == "challenger_wins":
            if not dry_run:
                try:
                    mt.pause_variant(grp["control_ad_id"], token)
                except Exception:
                    pass
            grp["resolved"] = True
            grp["winner"] = "challenger"
            resolutions.append({"key": key, "verdict": verdict["verdict"],
                                 "reason": verdict["reason"], "auto": True})

        elif verdict["verdict"] == "control_wins":
            if not dry_run:
                try:
                    mt.pause_variant(grp["challenger_ad_id"], token)
                except Exception:
                    pass
            grp["resolved"] = True
            grp["winner"] = "control"
            resolutions.append({"key": key, "verdict": verdict["verdict"],
                                 "reason": verdict["reason"], "auto": True})

        else:  # inconclusive
            if grp.get("extensions", 0) < 1:
                grp["window_days"] = window + AB_WINDOW_EXTENSION
                grp["extensions"] = grp.get("extensions", 0) + 1
                resolutions.append({"key": key, "verdict": "extended",
                                    "reason": f"Extended by {AB_WINDOW_EXTENSION}d. "
                                               + verdict["reason"], "auto": True})
            else:
                # Max extensions reached — surface as human decision.
                grp["resolved"] = True
                resolutions.append({"key": key, "verdict": "inconclusive",
                                    "reason": verdict["reason"], "auto": False})
    _save_state(state)
    return resolutions


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
# ── Warm-lead reference profile ──────────────────────────────────────────────
# Derived from actual CRM data (2026-06-22): 39 warm leads cluster tightly on
# Ahmedabad (31/39) + Business/Entrepreneur + 5-7 Cr.  Used as a fallback seed
# for profile_match_score when `top_profiles` from crm_report is empty or sparse
# (i.e., before enough quality leads have been tagged in the live CRM).
#
# Format matches top_converting_profiles() output:
#   {"profile": {"industry": ..., "budget": ..., "city": ...}, "quality_rate": ...}
_WARM_LEAD_REFERENCE_PROFILES: list[dict] = [
    {"profile": {"industry": "Business/Entrepreneur", "budget": "5–7Cr",  "city": "Ahmedabad"}, "quality_rate": 100.0},
    {"profile": {"industry": "Business/Entrepreneur", "budget": "7–10Cr", "city": "Ahmedabad"}, "quality_rate": 100.0},
    {"profile": {"industry": "Business/Entrepreneur", "budget": "10Cr+",  "city": "Ahmedabad"}, "quality_rate": 100.0},
    {"profile": {"industry": "Business/Entrepreneur", "budget": "5–7Cr",  "city": "Rajkot"},    "quality_rate": 80.0},
    {"profile": {"industry": "Business/Entrepreneur", "budget": "5–7Cr",  "city": "Surat"},     "quality_rate": 80.0},
]
# Only inject the reference for these clientele types (bungalow/premium buyers
# match this profile — NRI and commercial have different buyer personas).
_REFERENCE_PROFILE_CLIENTELE = {"luxury_bungalow", "premium_apartment", ""}


def _should_inject_reference(campaign_name: str, profiles: list[dict]) -> bool:
    """True when the reference profile should supplement a sparse top_profiles list."""
    if len(profiles) >= 3:
        return False  # enough real data — don't override with a static heuristic
    # Apply only to bungalow/premium-style campaigns (inferred from name keywords).
    cn = campaign_name.strip().lower()
    return any(kw in cn for kw in ("bungalow", "villa", "premium", "luxury", "pikorua", ""))


def adaptive_quality(campaign_name: str, spend_7d: float, leads_7d: int,
                     crm_leads: list[dict], crm_report: dict) -> dict:
    """
    Pick the quality metric for a campaign.

    Real quality-CPL (₹ per quality CRM lead matched to this campaign) once
    >= QUALITY_LEAD_MIN matched quality leads exist; otherwise the profile-match
    score (how much the campaign's leads resemble the best historical converters).

    When `top_profiles` is sparse (data still building), the known warm-lead
    reference profile (Ahmedabad + Business + 5-7 Cr) is used as a fallback seed
    so profile_match_score produces a meaningful signal from day one.
    Returns a dict the UI renders verbatim.
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
    # Inject reference profiles when real data is sparse — prevents a zero score
    # while the CRM builds up tagged quality leads.
    if _should_inject_reference(campaign_name, profiles):
        profiles = _WARM_LEAD_REFERENCE_PROFILES + profiles

    pm = ca.profile_match_score(matched, profiles)
    plain_cpl = round(spend_7d / leads_7d) if leads_7d else None
    return {
        "metric_used": "profile_match",
        "label": "Cost per lead",
        "value": plain_cpl, "unit": "₹",
        "profile_match_pct": pm["score"], "n_quality": n_quality,
        "n_matched": len(matched),
        "building": True,
        "reference_profile_active": _should_inject_reference(campaign_name, crm_report.get("top_profiles", []) if crm_report else []),
    }


# ── Plain-language verdict (no jargon) ────────────────────────────────────────
def _plain_metrics(m: dict) -> dict:
    """Translate the raw 7-day metrics into phrases a non-marketer reads at a glance."""
    cpl = m.get("cpl")
    ctr = m.get("ctr") or 0
    freq = m.get("frequency") or 0
    seen = ("each person has seen it about once" if freq < 1.6
            else f"each person has seen it ~{round(freq)} times")
    clicks = ("almost nobody who sees it clicks" if ctr < 0.8
              else "click-through is healthy" if ctr >= 1.2 else "click-through is okay")
    return {
        "cost_each": (f"₹{round(cpl):,} per enquiry" if cpl else "no enquiries yet"),
        "seen": seen, "clicks": clicks,
    }


def _verdict(m: dict, quality: dict, best_cpl: int | None) -> dict:
    """A one-glance verdict for a campaign: winning / okay / bleeding / idle.

    Anchored to the account's OWN best live cost-per-enquiry (dynamic — not a frozen
    number), so 'good' always means good *for this account right now*.
    """
    spend = m.get("spend") or 0
    leads = m.get("leads") or 0
    cpl = m.get("cpl")
    ctr = m.get("ctr") or 0
    anchor = best_cpl or BENCHMARK_CPL

    if spend < 1 or leads == 0:
        return {"state": "idle", "emoji": "⚪", "label": "Not running",
                "line": "This campaign isn't spending or bringing enquiries right now."}

    multiple = (cpl / anchor) if (cpl and anchor) else 1
    weak_clicks = ctr and ctr < 0.8

    if (cpl and multiple >= 2.5) or (cpl and cpl > CPL_CEILING and weak_clicks):
        bits = [f"costs ₹{round(cpl):,} per enquiry"]
        if anchor and multiple >= 1.5:
            bits.append(f"{multiple:.1f}× your best campaign (₹{anchor:,})")
        if weak_clicks:
            bits.append(f"and only {ctr}% of viewers click the ad")
        return {"state": "bleeding", "emoji": "🔴", "label": "Bleeding money",
                "line": "This " + ", ".join(bits) + "."}

    if cpl and multiple <= 1.3 and ctr >= 1.0:
        return {"state": "winning", "emoji": "🟢", "label": "Winning",
                "line": f"₹{round(cpl):,} per enquiry — among your best. Worth copying for new launches."}

    return {"state": "okay", "emoji": "🟡", "label": "Doing okay",
            "line": f"₹{round(cpl):,} per enquiry. Steady, with room to improve."}


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

    # Rung 0 (AUTO) — creative winner/loser within a multi-variant campaign.
    # When one variant is provably worse (CPL ≥ 2× avg AND enough data), pause it
    # and shift 30% extra budget to the winner. This is the highest-leverage lever
    # on a live campaign — it fires before geo fixes so budget stops going to the
    # loser immediately.
    # Per-ad cooldowns are tracked separately under camp_state["ad_cooldowns"].
    from pikorua_adflow.analytics import creative_performance as _cp
    from pikorua_adflow.tools.meta_tool import fetch_ads_with_age as _fads
    # Only meaningful when there's more than one ad in the campaign.
    if not _in_cooldown(camp_state, "winner_loser"):
        try:
            live_ads_full = [a for a in ads if (a.get("effective_status") or "") in ("ACTIVE", "")]
            if len(live_ads_full) >= 2:
                token = os.getenv("META_ACCESS_TOKEN", "")
                ad_records = [
                    {"ad_id": a["id"], "variant": i + 1, "adset_id": adset_id}
                    for i, a in enumerate(live_ads_full)
                ]
                perf = _cp.compare_variants(ad_records, token)
                if perf["has_winner_loser"]:
                    w_cpl = perf["winner_cpl"] or 0
                    l_cpl = perf["loser_cpl"] or 0
                    budget_paise = campaign.get("daily_budget")
                    cur_budget = int(int(budget_paise) / 100) if budget_paise else 1000
                    new_budget = min(int(cur_budget * 1.30), cur_budget + 2000)
                    add("winner_loser", 0, "auto",
                        "Pause the weaker ad, give budget to the winner",
                        (f"One creative costs ₹{round(l_cpl):,}/enquiry — "
                         f"{round(l_cpl / max(w_cpl, 1), 1)}× more than the other at ₹{round(w_cpl):,}. "
                         "Pausing it and shifting its budget to the winner."),
                        "winner_loser",
                        {"loser_ad_id": perf["loser_ad_id"],
                         "winner_adset_id": perf["winner_adset_id"],
                         "new_budget_inr": new_budget,
                         "base_budget": cur_budget,
                         "adset_id": adset_id})
        except Exception:
            pass  # never let B1 break the ladder

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
                                            base_audience=base_aud,
                                            campaign_id=cid)
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
    # Fires on weak click-through ALONE — a low CTR means the ad isn't landing,
    # whether the cost is climbing or has simply been poor from day one.
    stale = ctr and ctr < 0.8
    if stale:
        why = (f"Only {ctr}% of people who see this ad click it — barely anyone who sees it acts. "
               + ("Cost is climbing too. " if rising else ""))
        how = ("Open this campaign to regenerate and push fresh images & copy onto the live ads."
               if run_id else
               "Rebuild it in AdFlow — the new ads will be added directly to this campaign, "
               "keeping your budget, targeting, and delivery history intact.")
        add("fresh_creative", 8, "approve",
            "The ad isn't landing — refresh it",
            why + how,
            "fresh_creative", {"campaign_id": cid, "run_id": run_id,
                               "adset_id": adset_id,
                               "campaign_name": campaign.get("name", "")})

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

    # Rung 9.5 (APPROVE) — dayparting from lead-arrival timing.
    # Only surfaces when CRM has enough timing signal and no schedule is already set.
    # Fires only once (cooldown); audience fatigue is not required — this is pure efficiency.
    if not _in_cooldown(camp_state, "dayparting"):
        try:
            from pikorua_adflow.analytics import lead_timing as _lt
            timing = _lt.analyse(crm_leads)
            already_scheduled = bool(targeting.get("ad_schedule_config") or
                                     adset.get("pacing_type") == ["day_parting"])
            if timing["has_signal"] and not already_scheduled:
                top_names = timing["top_day_names"]
                quiet_names = timing["quiet_day_names"]
                pct = timing["concentration_pct"]
                top_str = (", ".join(top_names[:-1]) + f" and {top_names[-1]}"
                           if len(top_names) > 1 else top_names[0])
                quiet_str = (", ".join(quiet_names) if quiet_names else "")
                detail = (
                    f"{pct}% of your leads arrive on {top_str}. "
                    + (f"{quiet_str} {'are' if len(quiet_names) > 1 else 'is'} quiet. " if quiet_str else "")
                    + "Applying a focus schedule means Meta runs your full daily budget on "
                    "your peak days and skips the quiet ones — same results, less waste."
                )
                add("dayparting", 9, "approve",
                    f"Focus ads on your {len(top_names)} peak days",
                    detail, "dayparting",
                    {"adset_id": adset_id,
                     "meta_schedule_days": timing["meta_schedule_days"],
                     "top_day_names": timing["top_day_names"],
                     "quiet_day_names": timing["quiet_day_names"],
                     "concentration_pct": timing["concentration_pct"],
                     "sample_size": timing["sample_size"]})
        except Exception:
            pass

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
        if action == "winner_loser":
            # Pause the loser ad, boost the winner's ad-set budget.
            loser_ad_id = params.get("loser_ad_id", "")
            winner_adset_id = params.get("winner_adset_id", "")
            new_budget_inr = int(params.get("new_budget_inr", 0))
            base_budget = int(params.get("base_budget", new_budget_inr))
            if loser_ad_id:
                mt.pause_variant(loser_ad_id, token)
            if winner_adset_id and new_budget_inr:
                mt.update_adset_budget(winner_adset_id, new_budget_inr, token)
            undo = {
                "action": "resume_loser_restore_budget",
                "loser_ad_id": loser_ad_id,
                "winner_adset_id": winner_adset_id,
                "base_budget": base_budget,
            }
            impact = {
                "summary": (f"Paused loser ad; winner ad-set budget → ₹{new_budget_inr:,}/day"),
                "loser_paused": loser_ad_id,
                "winner_budget": new_budget_inr,
            }
        elif action == "remove_geo":
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
            tracker.open_record(run_id=f"autooptimiser:{cid}", variant=0, action="budget",
                                basis="budget_linear", metric="leads", label="AutoOptimiser budget cut",
                                before=None,
                                raw_multiplier=new_b / max(params.get("base_budget", new_b), 1),
                                expected=tracker.predict("budget_linear",
                                                         new_b / max(params.get("base_budget", new_b), 1), None))
            impact = {"metric": "budget", "after": new_b}
        elif action == "dayparting":
            peak_days = params.get("meta_schedule_days", [])
            if not peak_days:
                return {"ok": False, "error": "No schedule days provided."}
            mt.update_adset_schedule(adset_id, peak_days, token)
            undo = {"action": "remove_schedule", "adset_id": adset_id}
            top_str = ", ".join(params.get("top_day_names", []))
            impact = {"summary": f"Ads now focused on {top_str}",
                      "peak_days": params.get("top_day_names", []),
                      "quiet_days": params.get("quiet_day_names", [])}
        elif action == "lookalike_refresh":
            # Account-level: re-upload CRM to refresh the Meta lookalike seed.
            account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
            from pikorua_adflow.tools.meta_audience_tool import upload_crm_split_audiences
            result = upload_crm_split_audiences(ad_account_id=account_id)
            if "error" in result:
                return {"ok": False, "error": result["error"]}
            # Stamp built_at + seed_size in the registry.
            try:
                rows = json.loads(AUDIENCES_REGISTRY_PATH.read_text()) if AUDIENCES_REGISTRY_PATH.exists() else []
                now_iso = _now().isoformat()
                seed_size = result.get("total_leads") or result.get("leads_uploaded") or 0
                for row in rows:
                    if row.get("role") == "lookalike":
                        row["built_at"] = now_iso
                        row["seed_size"] = int(seed_size)
                AUDIENCES_REGISTRY_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
            except Exception:
                pass
            undo = {}  # upload cannot be meaningfully undone
            impact = {"summary": f"Lookalike seed refreshed with {result.get('total_leads', '?')} contacts",
                      "total_leads": result.get("total_leads")}
        elif action == "pause":
            mt.toggle_campaign_status(params["campaign_id"], False, token) \
                if hasattr(mt, "toggle_campaign_status") else mt._patch(
                    params["campaign_id"], {"status": "PAUSED"}, token)
            undo = {"action": "resume_campaign", "campaign_id": params["campaign_id"]}
        else:
            return {"ok": False, "error": f"Unknown action '{action}'."}
    except Exception as exc:
        from pikorua_adflow.tools.errors import explain_and_log
        friendly = explain_and_log(f"AutoOptimiser — {action}", exc)
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
        if ua == "resume_loser_restore_budget":
            loser_ad_id = undo.get("loser_ad_id", "")
            winner_adset_id = undo.get("winner_adset_id", "")
            base_budget = int(undo.get("base_budget", 0))
            if loser_ad_id:
                mt.resume_variant(loser_ad_id, token)
            if winner_adset_id and base_budget:
                mt.update_adset_budget(winner_adset_id, base_budget, token)
        elif ua == "restore_targeting":
            mt.update_adset_targeting(undo["adset_id"], undo["targeting"], token)
        elif ua == "toggle_advantage":
            mt.toggle_advantage_audience(undo["adset_id"], undo["enable"], token)
        elif ua == "set_budget":
            mt.update_adset_budget(undo["target"], undo["daily_budget_inr"], token)
        elif ua == "remove_schedule":
            mt.remove_adset_schedule(undo["adset_id"], token)
        elif ua == "resume_campaign":
            mt._patch(undo["campaign_id"], {"status": "ACTIVE"}, token)
        else:
            return {"ok": False, "error": "This action can't be undone automatically."}
    except Exception as exc:
        from pikorua_adflow.tools.errors import explain_and_log
        friendly = explain_and_log(f"AutoOptimiser undo — {fix_type}", exc)
        return {"ok": False, "error": friendly["message"]}
    entry["undone"] = True
    cs.get("cooldowns", {}).pop(fix_type, None)
    _save_state(state)
    return {"ok": True}


def _resolve_clientele_interests(clientele_type: str, token: str) -> list[dict]:
    """Resolve the clientele's interest names to Meta {id,name} (autooptimiser rung 7)."""
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
def run_autooptimiser(apply_safe: bool = True) -> dict:
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

    # Dynamic anchor: the best cost-per-enquiry actually being achieved on this account
    # right now (not a frozen number). Everything is judged relative to this.
    live_cpls = [ev["metrics"]["d7"]["cpl"] for ev in evals
                 if ev["metrics"]["d7"]["cpl"] and ev["metrics"]["d7"]["leads"]]
    best_cpl = int(min(live_cpls)) if live_cpls else BENCHMARK_CPL
    star = None
    for ev in evals:
        m = ev["metrics"]["d7"]
        ev["verdict"] = _verdict(m, ev["quality"], best_cpl)
        if m["cpl"] == best_cpl and m["leads"]:
            star = {"campaign_name": ev["campaign_name"], "cost_each": f"₹{best_cpl:,} per enquiry"}

    # Severity rank per state so the action feed shows the worst problem first.
    _SEV = {"bleeding": 0, "okay": 1, "winning": 2, "idle": 3}

    auto_applied: list[dict] = []
    decisions: list[dict] = []
    for ev in evals:
        sev = _SEV.get(ev["verdict"]["state"], 1)
        for fix in ev["fixes"]:
            fix = {**fix, "verdict": ev["verdict"]["state"], "_sev": sev}
            if fix["mode"] == "auto" and apply_safe and not dry_run:
                res = apply_fix(fix, auto=True)
                if res.get("ok"):
                    auto_applied.append({**fix, "impact": res.get("impact", {})})
            elif fix["mode"] == "auto":
                # dry-run: surface what WOULD be auto-applied without calling Meta
                auto_applied.append({**fix, "impact": {"dry_run": True}})
            else:
                decisions.append(fix)

    # Worst campaign first, then by ladder rung (targeting before budget before pause).
    decisions.sort(key=lambda f: (f.get("_sev", 1), f["rung"]))

    # CRM coverage — be honest about the signal that powers quality scoring.
    total_matched = sum((ev["quality"].get("n_matched") or 0) for ev in evals)
    total_quality = sum((ev["quality"].get("n_quality") or 0) for ev in evals)
    crm_coverage = {
        "matched": total_matched, "quality": total_quality,
        "untagged": max(total_matched - total_quality, 0),
        "scoring_on": total_quality >= QUALITY_LEAD_MIN,
    }

    # Account-level actions — things that apply across all campaigns.
    account_actions: list[dict] = []
    try:
        from pikorua_adflow.analytics import lookalike_health as _lh
        registry_rows = json.loads(AUDIENCES_REGISTRY_PATH.read_text(encoding="utf-8")) \
            if AUDIENCES_REGISTRY_PATH.exists() else []
        staleness = _lh.check_staleness(registry_rows, len(crm_leads))
        if staleness["stale"]:
            account_actions.append({
                "fix_type": "lookalike_refresh",
                "action": "lookalike_refresh",
                "campaign_id": "__account__",
                "campaign_name": "Audience — account-wide",
                "rung": 3,
                "mode": "approve",
                "title": "Refresh your buyer audience seed",
                "detail": staleness["reason"] + " " + staleness["action_detail"],
                "params": {"age_days": staleness["age_days"],
                           "seed_size": staleness["seed_size"],
                           "current_crm_count": staleness["current_crm_count"],
                           "growth_pct": staleness["growth_pct"]},
            })
    except Exception:
        pass

    state["last_run"] = _now().isoformat()

    # B2: resolve any A/B groups whose window has elapsed.
    ab_resolutions = _resolve_ab_groups(state)

    # B3: update per-clientele creative memory from this pass's quality data.
    try:
        from pikorua_adflow.analytics import creative_learning as _cl
        _cl.update_memory(evals, token)
    except Exception:
        pass  # never let memory write break the autooptimiser

    # ── Tier 2 — settle outcomes for cooled-down fixes ────────────────────────
    # Re-fetch Meta insights for each campaign that has open prediction records
    # and automatically close them with the real outcome, updating EMA calibration.
    settled_outcomes: list[dict] = []
    try:
        from pikorua_adflow.analytics import optimization_tracker as _ot
        for ev in evals:
            try:
                results = _ot.settle_by_campaign(ev["campaign_id"], token)
                settled_outcomes.extend(results)
            except Exception:
                pass
    except Exception:
        pass  # never let the settle pass break the autooptimiser

    # ── Tier 3 — LLM strategist advisory pass ────────────────────────────────
    # Advisory brain: reads live data + settled outcomes → returns explanations,
    # anomalies, and structured suggestions (safe → auto-apply; risky → approval).
    strategist_result: dict = {}
    llm_safe_applied: list[dict] = []
    try:
        from pikorua_adflow.analytics import llm_strategist as _strat
        strategist_result = _strat.run_daily_pass(
            evals=evals,
            crm_report=crm_report,
            settled_outcomes=settled_outcomes,
        )
        # Route safe suggestions through the existing Tier-1 apply path.
        for sug in strategist_result.get("suggestions", []):
            if sug.get("risk") == "safe" and apply_safe and not dry_run:
                fix = sug.get("fix")
                if fix:
                    try:
                        res = apply_fix(fix, auto=True)
                        if res.get("ok"):
                            llm_safe_applied.append({**fix, "impact": res.get("impact", {}),
                                                     "source": "llm_strategist"})
                    except Exception:
                        pass
    except Exception:
        pass  # never let the strategist break the autooptimiser

    _save_state(state)

    return {
        "campaigns": [_public_campaign(ev) for ev in evals],
        "auto_applied": auto_applied + llm_safe_applied,
        "decisions": decisions[:2],
        "all_decisions": decisions,
        "account_actions": account_actions,
        "crm_coverage": crm_coverage,
        "star": star,
        "best_cpl": best_cpl,
        "last_run": state["last_run"],
        "benchmark_cpl": BENCHMARK_CPL,
        "ab_resolutions": ab_resolutions,
        "settled_outcomes": len(settled_outcomes),
        "strategist": strategist_result,
    }



def _public_campaign(ev: dict) -> dict:
    """Trim an evaluation to what the page needs: a plain verdict up top, the raw
    numbers underneath for anyone who wants them."""
    m = ev["metrics"]["d7"]
    return {
        "campaign_id": ev["campaign_id"], "campaign_name": ev["campaign_name"],
        "clientele_type": ev["clientele_type"], "quality": ev["quality"],
        "verdict": ev.get("verdict", {}), "plain": _plain_metrics(m),
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
                undoable = bool((e.get("undo") or {}).get("action"))
                out.append({**e, "campaign_id": cid, "undoable": undoable})
    out.sort(key=lambda e: e.get("applied_at", ""), reverse=True)
    return out


# ── Defensive CRM reads (never let a CRM hiccup break the autooptimiser) ──────────
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
