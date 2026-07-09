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


# ── Live signal 1b: proven performance per LOCALITY within a city (from your CRM) ──
def performance_by_area(campaign_name: str, crm_leads: list[dict]) -> dict[str, dict]:
    """
    Like performance_by_city but one level finer — groups by (city, current_area)
    using the lead's own locality field. Only rows carrying a non-empty area are
    counted, so this naturally returns {} for campaigns/CRMs with no locality data
    rather than guessing.

    Returns {"{city_norm}::{area_norm}": {"city": str, "area": str, "leads": int, "quality": int}}.
    """
    from pikorua_adflow.analytics import crm_analytics as ca

    matched = ca.match_meta_leads(crm_leads, campaign_name)
    out: dict[str, dict] = {}
    for raw in matched:
        norm_row = ca._normalize([raw])[0]
        city = norm_row.get("city", "").strip()
        area = norm_row.get("currentarea", "").strip()
        if not city or not area:
            continue
        key = f"{_norm(city)}::{_norm(area)}"
        bucket = out.setdefault(key, {"city": city.title(), "area": area.title(), "leads": 0, "quality": 0})
        bucket["leads"] += 1
        if ca._is_quality(norm_row):
            bucket["quality"] += 1
    return out


def covered_area_keys(targeting: dict) -> set[str]:
    """Bare Meta keys of every specifically-targeted area (neighbourhood/zip/place) in a
    live spec — used so an area-level suggestion never re-offers one already targeted."""
    geo = (targeting or {}).get("geo_locations", {}) or {}
    keys: set[str] = set()
    for coll_name in ("neighborhoods", "zips", "places"):
        for a in (geo.get(coll_name) or []):
            k = a.get("key")
            if k:
                keys.add(str(k))
    return keys


def area_add_candidates(city_name: str, city_key: str, region: str, campaign_name: str,
                        crm_leads: list[dict], covered_areas: set[str], token: str,
                        *, perf_area: dict | None = None, min_quality: int = 1,
                        max_areas: int = 5) -> list[dict]:
    """
    Specific areas to target within `city_name` instead of the whole city. Prefers
    real CRM-proven localities (from each lead's own `current_area`), resolved to a
    Meta neighbourhood key and filtered to ones not already covered. When the CRM has
    no locality data for this city, falls back to Meta's own well-known neighbourhoods
    for it (flagged source="meta" — a lower-confidence, non-CRM suggestion) rather than
    returning nothing.

    Returns [{key, name, type, leads, quality, source: "crm"|"meta"}].
    """
    from pikorua_adflow.tools import meta_targeting as mt

    perf_area = perf_area if perf_area is not None else performance_by_area(campaign_name, crm_leads)
    prefix = _norm(city_name) + "::"
    city_areas = [(k, p) for k, p in perf_area.items()
                  if k.startswith(prefix) and p["quality"] >= min_quality]
    city_areas.sort(key=lambda kp: (kp[1]["quality"], kp[1]["leads"]), reverse=True)

    out: list[dict] = []
    if token and city_areas:
        cache = mt._load_cache()
        for _, p in city_areas[: max_areas * 2]:
            try:
                hits = mt.search_neighborhoods(p["area"], token, region=region, strict=True, limit=3)
            except Exception:
                hits = []
            hit = hits[0] if hits else None
            if not hit or str(hit["key"]) in covered_areas:
                continue
            out.append({"key": hit["key"], "name": hit["name"], "type": "neighborhood",
                        "leads": p["leads"], "quality": p["quality"], "source": "crm"})
            if len(out) >= max_areas:
                break
        mt._save_cache(cache)
    if out:
        return out

    if token and city_key:
        try:
            fallback = mt.suggest_neighborhoods_for_city(city_name, region, city_key, token, limit=max_areas)
        except Exception:
            fallback = []
        out = [{"key": f["key"], "name": f["name"], "type": "neighborhood",
                "leads": 0, "quality": 0, "source": "meta"} for f in fallback
               if str(f["key"]) not in covered_areas]
    return out[:max_areas]


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
    """[{name, key}] for the cities a live ad set currently targets.

    NOTE: when Meta returns a live ad set's targeting, city entries carry only a
    `key` (no `name`) — so `name` falls back to the key string. Never compare this
    `name` against CRM city names; compare on `key` (see covered_city_keys)."""
    geo = (targeting or {}).get("geo_locations", {}) or {}
    return [{"name": c.get("name") or str(c.get("key")), "key": str(c.get("key"))}
            for c in (geo.get("cities") or [])]


def covered_city_keys(targeting: dict, saved_audience: dict | None = None) -> set[str]:
    """
    The set of city KEYS a campaign effectively covers — the fix for the
    "Ahmedabad shown as untargeted while its areas are selected" bug.

    Meta's live targeting read gives geo keys, not names, and area-level targeting
    puts coverage under several different geo_locations collections depending on
    HOW the areas were picked, not just `neighborhoods`/`zips`. This unions:
      • directly-targeted city keys (`geo_locations.cities[].key`),
      • `geo_locations.places[].primary_city_id` — Meta's landmark/POI-radius
        targeting (e.g. "Paldi, Ahmedabad", "Ambli Bopal Road, Ahmedabad"). This is
        what Ads-Manager-native "drop a pin on a locality" area picks use, and it
        carries the covering city's id directly — no AdFlow overlay needed.
      • `geo_locations.custom_locations[].primary_city_id` — Meta's lat/long-circle
        targeting, same city-id field.
      • the city_key stamped on each targeted neighbourhood/zip in the saved audience
        (build_default_audience/audience.json carry it) — AdFlow's own area picker.
    Callers compare a CRM city's resolved key against this set.
    """
    geo = (targeting or {}).get("geo_locations", {}) or {}
    keys: set[str] = {str(c.get("key")) for c in (geo.get("cities") or []) if c.get("key")}
    for coll in (geo.get("places") or [], geo.get("custom_locations") or []):
        for a in coll:
            ck = a.get("primary_city_id")
            if ck:
                keys.add(str(ck))
    # Areas in the live spec are bare keys; the campaign's saved audience is where the
    # area→city_key mapping lives (stamped at selection time).
    aud = saved_audience or {}
    for coll in (aud.get("neighborhoods") or [], aud.get("zips") or []):
        for a in coll:
            ck = str(a.get("city_key") or "")
            if ck:
                keys.add(ck)
    # Map-pin (custom_locations) targeting carries its covering city on the saved
    # audience's map_point too — count it so a pin-targeted campaign isn't flagged
    # as leaving its own city untargeted.
    mp_ck = str((aud.get("map_point") or {}).get("city_key") or "")
    if mp_ck:
        keys.add(mp_ck)
    for p in (aud.get("custom_locations") or []):
        ck = str(p.get("city_key") or "")
        if ck:
            keys.add(ck)
    return keys


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
                        saved_audience: dict | None = None,
                        token: str = "", account: str = "",
                        campaign_id: str = "") -> dict:
    """
    Produce geo suggestions for one campaign. Returns:
      {
        "trim":   [{city, key, leads, quality, spend_wasted, reason}],           # REVIEW only
        "add":    [{city, leads, quality, reach, reason, suggested_areas}],       # new-city opportunity
        "expand": [{city, areas, reason}],                                       # more areas in an
                                                                                  # already-targeted city
        "has_data": bool,
      }

    Both "add" and "expand" prefer suggesting SPECIFIC areas (neighbourhoods) over a
    whole city — "add" is for a city not targeted at all; "expand" is for a city
    that's already covered only through specific areas/places (never a plain city
    circle), where CRM shows quality leads from OTHER localities in that same city
    you haven't picked yet. suggested_areas may be empty (no CRM/Meta area resolvable)
    — the caller falls back to whole-city add in that case.

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
    result: dict = {"trim": [], "add": [], "expand": [], "has_data": bool(perf)}
    if not perf:
        return result  # cold start — stay inclusive, suggest nothing

    # Fetch real spend-per-region from Meta (best-effort; {} if unavailable).
    spend_map = _spend_by_region(campaign_id, token)

    current = targeted_cities(targeting)
    # A live ad set's targeting returns geo KEYS, not names — so compare on keys, never
    # on the key-as-name fallback (that mismatch made "Ahmedabad" read as untargeted
    # even when it was). Resolve each CRM city → key and union in area→city coverage.
    covered = covered_city_keys(targeting, saved_audience)
    country = base_audience.get("country", "IN")
    resolve_cache = None
    key_for_city: dict[str, str] = {}
    region_for_city: dict[str, str] = {}
    if token:
        from pikorua_adflow.tools import meta_targeting as mt
        resolve_cache = mt._load_cache()
        for _ck, _p in perf.items():
            try:
                hit = mt._best_city(_p["display"], token, country, resolve_cache)
            except Exception:
                hit = None
            if hit and hit.get("key"):
                key_for_city[_ck] = str(hit["key"])
                region_for_city[_ck] = hit.get("region", "")
        mt._save_cache(resolve_cache)

    # TRIM (review-only): a TARGETED city with enough leads but zero quality. Framed as a
    # question — these buyers may be investors, so the human decides.
    # When spend data is available, the card shows the actual ₹ at stake.
    for c in current:
        # Match this targeted city (a key) back to a CRM perf city via resolved keys.
        p = next((perf[ck] for ck, k in key_for_city.items()
                  if k == str(c["key"]) and ck in perf), None)
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
    # Only suggest a city we've RESOLVED to a key and confirmed is not already covered —
    # conservative on purpose, so an unresolved/ambiguous city never triggers a false
    # "you're not targeting X" card (the bug this fix removes).
    reach_cache: dict = {}
    probes = 0
    add_candidates = [
        (key, p) for key, p in perf.items()
        if p["quality"] >= GEO_ADD_MIN_QUALITY
        and key in key_for_city and key_for_city[key] not in covered
    ]
    add_candidates.sort(key=lambda kp: (kp[1]["quality"], kp[1]["leads"]), reverse=True)
    perf_area = performance_by_area(campaign.get("name", ""), crm_leads)
    covered_areas = covered_area_keys(targeting)
    for key, p in add_candidates:
        reach = 0
        if token and account and probes < GEO_MAX_PROBES:
            reach = reach_for_city(p["display"], base_audience, token, account, reach_cache)
            probes += 1
        suggested_areas = []
        if token and key_for_city.get(key):
            try:
                suggested_areas = area_add_candidates(
                    p["display"], key_for_city[key], region_for_city.get(key, ""),
                    campaign.get("name", ""), crm_leads, covered_areas, token,
                    perf_area=perf_area)
            except Exception:
                suggested_areas = []
        area_note = (f" Proven areas within {p['display']}: "
                     + ", ".join(a["name"] for a in suggested_areas) + "."
                     if suggested_areas else "")
        result["add"].append({
            "city": p["display"], "leads": p["leads"], "quality": p["quality"],
            "reach": reach, "suggested_areas": suggested_areas,
            "reason": (f"{p['quality']} quality enquir{'y' if p['quality'] == 1 else 'ies'} "
                       f"already came from {p['display']}, but the campaign isn't targeting it. "
                       + (f"~{reach:,} reachable there. " if reach else "")
                       + "Adding it could open a proven, untapped buyer pool."
                       + area_note),
        })

    # EXPAND: a city already covered, but ONLY through specific areas/places (never a
    # plain city-radius circle) — check whether CRM shows quality leads from OTHER
    # localities in that same city that aren't in the currently-targeted area set.
    # This is the "the property's own city, but more of it" case: it never fires for
    # a city targeted via a full radius circle, since the whole city is already covered.
    geo = (targeting or {}).get("geo_locations", {}) or {}
    direct_city_keys = {str(c.get("key")) for c in (geo.get("cities") or []) if c.get("key")}
    area_only_city_keys = covered - direct_city_keys
    city_for_key: dict[str, str] = {}
    for _ck, _k in key_for_city.items():
        city_for_key.setdefault(_k, _ck)
    for area_only_key in area_only_city_keys:
        city_norm = city_for_key.get(area_only_key)
        if not city_norm or city_norm not in perf:
            continue
        city_display = perf[city_norm]["display"]
        region = region_for_city.get(city_norm, "")
        try:
            candidates = area_add_candidates(
                city_display, area_only_key, region, campaign.get("name", ""),
                crm_leads, covered_areas, token, perf_area=perf_area, max_areas=3)
        except Exception:
            candidates = []
        # Only worth surfacing when it's a real CRM-proven gap, not the generic
        # Meta fallback (that would just be noise for a city you already target).
        candidates = [c for c in candidates if c.get("source") == "crm"]
        if candidates:
            result["expand"].append({
                "city": city_display, "areas": candidates,
                "reason": (f"{city_display} is already targeted through specific areas — "
                           f"your CRM shows quality buyers in {len(candidates)} more "
                           f"localit{'y' if len(candidates) == 1 else 'ies'} "
                           f"({', '.join(c['name'] for c in candidates)}) you haven't picked yet."),
            })
    return result
