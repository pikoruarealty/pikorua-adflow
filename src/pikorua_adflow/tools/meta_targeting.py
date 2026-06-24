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

# ── Shared targeting building blocks ─────────────────────────────────────────
# IDs extracted directly from live Pikorua campaigns (verified 2026-06-23).
# Performance-validated — CPL benchmarks are annotated where available.
# Hardcoded so they don't need an API lookup on every deploy.

# Income cluster — present in ALL 4 active Pikorua campaigns.  The single
# highest-leverage filter for luxury real estate: India's top-10% earners.
_INCOME_TOP_10 = [{"id": "6661019705983", "name": "Household income (India): Top 10%"}]

# Owner / founder work positions — from the BEST bungalow campaign (₹220/lead).
# IDs 138434539530345 and 149598488387016 were verified in the ₹220 CPL ad set.
# These two positions outperform longer C-suite lists because they match the actual
# Ahmedabad luxury buyer: a self-employed business owner, not a corporate executive.
_WORK_POSITIONS_OWNERS = [
    {"id": "138434539530345", "name": "Owner/Managing Director"},   # ₹220 CPL bungalow
    {"id": "149598488387016", "name": "Owner and CEO"},              # ₹220 CPL bungalow
    {"id": "143727678985148", "name": "Founder and Managing Director"},
    {"id": "133337610036491", "name": "Founder, Director, CEO"},
    {"id": "149657818421934", "name": "Director (business)"},
]

# C-suite positions — only for apartment/corporate profiles (NN campaign, ₹609 CPL).
# NOTE: using too many of these narrows audience too aggressively; prefer 3-4 max.
_WORK_POSITIONS_CSUITE = [
    {"id": "112451425436956",  "name": "Vice President"},
    {"id": "112558655421889",  "name": "Chief financial officer"},
    {"id": "133337610036491",  "name": "Founder, Director, CEO"},
    {"id": "1379551615695666", "name": "Executive Vice President (EVP)"},
]
# IT-specific C-suite — AVOID for luxury real estate (used in ₹967 CPL GODREJ campaign).
# Listed here for reference; not added to any clientele profile.
_WORK_POSITIONS_IT_CSUITE_AVOID = [
    {"id": "103111706395693",  "name": "Chief Administrative Officer"},  # IT campaign ₹967 CPL
    {"id": "104016006301659",  "name": "Chief marketing officer"},
    {"id": "621959927947441",  "name": "Chief Information Officer (CIO)"},
    {"id": "1597325863845095", "name": "Information Technology Director"},
]
_WORK_POSITION_PROPERTY  = [{"id": "108900439134105", "name": "Property"}]
_WORK_POSITION_CA        = [{"id": "105525432814378", "name": "Chartered accountant"}]

# B2B industry overlay — large enterprise employees.  Present in ₹220 CPL bungalow campaign.
_INDUSTRIES_ENTERPRISE = [{"id": "6075565069783", "name": "Large business-to-business enterprise employees (500+ employees)"}]


# ── Clientele → targeting profile ────────────────────────────────────────────
# Schema per entry:
#   interests      — plain-text names, resolved live via Meta Targeting Search API.
#                    Any name Meta doesn't recognise is silently skipped.
#   behaviours     — plain-text names, resolved live.
#   work_positions — pre-resolved {id, name} dicts (no API call needed at deploy).
#   income_clusters— pre-resolved {id, name} dicts for user_adclusters.
#   age_min/max    — override defaults.
#
# Learnings from live campaign analysis (2026-06-23):
#   - Every active campaign uses Household income Top 10% — always include it.
#   - Every active campaign uses Engaged Shoppers behaviour.
#   - Bungalow campaign (₹232/lead) outperforms apartment (₹484/lead) because it
#     uses work_positions MD/Owner/Director + specific "Bungalow" interest.
#   - Apartment/engagement campaigns use Chartered Accountant education + C-suite
#     work_positions for the highest-quality professional buyer.
#   - Trader/industrialist campaigns use Textile, Manufacturing, Export interests.
CLIENTELE_TARGETING_MAP: dict[str, dict] = {

    "luxury_bungalow": {
        # Owner-occupier HNI — Ahmedabad business owner or MD buying a ₹5 Cr+ residence.
        # Benchmark: ₹220 CPL (best campaign in account — "bungalow ahmd general").
        # Mirror of that campaign's exact flexible_spec: 12 interests + 2 behaviours +
        # 2 owner work positions + income Top 10% + B2B enterprise industries.
        # Lesson: simple owner positions (Owner/MD + Owner/CEO) outperform long C-suite lists.
        "label": "Luxury Bungalow / Villa — HNI owner-occupier (₹5 Cr+)",
        "interests": [
            "Small business (business and finance)",           # verified in ₹220 CPL campaign
            "Bungalow",                                        # ID 6003011972081
            "First-class travel (travel and tourism business)",# ID 6003076027139
            "Apartment (property)",                            # ID 6003103732434 (in-market signal)
            "Investment management (investing)",               # ID 6003293787730
            "Business class (air travel)",                     # ID 6003352779232
            "Luxury resorts (lodging)",                        # ID 6003383552337
            "Luxury Lifestyle (website)",                      # ID 6003392552125
            "Property investing (investing)",                  # ID 6003446239080
            "Investor (investing)",                            # ID 6003587074473
            "Personal luxury car",                             # ID 6003755914953
            "Luxury vehicle (vehicles)",                       # ID 6004048615096
        ],
        "behaviours": [
            "Frequent international travelers",  # ID 6022788483583
            "Engaged Shoppers",                  # ID 6071631541183
        ],
        "work_positions": [
            {"id": "138434539530345", "name": "Owner/Managing Director"},  # ₹220 CPL verified
            {"id": "149598488387016", "name": "Owner and CEO"},             # ₹220 CPL verified
        ],
        "industries": _INDUSTRIES_ENTERPRISE,
        "income_clusters": _INCOME_TOP_10,
        "age_min": 36, "age_max": 65,
    },

    "premium_apartment": {
        # Corporate professional / senior employee — first or second premium flat, 1.5–4 Cr.
        # Benchmark: LAARGE Apts ₹376 CPL beats NN ₹609 CPL — key insight is MORE interests
        # (11) + fewer work positions outperforms FEWER interests (3) + MANY C-suite positions.
        # Strategy: broad aspirational interests + married filter + Lived in India (expat signal)
        # + Engaged Shoppers. Do NOT stack 11 specific C-suite titles — it over-narrows the pool.
        "label": "Premium Apartment — corporate professional (₹1.5–4 Cr)",
        "interests": [
            "Property investment trust (investing)",    # verified in LAARGE ₹376 CPL
            "First-class travel (travel and tourism business)",
            "Apartment (property)",                    # exact signal for apartment buyer
            "luxury (lifestyle content)",              # verified in LAARGE + GODREJ
            "creative property investing (property)",  # verified in LAARGE
            "Property investment club (club)",
            "Investment (business and finance)",
            "Luxury Lifestyle (website)",
            "Luxury vehicle (vehicles)",
            "Luxury goods (retail)",
        ],
        "behaviours": [
            "Frequent Travelers",                             # verified in LAARGE ₹376 CPL
            "Lived in India (Formerly Expats - India)",       # verified in LAARGE ₹376 CPL
            "Frequent international travelers",
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITION_PROPERTY,           # only "Property" — LAARGE style
        "relationship_statuses": [3],                         # married — verified in LAARGE + NN
        "income_clusters": _INCOME_TOP_10,
        "age_min": 35, "age_max": 55,
    },

    "trader_industrialist": {
        # Ahmedabad's dominant buyer: diamond / textile / FMCG / pharma / manufacturing trader.
        # Self-made wealth, upgrading from an older property to a premium bungalow.
        # Based on PIKORUA Engagement campaign (best trader interest stack seen in account).
        # Key signals: Textile, Manufacturing, Export + Owner/MD work positions + married.
        "label": "Trader / Industrialist — Gujarati business community upgrade (₹5 Cr+)",
        "interests": [
            "Textile (craft supplies)",           # verified in engagement campaign
            "Manufacturing (industry)",           # verified in engagement campaign
            "Export",                             # verified in engagement campaign
            "International business",             # verified in engagement campaign
            "Small and medium enterprises (business and finance)",
            "Property investing (investing)",
            "Luxury vehicle (vehicles)",
            "Luxury goods (retail)",
            "Investment (business and finance)",
        ],
        "behaviours": [
            "Frequent international travelers",
            "Frequent Travelers",
            "Small business owners",
            "Engaged Shoppers",
            "Returned from travels 2 weeks ago",  # verified in engagement campaign — post-travel = planning mindset
        ],
        "work_positions": [
            {"id": "874842615892965",  "name": "Managing Director"},
            {"id": "143727678985148",  "name": "Founder and Managing Director"},
            {"id": "133337610036491",  "name": "Founder, Director, CEO"},
            {"id": "110722838955052",  "name": "Owner"},
            {"id": "149657818421934",  "name": "Director (business)"},
        ],
        "income_clusters": _INCOME_TOP_10,
        "age_min": 42, "age_max": 68,
    },

    "nri_investment": {
        # Diaspora buyer — yield/portfolio purchase, not primary use.
        # Uses Lived in India (Formerly Expats) behaviour verified in LAARGE campaign.
        "label": "NRI Investor — diaspora yield / portfolio buyer (₹2–6 Cr)",
        "interests": [
            "Property investing (investing)",
            "Investment (business and finance)",
            "Luxury Lifestyle (website)",
            "luxury (lifestyle content)",
            "Luxury goods (retail)",
            "First-class travel (travel and tourism business)",
        ],
        "behaviours": [
            "Frequent international travelers",
            "Expats (All)",
            "Lived in India (Formerly Expats - India)",  # verified in LAARGE + Engagement campaigns
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITIONS_OWNERS,
        "income_clusters": _INCOME_TOP_10,
        "age_min": 30, "age_max": 60,
    },

    "commercial_office": {
        # Business buying office / commercial space as asset or for own use.
        "label": "Commercial / Office — business asset buyer (₹2 Cr+)",
        "interests": [
            "Property investing (investing)",
            "Investment (business and finance)",
            "Small and medium enterprises (business and finance)",
            "International business",
            "Entrepreneurship",
            "Luxury goods (retail)",
        ],
        "behaviours": [
            "Small business owners",
            "Frequent international travelers",
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITIONS_OWNERS,
        "income_clusters": _INCOME_TOP_10,
        "age_min": 30, "age_max": 60,
    },

    "it_professional": {
        # GIFT City, Infosys, TCS, Wipro or startup employee — first/second home, 1–3.5 Cr.
        # IT Director / CIO work positions from GODREJ campaign are the key differentiator.
        "label": "IT / Tech Professional — GIFT City / tech park buyer (₹1–3.5 Cr)",
        "interests": [
            "Apartment (property)",
            "Investment (business and finance)",
            "Property investing (investing)",
            "Personal finance",
            "Interior design",
            "Luxury Lifestyle (website)",
        ],
        "behaviours": [
            "Frequent Travelers",
            "Engaged Shoppers",
        ],
        "work_positions": [
            {"id": "1597325863845095", "name": "Information Technology Director"},
            {"id": "621959927947441",  "name": "Chief Information Officer (CIO)"},
            {"id": "106236979408167",  "name": "Chief information officer"},
            {"id": "133337610036491",  "name": "Founder, Director, CEO"},
            {"id": "112451425436956",  "name": "Vice President"},
        ],
        "income_clusters": _INCOME_TOP_10,
        "age_min": 26, "age_max": 44,
    },

    "doctor_professional": {
        # Medical professionals — independent home buyers, typically bungalows or premium floors.
        # High income, trust word-of-mouth, respond to exclusivity and privacy messaging.
        "label": "Doctor / Healthcare Professional — independent home buyer (₹3–8 Cr)",
        "interests": [
            "Bungalow",
            "Property investing (investing)",
            "Luxury goods (retail)",
            "First-class travel (travel and tourism business)",
            "Luxury vehicle (vehicles)",
            "Interior design",
            "Investment (business and finance)",
        ],
        "behaviours": [
            "Frequent international travelers",
            "Engaged Shoppers",
        ],
        "work_positions": [
            {"id": "105563979478424", "name": "Executive director"},   # proxies for senior doctors
            {"id": "112558655421889", "name": "Chief financial officer"},
        ],
        "income_clusters": _INCOME_TOP_10,
        "age_min": 32, "age_max": 58,
    },

    "hni_portfolio_investor": {
        # Pure investor — yield/hold/flip.  Responds to CAGR, appreciation, rental yield data.
        # Overlaps with luxury_bungalow on demographics but different decision triggers.
        "label": "HNI Portfolio Investor — multi-property / yield-seeker (₹4 Cr+)",
        "interests": [
            "Property investing (investing)",
            "Property investment trust (investing)",  # verified in LAARGE + Engagement campaigns
            "creative property investing (property)",  # verified in LAARGE
            "Investment (business and finance)",
            "Stock (investing)",                        # verified in Engagement campaign
            "Investor (investing)",
            "Luxury goods (retail)",
            "First-class travel (travel and tourism business)",
        ],
        "behaviours": [
            "Frequent international travelers",
            "Frequent Travelers",
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITIONS_OWNERS,
        "income_clusters": _INCOME_TOP_10,
        "age_min": 38, "age_max": 65,
    },

    "affordable_luxury": {
        # Young professional buying their first premium home — stepping up to a branded dev.
        # More price-sensitive; EMI, possession timeline, and location matter most.
        "label": "Affordable Luxury — first premium home, young professional (₹1–2.5 Cr)",
        "interests": [
            "Apartment (property)",
            "Property investing (investing)",
            "Luxury Lifestyle (website)",
            "Interior design",
            "Personal finance",
            "Investment (business and finance)",
        ],
        "behaviours": [
            "Frequent Travelers",
            "Small business owners",
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITION_CA + [
            {"id": "112451425436956", "name": "Vice President"},
        ],
        "income_clusters": _INCOME_TOP_10,
        "advantage_plus": False,  # disabled: broader/younger audience = higher cheap-form-fill risk
        "age_min": 26, "age_max": 42,
    },

    "nri_end_user": {
        # NRI buying for own / family use — parents' home, children's school base.
        # Emotional driver, not yield.  Lived in India behaviour is the clearest signal.
        "label": "NRI End User — homeland / family home buyer (₹2–5 Cr)",
        "interests": [
            "Apartment (property)",
            "Property investing (investing)",
            "luxury (lifestyle content)",
            "First-class travel (travel and tourism business)",
            "Interior design",
            "Investment (business and finance)",
        ],
        "behaviours": [
            "Frequent international travelers",
            "Expats (All)",
            "Lived in India (Formerly Expats - India)",
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITIONS_OWNERS,
        "income_clusters": _INCOME_TOP_10,
        "age_min": 32, "age_max": 58,
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
        interest_names  = profile["interests"]
        behaviour_names = profile["behaviours"]
        age_min, age_max = profile["age_min"], profile["age_max"]
        # Pre-resolved fields — passed straight through, no API lookup needed.
        work_positions        = profile.get("work_positions", [])
        income_clusters       = profile.get("income_clusters", [])
        industries            = profile.get("industries", [])
        relationship_statuses = profile.get("relationship_statuses", [])
        advantage_plus        = profile.get("advantage_plus", True)  # default on; profiles can opt out
    else:
        interest_names  = DEFAULT_INTERESTS
        behaviour_names = DEFAULT_BEHAVIOURS
        age_min, age_max = DEFAULT_AGE_MIN, DEFAULT_AGE_MAX
        work_positions = income_clusters = industries = []
        relationship_statuses = []
        advantage_plus = True

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
        "work_positions": work_positions,
        "income_clusters": income_clusters,
        "industries": industries,
        "relationship_statuses": relationship_statuses,
        "advantage_plus": advantage_plus,
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
        # Advantage+ on by default — Meta expands on interests to find converters.
        # Verified safe: ₹220 CPL bungalow uses advantage_audience=1 without locks.
        # affordable_luxury profile explicitly sets advantage_plus=False to prevent
        # cheap form-fillers in that younger/broader audience segment.
        "targeting_automation": {"advantage_audience": 1 if audience.get("advantage_plus", True) else 0},
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

    # work_positions — Facebook job titles from profile data; pre-resolved {id, name} dicts.
    work_positions = [{"id": str(w["id"]), "name": w.get("name", "")}
                      for w in audience.get("work_positions", []) if w.get("id")]
    if work_positions:
        group["work_positions"] = work_positions

    # user_adclusters — Meta income / lifestyle clusters; pre-resolved {id, name} dicts.
    income_clusters = [{"id": str(u["id"]), "name": u.get("name", "")}
                       for u in audience.get("income_clusters", []) if u.get("id")]
    if income_clusters:
        group["user_adclusters"] = income_clusters

    # industries — Facebook industry segments; pre-resolved {id, name} dicts.
    industries = [{"id": str(ind["id"]), "name": ind.get("name", "")}
                  for ind in audience.get("industries", []) if ind.get("id")]
    if industries:
        group["industries"] = industries

    if group:
        spec["flexible_spec"] = [group]

    # relationship_statuses — top-level (not inside flexible_spec).
    # 3 = married.  See Meta marketing API docs for full enum.
    rel_statuses = [int(r) for r in (audience.get("relationship_statuses") or [])
                    if str(r).isdigit()]
    if rel_statuses:
        spec["relationship_statuses"] = rel_statuses

    inc = [{"id": str(a["id"])} for a in audience.get("included_custom_audiences", []) if a.get("id")]
    exc = [{"id": str(a["id"])} for a in audience.get("excluded_custom_audiences", []) if a.get("id")]
    if inc:
        spec["custom_audiences"] = inc
    if exc:
        spec["excluded_custom_audiences"] = exc

    return spec


# ── CRM-profile → Meta-interest mapping (autooptimiser rung 7) ───────────────────
# Maps the industries crm_analytics.top_converting_profiles() produces onto Meta
# interest names. Used to lean targeting toward whatever profile actually converts
# for THIS campaign's clientele — never across clienteles.
_INDUSTRY_TO_INTERESTS: dict[str, list[str]] = {
    # Names verified to resolve against live Meta campaigns (2026-06-23).
    # Ordered by relevance; the resolver stops at `limit` (default 4) resolved hits.
    "IT/Tech":             [
        "Property investing (investing)",     # primary intent signal
        "Luxury Lifestyle (website)",
        "Apartment (property)",
        "Investment (business and finance)",
    ],
    "Finance/Banking":     [
        "Property investing (investing)",
        "Investor (investing)",
        "Property investment trust (investing)",
        "First-class travel (travel and tourism business)",
    ],
    "Business/Entrepreneur": [
        "Bungalow",
        "Property investing (investing)",
        "Luxury vehicle (vehicles)",
        "First-class travel (travel and tourism business)",
    ],
    "Medical/Healthcare":  [
        "Bungalow",
        "Property investing (investing)",
        "Luxury goods (retail)",
        "Luxury vehicle (vehicles)",
    ],
    "Real Estate":         [
        "Property investing (investing)",
        "Property investment trust (investing)",
        "creative property investing (property)",
        "Investor (investing)",
    ],
    "Trader/Industrialist": [
        "Textile (craft supplies)",
        "Manufacturing (industry)",
        "Property investing (investing)",
        "Luxury vehicle (vehicles)",
    ],
    "Government/PSU":      [
        "Investment (business and finance)",
        "Property investing (investing)",
        "Luxury Lifestyle (website)",
    ],
    "NRI":                 [
        "Property investing (investing)",
        "First-class travel (travel and tourism business)",
        "Luxury Lifestyle (website)",
        "luxury (lifestyle content)",
    ],
    "Retired":             [
        "Property investment trust (investing)",
        "Investor (investing)",
        "Luxury goods (retail)",
        "Luxury vehicle (vehicles)",
    ],
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
