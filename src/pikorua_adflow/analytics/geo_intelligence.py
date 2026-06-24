"""
geo_intelligence.py — dynamic, data-driven geo opportunity scoring for the autooptimiser.

DESIGN PRINCIPLE (locked with the user): there is NO hardcoded city-wealth table here.
Rankings of "rich cities" or "NRI source countries" are true today and stale tomorrow,
so nothing is frozen. Every signal is recomputed at call time from live sources:

  • PROVEN PERFORMANCE  — your own CRM: leads + *quality* conversions attributed to each
                          city the campaign actually reaches. The only signal that proves
                          a geo works for YOU. Strongest weight.
  • REACHABLE DEMAND    — Meta's live reach estimate for a city at the campaign's own
                          intent/age layer (Graph API). How many real, reachable buyers
                          exist there now — not a static population number.
  • DEMAND TREND        — optional, best-effort, TTL-cached live signal (left as a hook;
                          off by default so it never slows the autooptimiser pass).

Two hard rules this module enforces, both reflecting the user's direction:

  1. NEVER recommend removing a geo on a "wrong city" assumption. Out-of-city buyers are
     often the highest-value segment (investors, diaspora, relocators). A geo is only ever
     flagged for the human to REVIEW — never auto-removed — and only when it has produced
     a meaningful number of leads with ZERO quality conversions in your CRM. Framed as a
     question ("these may be investors — keep, or narrow?"), never as an assertion.

  2. Proactively surface geos to ADD — high-opportunity cities the campaign is NOT yet
     targeting, discovered from where your own CRM leads actually come from. This is the
     "don't miss a buyer just because they're in another city" direction.

Cold start (no CRM data): the module returns nothing — it never narrows on assumption and
defers entirely to Meta's own delivery.
"""

from __future__ import annotations

import os

# ── Tunables (counts, not city names — the "what factors" not the "which cities") ──
GEO_MIN_LEADS_TO_JUDGE = 6     # need at least this many leads from a city before judging it
GEO_ADD_MIN_QUALITY = 2        # an untargeted city needs this many quality leads to suggest adding
GEO_MAX_PROBES = 8             # cap live reach lookups per pass (keeps the autooptimiser fast)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


# ── Live signal 1: proven performance per city (from your CRM) ─────────────────
def performance_by_city(campaign_name: str, crm_leads: list[dict]) -> dict[str, dict]:
    """
    Group this campaign's CRM-attributed leads by the city the *lead* came from, and
    count quality conversions. Fully dynamic — reads whatever your CRM holds right now.

    Returns {city_norm: {"display": str, "leads": int, "quality": int}}.
    """
    from pikorua_adflow.analytics import crm_analytics as ca

    matched = ca.match_meta_leads(crm_leads, campaign_name)
    out: dict[str, dict] = {}
    for raw in matched:
        norm_row = ca._normalize([raw])[0]
        city = norm_row.get("city", "").strip()
        if not city:
            continue
        key = _norm(city)
        bucket = out.setdefault(key, {"display": city.title(), "leads": 0, "quality": 0})
        bucket["leads"] += 1
        if ca._is_quality(norm_row):
            bucket["quality"] += 1
    return out


# ── Live signal 2: reachable demand for a city (Meta reach estimate) ───────────
def reach_for_city(city_name: str, base_audience: dict, token: str, account: str,
                   cache: dict | None = None) -> int:
    """
    Live Meta reach estimate for `city_name` at the campaign's own intent/age layer.
    Resolves the city key, layers the campaign's existing interests/age, and asks Meta
    how many people are reachable there NOW. 0 on any failure (never raises).
    """
    from pikorua_adflow.tools import meta_targeting as mt
    from pikorua_adflow.tools.meta_tool import fetch_reach_estimate

    cache = cache if cache is not None else {}
    ckey = _norm(city_name)
    if ckey in cache:
        return cache[ckey]
    estimate = 0
    try:
        resolve_cache = mt._load_cache()
        city = mt._best_city(city_name, token, base_audience.get("country", "IN"), resolve_cache)
        mt._save_cache(resolve_cache)
        if city:
            probe = {
                "country": base_audience.get("country", "IN"),
                "city": city["name"], "city_key": city["key"],
                "radius_km": base_audience.get("radius_km") or mt.DEFAULT_RADIUS_KM,
                "age_min": base_audience.get("age_min") or mt.DEFAULT_AGE_MIN,
                "age_max": base_audience.get("age_max") or mt.DEFAULT_AGE_MAX,
                "interests": base_audience.get("interests", []),
                "behaviours": base_audience.get("behaviours", []),
            }
            spec = mt.build_targeting_spec(probe)
            estimate = fetch_reach_estimate(account, spec, token).get("estimate_mau", 0)
    except Exception:
        estimate = 0
    cache[ckey] = estimate
    return estimate


# ── Targeted-city extraction from a live ad-set targeting spec ─────────────────
def targeted_cities(targeting: dict) -> list[dict]:
    """[{name, key}] for the cities a live ad set currently targets."""
    geo = (targeting or {}).get("geo_locations", {}) or {}
    return [{"name": c.get("name") or str(c.get("key")), "key": str(c.get("key"))}
            for c in (geo.get("cities") or [])]


# ── Live signal 3: spend per geo region (Meta region breakdown) ──────────────────
def _spend_by_region(campaign_id: str, token: str) -> dict[str, float]:
    """
    Fetch spend-per-region from Meta for this campaign and return a
    {region_name_lower: spend_inr} lookup. {} on failure or missing campaign_id.
    Region names from Meta are state/city strings (e.g. 'Gujarat', 'Ahmedabad').
    We normalise to lower-case for fuzzy matching against CRM city strings.
    """
    if not campaign_id or not token:
        return {}
    try:
        from pikorua_adflow.tools.meta_tool import fetch_insights_by_region
        rows = fetch_insights_by_region(campaign_id, token)
        return {_norm(r["region_name"]): r["spend_inr"] for r in rows if r["region_name"]}
    except Exception:
        return {}


# ── The recommendation engine ──────────────────────────────────────────────────
def geo_recommendations(campaign: dict, targeting: dict, brief: dict | None,
                        crm_leads: list[dict], *, base_audience: dict | None = None,
                        token: str = "", account: str = "",
                        campaign_id: str = "") -> dict:
    """
    Produce geo suggestions for one campaign. Returns:
      {
        "trim": [{city, key, leads, quality, spend_wasted, reason}],  # REVIEW only
        "add":  [{city, leads, quality, reach, reason}],               # opportunity
        "has_data": bool,
      }

    When campaign_id is supplied, trim cards are enriched with spend_wasted (₹ actually
    spent on that region with no quality result) sourced from Meta's region breakdown.
    This makes 'should we pull back from Mumbai?' a concrete ₹ decision, not an abstract
    lead-count judgement.

    All suggestions are advisory (the autooptimiser surfaces them as approve-decisions).
    Nothing here ever removes or adds a geo on its own.
    """
    token = token or os.getenv("META_ACCESS_TOKEN", "")
    account = (account or os.getenv("META_AD_ACCOUNT_ID", "")).replace("act_", "")
    campaign_id = campaign_id or campaign.get("id", "")
    base_audience = base_audience or {}
    perf = performance_by_city(campaign.get("name", ""), crm_leads)
    result: dict = {"trim": [], "add": [], "has_data": bool(perf)}
    if not perf:
        return result  # cold start — stay inclusive, suggest nothing

    # Fetch real spend-per-region from Meta (best-effort; {} if unavailable).
    spend_map = _spend_by_region(campaign_id, token)

    current = targeted_cities(targeting)
    current_keys = {_norm(c["name"]) for c in current}

    # TRIM (review-only): a TARGETED city with enough leads but zero quality. Framed as a
    # question — these buyers may be investors, so the human decides.
    # When spend data is available, the card shows the actual ₹ at stake.
    for c in current:
        p = perf.get(_norm(c["name"]))
        if p and p["leads"] >= GEO_MIN_LEADS_TO_JUDGE and p["quality"] == 0:
            # Match city name against Meta's region names (best-effort fuzzy lookup).
            city_key = _norm(p["display"])
            spend_wasted = spend_map.get(city_key, 0.0)
            # Also try the raw city key string in case Meta names differ.
            if not spend_wasted:
                for rname, rspend in spend_map.items():
                    if city_key in rname or rname in city_key:
                        spend_wasted = rspend
                        break
            spend_str = f" ₹{spend_wasted:,.0f} spent here with no quality result." if spend_wasted else ""
            result["trim"].append({
                "city": p["display"], "key": c["key"],
                "leads": p["leads"], "quality": 0,
                "spend_wasted": round(spend_wasted, 2),
                "reason": (f"{p['leads']} leads from {p['display']} and none have become a "
                           f"quality enquiry yet.{spend_str} They may be investors who take longer — "
                           "keep them, or narrow this geo?"),
            })

    # ADD (opportunity): cities your CRM leads actually come from that you're NOT targeting
    # and that already show quality interest. Ranked by live reach where resolvable.
    reach_cache: dict = {}
    probes = 0
    add_candidates = [
        (key, p) for key, p in perf.items()
        if key not in current_keys and p["quality"] >= GEO_ADD_MIN_QUALITY
    ]
    add_candidates.sort(key=lambda kp: (kp[1]["quality"], kp[1]["leads"]), reverse=True)
    for key, p in add_candidates:
        reach = 0
        if token and account and probes < GEO_MAX_PROBES:
            reach = reach_for_city(p["display"], base_audience, token, account, reach_cache)
            probes += 1
        result["add"].append({
            "city": p["display"], "leads": p["leads"], "quality": p["quality"],
            "reach": reach,
            "reason": (f"{p['quality']} quality enquir{'y' if p['quality'] == 1 else 'ies'} "
                       f"already came from {p['display']}, but the campaign isn't targeting it. "
                       + (f"~{reach:,} reachable there. " if reach else "")
                       + "Adding it could open a proven, untapped buyer pool."),
        })
    return result
