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
import re
import time
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
    {"id": "103113219728224",  "name": "Chief executive officer"},  # verified generic CEO title
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

    # ── Simplified 3-option profiles ─────────────────────────────────────────
    # These are the primary options shown in the campaign form.  They merge the
    # best-verified signals from the granular profiles above.

    "hni": {
        # All domestic HNI buyers — business owner, trader, doctor, professional,
        # investor.  Uses the signals verified across the best-performing campaigns
        # (₹220 CPL bungalow, ₹376 CPL LAARGE apartment).
        "label": "HNI",
        "interests": [
            "Small business (business and finance)",      # ₹220 CPL verified
            "Bungalow",                                   # ID 6003011972081
            "Property investing (investing)",             # cross-verified in all HNI campaigns
            "Property investment trust (investing)",      # ₹376 CPL LAARGE verified
            "Investment (business and finance)",
            "Investor (investing)",
            "First-class travel (travel and tourism business)",
            "Luxury goods (retail)",
            "Luxury vehicle (vehicles)",
            "Luxury Lifestyle (website)",
        ],
        "behaviours": [
            "Frequent international travelers",
            "Small business owners",
            "Frequent Travelers",
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITIONS_OWNERS,
        "industries": _INDUSTRIES_ENTERPRISE,
        "income_clusters": _INCOME_TOP_10,
        "age_min": 28, "age_max": 68,
    },

    "nri": {
        # All NRI buyers — investor or end-user.  Expat behaviours are the primary
        # differentiator; Lived in India is the single strongest NRI signal (LAARGE verified).
        "label": "NRI",
        "interests": [
            "Property investing (investing)",
            "Property investment trust (investing)",
            "Investment (business and finance)",
            "Luxury Lifestyle (website)",
            "luxury (lifestyle content)",
            "First-class travel (travel and tourism business)",
            "Apartment (property)",
            "Interior design",
        ],
        "behaviours": [
            "Expats (All)",
            "Lived in India (Formerly Expats - India)",   # strongest NRI signal
            "Frequent international travelers",
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITIONS_OWNERS,
        "income_clusters": _INCOME_TOP_10,
        "age_min": 28, "age_max": 62,
    },

    "hni_nri": {
        # HNI + NRI combined — broadest luxury real estate audience.
        # Includes both domestic owner/investor signals and expat signals.
        "label": "HNI + NRI",
        "interests": [
            "Small business (business and finance)",
            "Bungalow",
            "Property investing (investing)",
            "Property investment trust (investing)",
            "Investment (business and finance)",
            "Investor (investing)",
            "First-class travel (travel and tourism business)",
            "Luxury goods (retail)",
            "Luxury vehicle (vehicles)",
            "Luxury Lifestyle (website)",
            "Apartment (property)",
            "Interior design",
        ],
        "behaviours": [
            "Expats (All)",
            "Lived in India (Formerly Expats - India)",
            "Frequent international travelers",
            "Small business owners",
            "Frequent Travelers",
            "Engaged Shoppers",
        ],
        "work_positions": _WORK_POSITIONS_OWNERS,
        "industries": _INDUSTRIES_ENTERPRISE,
        "income_clusters": _INCOME_TOP_10,
        "age_min": 28, "age_max": 68,
    },
}
DEFAULT_CLIENTELE = "hni"


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

# Meta occasionally retires/renames interest & behaviour IDs (e.g. the "Interior
# design" id 6003263790983 that caused the Jul 1-2 deploy failures) — an
# unbounded cache keeps serving a dead id forever. Force a re-resolve after this
# many seconds so a retired id gets flushed out on its own.
_CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days

_MISS = object()  # sentinel: distinct from a legitimately cached "no match" (None)


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


def _cache_get(cache: dict, key: str) -> Any:
    """Return the cached value for `key`, or `_MISS` if absent/expired/legacy-shaped."""
    entry = cache.get(key)
    if not isinstance(entry, dict) or "value" not in entry or "cached_at" not in entry:
        return _MISS  # missing, or written before the TTL wrapper existed
    if time.time() - entry["cached_at"] > _CACHE_TTL_SECONDS:
        return _MISS
    return entry["value"]


def _cache_set(cache: dict, key: str, value: Any) -> None:
    cache[key] = {"value": value, "cached_at": time.time()}


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


# ── area-level geo: neighbourhoods + pincodes (India-verified) ───────────────
def search_neighborhoods(query: str, token: str, region: str = "", limit: int = 12,
                         strict: bool = False) -> list[dict]:
    """
    Neighbourhood candidates for area-level targeting: [{key, name, region, region_id}].
    Meta returns the same neighbourhood name across states (e.g. "Prahlad Nagar" in
    UP *and* Gujarat). `region` behaviour:
      • strict=True  → keep ONLY same-region hits (used by the auto suggestion row,
        which queries by city name and must never surface a wrong-state area).
      • strict=False → keep all India hits but sort same-region first (manual typeahead;
        the UI shows each result's region so the user disambiguates — Meta sometimes
        mis-tags a valid area's region, e.g. Ahmedabad's "Satellite", so we don't drop).
    """
    params = {
        "type": "adgeolocation",
        "location_types": json.dumps(["neighborhood"]),
        "q": query,
        "limit": limit,
    }
    rl = (region or "").strip().lower()
    out = []
    for d in _get(params, token):
        if d.get("country_code") != "IN":
            continue
        if strict and rl and (d.get("region", "").lower() != rl):
            continue
        out.append({
            "key": str(d["key"]),
            "name": d.get("name", ""),
            "region": d.get("region", ""),
            "region_id": d.get("region_id"),
        })
    if rl and not strict:
        out.sort(key=lambda n: n.get("region", "").lower() != rl)  # same-region first
    return out


def search_zips(query: str, token: str, limit: int = 15) -> list[dict]:
    """Pincode candidates: [{key, name, primary_city, primary_city_id}] (India only)."""
    params = {
        "type": "adgeolocation",
        "location_types": json.dumps(["zip"]),
        "q": query,
        "limit": limit,
    }
    out = []
    for d in _get(params, token):
        if d.get("country_code") != "IN":
            continue
        out.append({
            "key": str(d["key"]),
            "name": d.get("name", ""),
            "primary_city": d.get("primary_city", ""),
            "primary_city_id": str(d.get("primary_city_id") or ""),
        })
    return out


def suggest_neighborhoods_for_city(city_name: str, region: str, city_key: str,
                                   token: str, limit: int = 15) -> list[dict]:
    """Neighbourhoods within a city, for the 'Suggested for {city}' quick-add row.
    Queries by city name and keeps only same-region hits; stamps city_key on each."""
    if not city_name:
        return []
    out = []
    for n in search_neighborhoods(city_name, token, region=region, limit=limit, strict=True):
        out.append({"key": n["key"], "name": n["name"], "region": n.get("region", ""),
                    "city_key": str(city_key or "")})
    return out


# Postal-circle first-3-digit prefixes by Indian state (factual postal geography,
# not an opportunity ranking — the no-hardcoded-cities rule in geo_intelligence is
# about wealth/opportunity tables, which this is not). Used only to enumerate a
# city's pincodes for suggestions; manual pincode add works anywhere via search_zips.
_STATE_ZIP_PREFIXES: dict[str, list[str]] = {
    "gujarat": ["380", "382", "360", "361", "364", "388", "390", "395", "396"],
    "maharashtra": ["400", "410", "411", "413", "440", "422", "431"],
    "delhi": ["110"],
    "karnataka": ["560", "570", "580", "590"],
    "rajasthan": ["302", "313", "324", "342"],
    "madhya pradesh": ["452", "462", "482", "474"],
    "uttar pradesh": ["201", "226", "208", "282"],
    "tamil nadu": ["600", "641", "620", "625"],
    "telangana": ["500", "506"],
    "west bengal": ["700", "711"],
    "haryana": ["122", "121", "124"],
    "punjab": ["141", "143", "160"],
}


def suggest_zips_for_city(city_name: str, city_key: str, region: str, token: str,
                          max_zips: int = 24) -> list[dict]:
    """Pincodes belonging to a city, for the 'Suggested for {city}' quick-add row.
    Probes the state's postal prefixes and keeps only pincodes whose primary_city_id
    matches this city. Returns [] for regions we don't have prefixes for (the UI then
    falls back to neighbourhood suggestions + manual pincode entry)."""
    prefixes = _STATE_ZIP_PREFIXES.get((region or "").strip().lower(), [])
    if not prefixes or not city_key:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for pfx in prefixes:
        if len(out) >= max_zips:
            break
        try:
            hits = search_zips(pfx, token, limit=30)
        except RuntimeError:
            continue
        for z in hits:
            if z["primary_city_id"] == str(city_key) and z["key"] not in seen:
                seen.add(z["key"])
                out.append({"key": z["key"], "name": z["name"], "city_key": str(city_key)})
    return sorted(out, key=lambda z: z["name"])[:max_zips]


# ── resolve (best single match) — cached ─────────────────────────────────────
def _search_query(name: str) -> str:
    """Meta's adinterest/behaviour typeahead frequently returns zero hits when queried
    with the full "Name (category)" string verbatim, but reliably matches on the bare
    name — the trailing parenthetical is a disambiguator for humans, not a search token.
    Only the query is stripped; matching below still compares against the full name."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip() or name


def _best_interest(name: str, token: str, cache: dict) -> dict | None:
    key = f"interest::{name.lower()}"
    cached = _cache_get(cache, key)
    if cached is not _MISS:
        return cached
    hits = search_interests(_search_query(name), token, limit=8)
    # Prefer an exact (case-insensitive) name match, else the top result.
    match = next((h for h in hits if h["name"].lower() == name.lower()), hits[0] if hits else None)
    result = {"id": match["id"], "name": match["name"]} if match else None
    _cache_set(cache, key, result)
    return result


def _best_city(name: str, token: str, country: str, cache: dict) -> dict | None:
    key = f"city::{country}::{name.lower()}"
    cached = _cache_get(cache, key)
    if cached is not _MISS:
        return cached
    hits = search_cities(name, token, country=country, limit=5)
    match = next((h for h in hits if h["name"].lower() == name.lower()), hits[0] if hits else None)
    result = {"key": match["key"], "name": match["name"], "region": match.get("region", "")} if match else None
    _cache_set(cache, key, result)
    return result


def _best_behaviour(name: str, token: str, cache: dict) -> dict | None:
    key = f"behaviour::{name.lower()}"
    cached = _cache_get(cache, key)
    if cached is not _MISS:
        return cached
    hits = search_behaviours(_search_query(name), token, limit=8)
    match = next((h for h in hits if h["name"].lower() == name.lower()), hits[0] if hits else None)
    result = {"id": match["id"], "name": match["name"]} if match else None
    _cache_set(cache, key, result)
    return result


def build_default_audience(city: str, token: str, *, locality: str = "",
                           country: str = "IN",
                           nri_geographies: str = "",
                           clientele_type: str = "",
                           llm_selection: dict | None = None) -> dict:
    """
    Resolve the curated starting audience for a campaign.

    `llm_selection` — a pre-validated dict from resolve_llm_targeting(), i.e. the
    AudienceCrew's per-campaign targeting pick. When present, its interests/
    behaviours/work_positions/age_min/age_max are used INSTEAD OF the static
    clientele_type profile (income_clusters/industries still come from the
    clientele profile — those are a fixed, verified lever, not something the LLM
    reasons about). When absent, falls back to today's behavior unchanged.

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

    profile = clientele_profile(clientele_type) if clientele_type else {}
    income_clusters       = profile.get("income_clusters", [])
    industries            = profile.get("industries", [])
    relationship_statuses = profile.get("relationship_statuses", [])
    advantage_plus        = profile.get("advantage_plus", True)  # default on; profiles can opt out

    if llm_selection:
        interests  = llm_selection.get("interests", [])
        behaviours = llm_selection.get("behaviours", [])
        work_positions = llm_selection.get("work_positions") or profile.get("work_positions", [])
        # LLM relationship pick overrides the profile default when it made one.
        if llm_selection.get("relationship_statuses"):
            relationship_statuses = llm_selection["relationship_statuses"]
        age_min, age_max = llm_selection["age_min"], llm_selection["age_max"]
        _save_cache(cache)  # llm_selection was already resolved+cached by resolve_llm_targeting
    else:
        if clientele_type:
            interest_names  = profile["interests"]
            behaviour_names = profile["behaviours"]
            age_min, age_max = profile["age_min"], profile["age_max"]
            work_positions = profile.get("work_positions", [])
        else:
            interest_names  = DEFAULT_INTERESTS
            behaviour_names = DEFAULT_BEHAVIOURS
            age_min, age_max = DEFAULT_AGE_MIN, DEFAULT_AGE_MAX
            work_positions = []

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
        # Geo model: "radius" (city+radius circle, default) or "areas" (specific
        # neighbourhoods/pincodes only — set by the audience panel). Areas carry a
        # `city_key` so downstream geo analysis can map an area back to its city.
        "geo_mode": "radius",
        "neighborhoods": [],
        "zips": [],
        # Platform/OS restriction: "all" (default) | "ios" | "android". Advisory
        # default can be suggested by autooptimiser's device-performance rung,
        # but is only ever applied when the user approves/saves it.
        "platform_os": "all",
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
    neighborhoods = [{"key": str(n["key"])} for n in (audience.get("neighborhoods") or []) if n.get("key")]
    zips = [{"key": str(z["key"])} for z in (audience.get("zips") or []) if z.get("key")]
    # geo_mode "areas" means: target ONLY the chosen neighbourhoods/pincodes (tight —
    # avoids the wasteland a broad city radius sweeps in). Fall back to the city radius
    # circle if the user flipped to areas but hasn't picked any yet.
    use_areas = audience.get("geo_mode") == "areas" and (neighborhoods or zips)
    geo: dict[str, Any] = {}
    if use_areas:
        if neighborhoods:
            geo["neighborhoods"] = neighborhoods
        if zips:
            geo["zips"] = zips
    elif city_key:
        radius = int(audience.get("radius_km") or DEFAULT_RADIUS_KM)
        radius = max(_RADIUS_MIN_KM, min(_RADIUS_MAX_KM, radius))
        geo["cities"] = [{"key": str(city_key), "radius": radius, "distance_unit": "kilometer"}]
    else:
        geo["countries"] = [country]
    # NRI diaspora countries sit alongside the geo above in the same geo_locations block.
    # Meta unions them: reach people in the city radius / chosen areas OR any of these countries.
    if nri_countries:
        existing = geo.get("countries", [])
        geo["countries"] = list(dict.fromkeys(existing + nri_countries))
    spec["geo_locations"] = geo

    # Platform/OS restriction — Meta's targeting.user_os field (verified live:
    # accepts ["iOS"] / ["Android"], distinct from publisher_platforms which
    # controls FB/IG/Audience-Network placement, not device OS).
    platform_os = (audience.get("platform_os") or "all").strip().lower()
    if platform_os == "ios":
        spec["user_os"] = ["iOS"]
    elif platform_os == "android":
        spec["user_os"] = ["Android"]

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


# ── Per-campaign LLM targeting selection pool ────────────────────────────────
# Unlike CLIENTELE_TARGETING_MAP (12 fixed baskets keyed by clientele_type), this
# pool is organised by axis so the AudienceCrew's targeting_researcher agent can
# mix-and-match per campaign (city/price/property-type specific) instead of being
# locked into one of the 12 buckets. Every name below was verified live against
# Meta's Targeting Search API on 2026-07-05 (search_interests/search_behaviours) —
# do not add a name here without verifying it resolves; a name that returns zero
# hits is silently dropped by resolve_llm_targeting, which just wastes a pick.
INTEREST_POOL: dict[str, list[str]] = {
    "property_investment": [
        "Property investing (investing)",
        "Property investment trust (investing)",
        "Investor (investing)",
        "Property management (property)",
        "Gated community (property)",
        "Villa (lodging)",
        "penthouse apartment (property)",
        "Apartment (property)",
        "Bungalow",
    ],
    "luxury_lifestyle": [
        "Luxury goods (retail)",
        "Luxury vehicle (vehicles)",
        "Personal luxury car",
        "Luxury resorts (lodging)",
        "Luxury Lifestyle (website)",
        "luxury (lifestyle content)",
        "Rolex (watches)",
        "Yacht (watercraft)",
        "Wine (alcoholic drinks)",
        "Golf (sport)",
    ],
    "professional_finance": [
        "Investment (business and finance)",
        "Investment management (investing)",
        "Investment banking (banking)",
        "Wealth management (banking)",
        "Private banking",
        "Personal finance (banking)",
        "mutual fund (investing)",
        "Stock market (investing)",
        "Certified Financial Planner",
        "Entrepreneurship (business and finance)",
        "Small business (business and finance)",
    ],
    "trader_industrialist": [
        "Textile (craft supplies)",
        "Manufacturing (industry)",
        "Export",
        "International business",
        "Small and medium enterprises (business and finance)",
    ],
    "tech_professional": [
        "Interior design (design)",
        "Home improvement (home and garden)",
    ],
    "professional_services": [
        "Lawyer (law and legal services)",       # verified id 6003392101554
        "law firm (law and legal services)",      # verified id 6003057724244
    ],
    "travel_signal": [
        "First-class travel (travel and tourism business)",
        "Business class (air travel)",
    ],
}

# Flattened set for validation — resolve_llm_targeting drops any name not in here.
_ALL_POOL_INTERESTS: set[str] = {n for names in INTEREST_POOL.values() for n in names}

BEHAVIOUR_POOL: dict[str, list[str]] = {
    "travel": [
        "Frequent international travellers",       # British spelling — verified id 6022788483583
        "Frequent travellers",                      # verified id 6002714895372
        "Returned from travelling one week ago",    # verified id 6008261969983
        "Returned from travelling two weeks ago",   # verified id 6008297697383
    ],
    "affluence_proxy": [
        "Engaged shoppers",                                    # verified id 6071631541183
        "People in India who prefer high-value goods",         # verified id 6028974370383
        "People who prefer high-value goods in UAE",            # verified id 6082317210583
    ],
    "professional": [
        "Small business owners",                    # verified id 6002714898572
    ],
    "nri_diaspora": [
        "Lived in India (formerly Expats – India)",  # verified id 6016916298983 (em dash)
    ],
}
_ALL_POOL_BEHAVIOURS: set[str] = {n for names in BEHAVIOUR_POOL.values() for n in names}

# Work-position tiers for the LLM to pick from. Live-account performance data
# (2026-07-05 audit) shows 1-2 "owner_tier" positions consistently outperform
# long "csuite_tier" stacks (₹112-187 CPL vs ₹384-557 CPL on the same account) —
# resolve_llm_targeting enforces this as a hard cap, not just a prompt hint.
WORK_POSITION_POOL: dict[str, list[dict]] = {
    "owner_tier": _WORK_POSITIONS_OWNERS,
    "csuite_tier": _WORK_POSITIONS_CSUITE,
    "it_tier": [
        {"id": "1597325863845095", "name": "Information Technology Director"},
        {"id": "621959927947441",  "name": "Chief Information Officer (CIO)"},
        {"id": "106236979408167",  "name": "Chief information officer"},
    ],
    "specialist_tier": [
        {"id": "108900439134105", "name": "Property"},
        {"id": "105525432814378", "name": "Chartered accountant"},
        {"id": "105563979478424", "name": "Executive director"},
        {"id": "107402372623035", "name": "Doctor"},                 # verified job title
        {"id": "649354901854686", "name": "Medical Doctor (MD)"},    # verified job title
        {"id": "112696438745118", "name": "Lawyer"},                 # verified job title
        {"id": "112184688796827", "name": "Advocate"},               # verified job title (India)
        {"id": "100336133352803", "name": "Merchant Navy"},          # verified job title
        {"id": "530784513684674", "name": "Government Employee"},   # verified job title
        {"id": "108276862537207", "name": "Pharmaceutical sciences"}, # verified job title (pharma proxy)
    ],
}
_WORK_POSITION_BY_NAME: dict[str, dict] = {
    w["name"]: w for tier in WORK_POSITION_POOL.values() for w in tier
}
_CSUITE_TIER_NAMES: set[str] = {w["name"] for w in WORK_POSITION_POOL["csuite_tier"]}

MAX_LLM_INTERESTS = 10
MAX_LLM_BEHAVIOURS = 4
MAX_CSUITE_WORK_POSITIONS = 2
MAX_LLM_WORK_POSITIONS = 3

# Relationship-status axis (Meta's top-level `relationship_statuses` enum) — an
# LLM-selectable + UI-editable lever, not a hardcoded default. Luxury real-estate
# buyers skew married/family, so "Married" is the useful one here; 2=engaged kept
# available for wedding-adjacent inventory. Values are Meta's documented enum ints.
RELATIONSHIP_STATUS_POOL: dict[str, int] = {
    "Married": 3,
    "Engaged": 2,
}
_RELATIONSHIP_BY_NAME = {k.lower(): v for k, v in RELATIONSHIP_STATUS_POOL.items()}

# ── NRI / Gujarati-diaspora country suggestions ──────────────────────────────
# High Gujarati + broader Indian-diaspora density, curated for click-to-add in the
# audience panel (not auto-applied). ISO-2 codes match _NRI_COUNTRY_MAP / geo union.
NRI_DIASPORA_SUGGESTIONS: list[dict] = [
    {"code": "US", "label": "United States"},
    {"code": "GB", "label": "United Kingdom"},
    {"code": "AE", "label": "UAE (Dubai/Abu Dhabi)"},
    {"code": "CA", "label": "Canada"},
    {"code": "AU", "label": "Australia"},
    {"code": "KE", "label": "Kenya"},
    {"code": "ZA", "label": "South Africa"},
    {"code": "SG", "label": "Singapore"},
    {"code": "NZ", "label": "New Zealand"},
    {"code": "OM", "label": "Oman"},
    {"code": "QA", "label": "Qatar"},
    {"code": "BH", "label": "Bahrain"},
    {"code": "KW", "label": "Kuwait"},
    {"code": "TZ", "label": "Tanzania"},
    {"code": "UG", "label": "Uganda"},
]


def render_targeting_pool_for_prompt() -> str:
    """Render the pools as plain text for the AudienceCrew's select_targeting task."""
    lines: list[str] = []
    lines.append("INTERESTS (choose only from these names, verbatim):")
    for tag, names in INTEREST_POOL.items():
        lines.append(f"  [{tag}] " + ", ".join(names))
    lines.append("")
    lines.append("BEHAVIOURS (choose only from these names, verbatim):")
    for tag, names in BEHAVIOUR_POOL.items():
        lines.append(f"  [{tag}] " + ", ".join(names))
    lines.append("")
    lines.append("WORK POSITIONS (choose only from these names, verbatim):")
    for tag, positions in WORK_POSITION_POOL.items():
        lines.append(f"  [{tag}] " + ", ".join(p["name"] for p in positions))
    lines.append("")
    lines.append("NOTE — 100% cheque-only buyers (declared/legitimate income, no cash "
                 "component) skew toward: Chief executive officer/Chief financial officer "
                 "(csuite_tier), Merchant Navy/Government Employee/Pharmaceutical sciences "
                 "(specialist_tier), IT Director/CIO (it_tier), and Export/International "
                 "business (trader_industrialist interests) — favour these when the brief "
                 "says cheque-only, still within the owner/csuite caps above.")
    lines.append("")
    lines.append("RELATIONSHIP STATUS (optional; choose from these names, verbatim — "
                 "luxury/family buyers usually skew Married):")
    lines.append("  " + ", ".join(RELATIONSHIP_STATUS_POOL.keys()))
    return "\n".join(lines)


def resolve_llm_targeting(selection: dict | None, token: str) -> dict | None:
    """
    Validate + resolve the AudienceCrew's structured targeting_selection.json
    against the verified pools above, then resolve names to Meta {id,name} dicts.

    Returns None if `selection` is missing/empty/unusable — callers should fall
    back to the static CLIENTELE_TARGETING_MAP profile in that case. Never raises;
    any single bad field is dropped rather than failing the whole selection.
    """
    if not selection:
        return None

    picked_interests = [n for n in (selection.get("interests") or []) if n in _ALL_POOL_INTERESTS]
    picked_behaviours = [n for n in (selection.get("behaviours") or []) if n in _ALL_POOL_BEHAVIOURS]
    picked_positions_raw = [n for n in (selection.get("work_positions") or []) if n in _WORK_POSITION_BY_NAME]

    if not picked_interests and not picked_behaviours and not picked_positions_raw:
        return None  # nothing usable came back — treat as no selection

    # dedupe, preserving order
    picked_interests = list(dict.fromkeys(picked_interests))[:MAX_LLM_INTERESTS]
    picked_behaviours = list(dict.fromkeys(picked_behaviours))[:MAX_LLM_BEHAVIOURS]

    # cap csuite_tier picks at MAX_CSUITE_WORK_POSITIONS, non-csuite picks pass through
    work_positions: list[dict] = []
    csuite_count = 0
    for name in dict.fromkeys(picked_positions_raw):
        if len(work_positions) >= MAX_LLM_WORK_POSITIONS:
            break
        if name in _CSUITE_TIER_NAMES:
            if csuite_count >= MAX_CSUITE_WORK_POSITIONS:
                continue
            csuite_count += 1
        work_positions.append(_WORK_POSITION_BY_NAME[name])

    try:
        age_min = int(selection.get("age_min"))
        age_max = int(selection.get("age_max"))
        if not (18 <= age_min < age_max <= 75):
            age_min, age_max = DEFAULT_AGE_MIN, DEFAULT_AGE_MAX
    except (TypeError, ValueError):
        age_min, age_max = DEFAULT_AGE_MIN, DEFAULT_AGE_MAX

    cache = _load_cache()
    interests: list[dict] = []
    for nm in picked_interests:
        hit = _best_interest(nm, token, cache)
        if hit and hit not in interests:
            interests.append(hit)
    behaviours: list[dict] = []
    for nm in picked_behaviours:
        try:
            hit = _best_behaviour(nm, token, cache)
        except RuntimeError:
            hit = None
        if hit and hit not in behaviours:
            behaviours.append(hit)
    _save_cache(cache)

    # Relationship statuses — optional. Accept either the enum ints directly or the
    # pool names ("Married"/"Engaged"); anything unrecognised is dropped.
    rel_statuses: list[int] = []
    for r in (selection.get("relationship_statuses") or []):
        if isinstance(r, int) and r in RELATIONSHIP_STATUS_POOL.values():
            rel_statuses.append(r)
        elif isinstance(r, str) and r.lower() in _RELATIONSHIP_BY_NAME:
            rel_statuses.append(_RELATIONSHIP_BY_NAME[r.lower()])
    rel_statuses = list(dict.fromkeys(rel_statuses))

    return {
        "interests": interests,
        "behaviours": behaviours,
        "work_positions": work_positions,
        "relationship_statuses": rel_statuses,
        "age_min": age_min,
        "age_max": age_max,
        "reasoning": str(selection.get("reasoning", ""))[:600],
    }


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
    nbh = audience.get("neighborhoods") or []
    zps = audience.get("zips") or []
    if audience.get("geo_mode") == "areas" and (nbh or zps):
        area_bits = []
        if nbh:
            area_bits.append(f"{len(nbh)} area{'s' if len(nbh) != 1 else ''}")
        if zps:
            area_bits.append(f"{len(zps)} pincode{'s' if len(zps) != 1 else ''}")
        city_lbl = audience.get("city") or ""
        geo = (f"{city_lbl}: " if city_lbl else "") + " + ".join(area_bits)
    elif audience.get("city") and audience.get("city_key"):
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
    if 3 in (audience.get("relationship_statuses") or []):
        bits.append("Married")
    if n_inc:
        bits.append(f"{n_inc} custom audience{'s' if n_inc != 1 else ''}")
    if n_exc:
        bits.append(f"{n_exc} excluded")
    platform_os = (audience.get("platform_os") or "all").strip().lower()
    if platform_os == "ios":
        bits.append("iOS only")
    elif platform_os == "android":
        bits.append("Android only")
    return " · ".join(bits)
