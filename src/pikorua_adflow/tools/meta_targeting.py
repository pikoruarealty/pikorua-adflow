"""
Meta targeting resolver — turns plain names into the IDs/keys Meta's ad set needs.

The AudienceCrew produces a rich targeting brief (interests, demographics, cities)
but Meta's API only accepts opaque IDs/keys, not names. This module bridges that
gap using Meta's read-only Targeting Search API:

  - cities    : GET /search?type=adgeolocation   -> geo "key"
  - interests : GET /search?type=adinterest       -> interest "id"
  - behaviours: GET /search?type=adTargetingCategory&class=behaviors -> behaviour "id"

All calls here are READ-ONLY — they never create anything or spend money, so they
are safe to run even when DRY_RUN is off. Resolved lookups are cached on disk so we
don't re-hit Meta for the same name on every deploy.

The curated default lists below are the starting audience attached to every campaign.
They are intentionally editable in one place so a non-original developer can tune the
luxury-real-estate audience without touching any logic.
"""
from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_BASE = "https://graph.facebook.com/v20.0"

# ── NRI country name → ISO-2 map ─────────────────────────────────────────────
# Covers the geographies Pikorua typically targets.
_NRI_COUNTRY_MAP: dict[str, str] = {
    # Gulf
    "uae": "AE", "united arab emirates": "AE", "dubai": "AE", "abu dhabi": "AE",
    "qatar": "QA", "bahrain": "BH", "kuwait": "KW", "oman": "OM",
    # North America
    "us": "US", "usa": "US", "united states": "US", "america": "US",
    "canada": "CA",
    # Europe
    "uk": "GB", "united kingdom": "GB", "england": "GB", "britain": "GB",
    "germany": "DE", "france": "FR", "netherlands": "NL", "switzerland": "CH",
    # Asia-Pacific
    "singapore": "SG", "australia": "AU", "new zealand": "NZ",
    "hong kong": "HK", "japan": "JP",
    # Other
    "kenya": "KE", "south africa": "ZA",
}


def parse_nri_countries(nri_geographies: str) -> list[str]:
    """
    Turn a free-text NRI geographies field (e.g. "UAE, US, UK") into ISO-2 codes.
    Skips anything it can't map so safe to pass raw user input.
    """
    codes: list[str] = []
    for part in nri_geographies.replace(";", ",").split(","):
        token = part.strip().lower()
        if not token:
            continue
        # Map lookup first — handles aliases like "uk"→"GB", "uae"→"AE"
        if token in _NRI_COUNTRY_MAP:
            codes.append(_NRI_COUNTRY_MAP[token])
        # Bare 2-char ISO code not in the map (e.g. "AE", "SG", "CA")
        elif len(token) == 2 and token.upper().isalpha():
            codes.append(token.upper())
        else:
            # partial match for longer names (e.g. "united arab" hits "united arab emirates")
            match = next((v for k, v in _NRI_COUNTRY_MAP.items() if token in k), None)
            if match:
                codes.append(match)
    # dedup, preserve order
    seen: set[str] = set()
    return [c for c in codes if not (c in seen or seen.add(c))]  # type: ignore[func-returns-value]


# ── Curated luxury-real-estate starting audience ─────────────────────────────
# Plain Meta interest names. Resolved live; any name Meta doesn't recognise is
# silently skipped, so it's safe to list aspirational extras here.
DEFAULT_INTERESTS: list[str] = [
    "Luxury goods",
    "Real estate investing",
    "Wealth management",
    "Investment",
    "Private banking",
    "Luxury vehicles",
    "Interior design",
    "Entrepreneurship",
]

# Behaviour names (Meta's "behaviors" taxonomy). Best-effort — skipped if unmatched.
DEFAULT_BEHAVIOURS: list[str] = [
    "Frequent international travellers",  # Meta uses British spelling
]

# ── Clientele → targeting profile ────────────────────────────────────────────
# Each property's buyer clientele (chosen on the campaign form) tunes the starting
# audience: which interests lead, what age band, and whether NRI geo is implied.
# This is the "different clientele for different projects" rule — a bungalow HNI is
# NOT a flat buyer, so we never share one type's audience with another's.
# `interests` here REPLACE DEFAULT_INTERESTS for that clientele (names resolved live;
# unmatched names are skipped). `age_min/age_max` override the defaults below.
CLIENTELE_TARGETING_MAP: dict[str, dict] = {
    "luxury_bungalow": {
        "label": "Luxury Bungalow / Villa",
        "interests": [
            "Luxury goods", "Luxury vehicles", "Private banking", "Wealth management",
            "Fine dining", "Golf", "Interior design", "Art",
        ],
        "behaviours": ["Frequent international travellers"],
        "age_min": 38, "age_max": 65,
    },
    "premium_apartment": {
        "label": "Premium Apartment",
        "interests": [
            "Real estate investing", "Investment", "Interior design",
            "Entrepreneurship", "Technology", "Personal finance", "Luxury goods",
        ],
        "behaviours": [],
        "age_min": 28, "age_max": 50,
    },
    "nri_investment": {
        "label": "NRI Investment",
        "interests": [
            "Real estate investing", "Investment", "Wealth management",
            "Property", "Non-resident Indian", "Expatriate",
        ],
        "behaviours": ["Frequent international travellers", "Expats (All)"],
        "age_min": 30, "age_max": 60,
    },
    "commercial_office": {
        "label": "Commercial / Office",
        "interests": [
            "Real estate investing", "Investment", "Entrepreneurship",
            "Small business", "Commercial property", "Business",
        ],
        "behaviours": ["Small business owners"],
        "age_min": 30, "age_max": 60,
    },
}
DEFAULT_CLIENTELE = "premium_apartment"


def clientele_profile(clientele_type: str) -> dict:
    """Return the targeting profile for a clientele type (falls back to the default)."""
    return CLIENTELE_TARGETING_MAP.get(
        (clientele_type or "").strip().lower(),
        CLIENTELE_TARGETING_MAP[DEFAULT_CLIENTELE],
    )

# City radius bounds Meta enforces for kilometre targeting.
_RADIUS_MIN_KM = 17
_RADIUS_MAX_KM = 80
DEFAULT_RADIUS_KM = 25

DEFAULT_AGE_MIN = 28
DEFAULT_AGE_MAX = 65

_CACHE_PATH = pathlib.Path("outputs") / "targeting_cache.json"


# ── low-level HTTP + cache ───────────────────────────────────────────────────
def _get(params: dict[str, Any], token: str) -> list[dict]:
    """GET the Targeting Search endpoint, return the `data` list (empty on error-free miss)."""
    q = dict(params)
    q["access_token"] = token
    url = f"{_BASE}/search?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read()).get("data", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Targeting search failed [{e.code}]: {body}") from e


def _load_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except OSError:
        pass  # cache is an optimisation; never fail a deploy over it


# ── search (typeahead) — uncached, returns several candidates ────────────────
def search_interests(query: str, token: str, limit: int = 8) -> list[dict]:
    """Return interest candidates: [{id, name, audience_size}]."""
    out = []
    for d in _get({"type": "adinterest", "q": query, "limit": limit}, token):
        out.append({
            "id": str(d["id"]),
            "name": d.get("name", ""),
            "audience_size": d.get("audience_size_upper_bound")
                             or d.get("audience_size_lower_bound") or 0,
        })
    return out


def search_cities(query: str, token: str, country: str = "IN", limit: int = 8) -> list[dict]:
    """Return city candidates: [{key, name, region, country_code}] limited to one country."""
    params = {
        "type": "adgeolocation",
        "location_types": json.dumps(["city"]),
        "q": query,
        "limit": limit,
    }
    out = []
    for d in _get(params, token):
        if country and d.get("country_code") != country:
            continue
        out.append({
            "key": str(d["key"]),
            "name": d.get("name", ""),
            "region": d.get("region", ""),
            "country_code": d.get("country_code", ""),
        })
    return out


def search_behaviours(query: str, token: str, limit: int = 8) -> list[dict]:
    """Return behaviour candidates: [{id, name, audience_size}]."""
    params = {"type": "adTargetingCategory", "class": "behaviors", "limit": 2000}
    ql = query.lower()
    out = []
    for d in _get(params, token):
        if ql and ql not in d.get("name", "").lower():
            continue
        out.append({
            "id": str(d["id"]),
            "name": d.get("name", ""),
            "audience_size": d.get("audience_size_upper_bound")
                             or d.get("audience_size_lower_bound") or 0,
        })
        if len(out) >= limit:
            break
    return out


# ── resolve (best single match) — cached ─────────────────────────────────────
def _best_interest(name: str, token: str, cache: dict) -> dict | None:
    key = f"interest::{name.lower()}"
    if key in cache:
        return cache[key] or None
    hits = search_interests(name, token, limit=5)
    # Prefer an exact (case-insensitive) name match, else the top result.
    match = next((h for h in hits if h["name"].lower() == name.lower()), hits[0] if hits else None)
    result = {"id": match["id"], "name": match["name"]} if match else None
    cache[key] = result
    return result


def _best_city(name: str, token: str, country: str, cache: dict) -> dict | None:
    key = f"city::{country}::{name.lower()}"
    if key in cache:
        return cache[key] or None
    hits = search_cities(name, token, country=country, limit=5)
    match = next((h for h in hits if h["name"].lower() == name.lower()), hits[0] if hits else None)
    result = {"key": match["key"], "name": match["name"], "region": match.get("region", "")} if match else None
    cache[key] = result
    return result


def _best_behaviour(name: str, token: str, cache: dict) -> dict | None:
    key = f"behaviour::{name.lower()}"
    if key in cache:
        return cache[key] or None
    hits = search_behaviours(name, token, limit=5)
    match = next((h for h in hits if h["name"].lower() == name.lower()), hits[0] if hits else None)
    result = {"id": match["id"], "name": match["name"]} if match else None
    cache[key] = result
    return result


def build_default_audience(city: str, token: str, *, locality: str = "",
                           country: str = "IN",
                           nri_geographies: str = "",
                           clientele_type: str = "") -> dict:
    """
    Resolve the curated starting audience for a campaign.

    `clientele_type` (luxury_bungalow | premium_apartment | nri_investment |
    commercial_office) picks the interest/behaviour/age profile so a bungalow HNI
    audience never leaks into a flat campaign. When omitted, the generic
    DEFAULT_INTERESTS list is used (backward-compatible with pre-clientele runs).

    Returns an editable audience dict:
      {city, city_key, region, radius_km, age_min, age_max,
       interests:[{id,name}], behaviours:[{id,name}], country, clientele_type}
    city_key is None if the city couldn't be resolved (deploy then falls back to
    country-level — see meta_tool.build_targeting_spec).
    """
    cache = _load_cache()

    resolved_city = _best_city(city, token, country, cache) if city and city != "India" else None

    if clientele_type:
        profile = clientele_profile(clientele_type)
        interest_names = profile["interests"]
        behaviour_names = profile["behaviours"]
        age_min, age_max = profile["age_min"], profile["age_max"]
    else:
        interest_names = DEFAULT_INTERESTS
        behaviour_names = DEFAULT_BEHAVIOURS
        age_min, age_max = DEFAULT_AGE_MIN, DEFAULT_AGE_MAX

    interests = []
    for nm in interest_names:
        hit = _best_interest(nm, token, cache)
        if hit and hit not in interests:
            interests.append(hit)

    behaviours = []
    for nm in behaviour_names:
        try:
            hit = _best_behaviour(nm, token, cache)
        except RuntimeError:
            hit = None  # behaviours taxonomy is best-effort
        if hit and hit not in behaviours:
            behaviours.append(hit)

    _save_cache(cache)

    nri_countries = parse_nri_countries(nri_geographies) if nri_geographies else []

    return {
        "country": country,
        "city": resolved_city["name"] if resolved_city else "",
        "city_key": resolved_city["key"] if resolved_city else None,
        "region": resolved_city.get("region", "") if resolved_city else "",
        "radius_km": DEFAULT_RADIUS_KM,
        "age_min": age_min,
        "age_max": age_max,
        "interests": interests,
        "behaviours": behaviours,
        "nri_countries": nri_countries,
        "clientele_type": (clientele_type or "").strip().lower() or DEFAULT_CLIENTELE,
    }


def build_targeting_spec(audience: dict) -> dict:
    """
    Convert an audience dict (from build_default_audience or the edited overlay)
    into the Meta ad-set `targeting` spec.

    Falls back to country-level geo if no city_key is present, and omits the
    interest/behaviour group entirely if both are empty (so we never send an
    empty flexible_spec, which Meta rejects).
    """
    age_min = int(audience.get("age_min") or DEFAULT_AGE_MIN)
    age_max = int(audience.get("age_max") or DEFAULT_AGE_MAX)
    country = audience.get("country") or "IN"

    spec: dict[str, Any] = {
        "age_min": age_min,
        "age_max": age_max,
        # Honour the exact audience below rather than letting Meta expand it.
        "targeting_automation": {"advantage_audience": 0},
    }

    city_key = audience.get("city_key")
    nri_countries = [c for c in (audience.get("nri_countries") or []) if c]
    geo: dict[str, Any] = {}
    if city_key:
        radius = int(audience.get("radius_km") or DEFAULT_RADIUS_KM)
        radius = max(_RADIUS_MIN_KM, min(_RADIUS_MAX_KM, radius))
        geo["cities"] = [{"key": str(city_key), "radius": radius, "distance_unit": "kilometer"}]
    else:
        geo["countries"] = [country]
    # NRI diaspora countries sit alongside the city in the same geo_locations block.
    # Meta unions them: reach people in the city radius OR in any of these countries.
    if nri_countries:
        existing = geo.get("countries", [])
        geo["countries"] = list(dict.fromkeys(existing + nri_countries))
    spec["geo_locations"] = geo

    group: dict[str, Any] = {}
    interests = [{"id": str(i["id"]), "name": i.get("name", "")}
                 for i in audience.get("interests", []) if i.get("id")]
    behaviours = [{"id": str(b["id"]), "name": b.get("name", "")}
                  for b in audience.get("behaviours", []) if b.get("id")]
    if interests:
        group["interests"] = interests
    if behaviours:
        group["behaviors"] = behaviours
    if group:
        spec["flexible_spec"] = [group]

    inc = [{"id": str(a["id"])} for a in audience.get("included_custom_audiences", []) if a.get("id")]
    exc = [{"id": str(a["id"])} for a in audience.get("excluded_custom_audiences", []) if a.get("id")]
    if inc:
        spec["custom_audiences"] = inc
    if exc:
        spec["excluded_custom_audiences"] = exc

    return spec


# ── CRM-profile → Meta-interest mapping (autopilot rung 7) ───────────────────
# Maps the industries crm_analytics.top_converting_profiles() produces onto Meta
# interest names. Used to lean targeting toward whatever profile actually converts
# for THIS campaign's clientele — never across clienteles.
_INDUSTRY_TO_INTERESTS: dict[str, list[str]] = {
    "IT/Tech": ["Technology", "Software", "Information technology"],
    "Finance/Banking": ["Investment", "Personal finance", "Private banking"],
    "Business/Entrepreneur": ["Entrepreneurship", "Small business", "Business"],
    "Medical/Healthcare": ["Medicine", "Health care"],
    "Real Estate": ["Real estate investing", "Property"],
    "Government/PSU": ["Investment", "Wealth management"],
    "NRI": ["Non-resident Indian", "Real estate investing", "Investment"],
    "Retired": ["Wealth management", "Investment"],
}


def interests_from_crm_profiles(top_profiles: list[dict], token: str,
                                cache: dict | None = None, limit: int = 4) -> list[dict]:
    """
    Resolve Meta interest {id,name} dicts from CRM top-converting profiles.

    `top_profiles` is the output of crm_analytics.top_converting_profiles() —
    each has profile.industry. We map those industries to interest names and
    resolve the best Meta match, highest-quality profile first. Returns at most
    `limit` resolved interests, de-duplicated. Never raises.
    """
    cache = _load_cache() if cache is None else cache
    out: list[dict] = []
    seen_ids: set[str] = set()
    for prof in top_profiles or []:
        industry = (prof.get("profile", {}) or {}).get("industry", "")
        for nm in _INDUSTRY_TO_INTERESTS.get(industry, []):
            try:
                hit = _best_interest(nm, token, cache)
            except RuntimeError:
                hit = None
            if hit and hit["id"] not in seen_ids:
                out.append(hit)
                seen_ids.add(hit["id"])
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    _save_cache(cache)
    return out


def audience_summary(audience: dict) -> str:
    """One-line human summary for the deploy preview / logs."""
    geo = audience.get("city") or audience.get("country") or "India"
    if audience.get("city") and audience.get("city_key"):
        geo = f"{audience['city']} +{audience.get('radius_km', DEFAULT_RADIUS_KM)}km"
    nri = audience.get("nri_countries") or []
    n_int = len(audience.get("interests", []))
    n_beh = len(audience.get("behaviours", []))
    n_inc = len(audience.get("included_custom_audiences", []))
    n_exc = len(audience.get("excluded_custom_audiences", []))
    bits = [geo]
    if nri:
        bits.append("+ " + ", ".join(nri))
    bits.append(f"Age {audience.get('age_min', DEFAULT_AGE_MIN)}–{audience.get('age_max', DEFAULT_AGE_MAX)}")
    if n_int:
        bits.append(f"{n_int} interest{'s' if n_int != 1 else ''}")
    if n_beh:
        bits.append(f"{n_beh} behaviour{'s' if n_beh != 1 else ''}")
    if n_inc:
        bits.append(f"{n_inc} custom audience{'s' if n_inc != 1 else ''}")
    if n_exc:
        bits.append(f"{n_exc} excluded")
    return " · ".join(bits)
