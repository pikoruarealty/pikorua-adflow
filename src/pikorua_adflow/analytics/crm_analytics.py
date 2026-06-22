"""
CRM Analytics Engine — deep lead intelligence for Pikorua (luxury real estate).

Standalone, pure-Python module. No FastAPI, no CrewAI, no hard dependency
beyond the standard library (`requests` is only used, optionally, for pincode
lookups and is failure-tolerant). Designed to be lifted out of this project and
dropped into any Python codebase.

TO USE STANDALONE
-----------------
    from crm_analytics import full_report
    report = full_report(list_of_lead_dicts)

Each lead dict may use any reasonable spelling of these fields (matching is
case- and punctuation-insensitive, so "Buying Status", "buying_status" and
"BuyingStatus" are all accepted):

    Name, Phone, Email, City, Campaign, Source, Status, Assigned To,
    Call Status, HWC, Buying Status, Budget, Profession, Company,
    Current City, Current Area, Configuration, Follow-up, Received

Every function takes the raw list of lead dicts and returns a JSON-serialisable
dict (or list). Nothing here raises on bad/missing data — a thin report is
always better than a crash.

IN THIS PROJECT
---------------
`get_leads()` pulls live rows from `crm_source.fetch_rows()` (Supabase-first,
CSV fallback) so the FastAPI layer can stay a one-liner.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Field resolution — map any spelling to a canonical key
# --------------------------------------------------------------------------- #
_CANON: dict[str, list[str]] = {
    "name": ["name", "fullname", "leadname"],
    "phone": ["phone", "mobile", "contact", "phonenumber"],
    "email": ["email", "emailaddress"],
    "city": ["city", "location"],
    "campaign": ["campaign", "campaignname", "adcampaign"],
    "source": ["source", "leadsource", "adsource"],
    "status": ["status", "leadstatus"],
    "assignedto": ["assignedto", "owner", "salesrep"],
    "callstatus": ["callstatus"],
    "hwc": ["hwc"],
    "buyingstatus": ["buyingstatus", "buying"],
    "budget": ["budget", "budgetrange", "budgetbracket", "pricerange"],
    "profession": ["profession", "occupation", "job", "designation"],
    "company": ["company", "companyname", "organisation", "employer"],
    "currentcity": ["currentcity"],
    "currentarea": ["currentarea", "area", "locality"],
    "configuration": ["configuration", "config"],
    "received": ["received", "receivedat", "date", "created", "leaddate"],
    "followup": ["followup"],
    "remarks": ["remarks", "notes"],
}


def _key(s: str) -> str:
    """Lowercase + strip all non-alphanumerics, for tolerant key matching."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _normalize(leads: list[dict]) -> list[dict]:
    """Return rows re-keyed to the canonical names in _CANON (values stringified)."""
    out: list[dict] = []
    for row in leads or []:
        norm = {_key(k): ("" if v is None else str(v).strip()) for k, v in row.items()}
        canon = {}
        for canonical, variants in _CANON.items():
            val = ""
            for vname in variants:
                v = norm.get(_key(vname))
                if v:
                    val = v
                    break
            canon[canonical] = val
        out.append(canon)
    return out


# --------------------------------------------------------------------------- #
# Lead quality
# --------------------------------------------------------------------------- #
_QUALITY_STATUSES = {"exploring", "hot", "warm"}


def _is_quality(norm_row: dict) -> bool:
    """A lead is 'quality' when its Buying Status is exploring / warm / hot."""
    return norm_row.get("buyingstatus", "").lower() in _QUALITY_STATUSES


def _quality_rate(norm_rows: list[dict]) -> float:
    """Percent (0-100, 1 dp) of rows that are quality leads."""
    if not norm_rows:
        return 0.0
    q = sum(1 for r in norm_rows if _is_quality(r))
    return round(q / len(norm_rows) * 100, 1)


# --------------------------------------------------------------------------- #
# Budget parsing
# --------------------------------------------------------------------------- #
_BUDGET_BUCKETS = ["<5Cr", "5–7Cr", "7–10Cr", "10Cr+", "Unknown"]
_NUM_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(crores?|cr|lakhs?|lacs?|l)?")


def parse_budget_cr(raw: str) -> float | None:
    """
    Parse a budget string into a representative value in Crores.

    Handles ranges and many spellings:
      "1-2 Cr", "2-5 Cr", "50L-1Cr", "5Cr+", "2 Cr – 3 Cr",
      "12 Cr & above", "INR 4 cr to 6 cr", bare numbers ("5" -> 5 Cr).
    Returns the midpoint of a range, or None if no number is found.
    """
    if not raw:
        return None
    s = raw.strip().lower()
    s = re.sub(r"[‒–—―]", "-", s)      # any dash -> hyphen
    s = s.replace("inr", " ").replace("rs", " ")
    s = s.replace(" to ", "-").replace("&", "-")

    matches = [(float(n), u) for n, u in _NUM_UNIT_RE.findall(s) if n]
    if not matches:
        return None

    def to_cr(n: float, unit: str) -> float:
        if unit in ("l", "lac", "lacs", "lakh", "lakhs"):
            return n / 100.0
        return n  # cr / crore / unitless -> treat as Cr

    values: list[float] = []
    for i, (n, u) in enumerate(matches):
        if not u:  # bare number inherits the next explicit unit ("1-2 Cr")
            u = next((matches[j][1] for j in range(i + 1, len(matches)) if matches[j][1]), "")
        values.append(to_cr(n, u))

    return (min(values) + max(values)) / 2.0


def budget_bucket(raw: str) -> str:
    """Map a budget string to one of _BUDGET_BUCKETS."""
    cr = parse_budget_cr(raw)
    if cr is None:
        return "Unknown"
    if cr < 5:
        return "<5Cr"
    if cr < 7:
        return "5–7Cr"
    if cr < 10:
        return "7–10Cr"
    return "10Cr+"


# --------------------------------------------------------------------------- #
# Profession -> industry
# --------------------------------------------------------------------------- #
# First keyword match wins, so order from most-specific to most-general.
_INDUSTRY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("NRI", ["nri", "non resident", "non-resident"]),
    ("Retired", ["retired", "pensioner", "superannuat"]),
    ("Government/PSU", ["government", "govt", "ias", "ips", "irs", "psu",
                         "public sector", "railway", "defence", "defense",
                         "army", "navy", "air force", "bureaucrat", "collector"]),
    ("Medical/Healthcare", ["doctor", "dr.", "physician", "surgeon", "dentist",
                             "medical", "hospital", "pharma", "nurse",
                             "healthcare", "mbbs", "clinic", "radiolog",
                             "cardiolog", "ortho"]),
    ("Finance/Banking", ["bank", "finance", "financial", "chartered accountant",
                          " ca ", "accountant", "auditor", "investment",
                          "trader", "stock", "broker", "wealth", "insurance",
                          "fintech", "cfo", "actuary"]),
    ("Real Estate", ["real estate", "realtor", "realty", "builder",
                      "construction", "property"]),
    ("IT/Tech", ["software", "developer", "it ", " it", "engineer", "tech",
                 "programmer", "sde", "data scientist", "analyst", "devops",
                 "qa ", "sap", "oracle", "infosys", "tcs", "wipro",
                 "cognizant", "google", "microsoft", "amazon", "consultant",
                 "product manager"]),
    ("Business/Entrepreneur", ["business", "entrepreneur", "founder", "ceo",
                                "coo", "cto", "director", "proprietor", "owner",
                                "managing director", " md", "self employed",
                                "self-employed", "merchant", "industrialist",
                                "partner"]),
]


def profession_to_industry(profession: str, company: str = "") -> str:
    """Classify a profession (with optional company fallback) into an industry."""
    text = f" {profession.lower()} "
    for industry, keywords in _INDUSTRY_KEYWORDS:
        if any(k in text for k in keywords):
            return industry
    # Secondary signal: try the company name when profession was unhelpful
    if company:
        ctext = f" {company.lower()} "
        for industry, keywords in _INDUSTRY_KEYWORDS:
            if any(k in ctext for k in keywords):
                return industry
    return "Other"


# --------------------------------------------------------------------------- #
# Date parsing
# --------------------------------------------------------------------------- #
_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                 "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%d.%m.%Y"]


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    s = raw.strip()
    # ISO timestamps from Supabase (e.g. 2026-05-01T12:30:00+00:00 or ...Z)
    iso = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        pass
    head = s.split("T")[0].split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Pincode lookup (OpenStreetMap Nominatim) — cached, never blocking
# --------------------------------------------------------------------------- #
_CACHE_PATH = pathlib.Path(__file__).resolve().parents[3] / "outputs" / "pincode_cache.json"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _load_pincode_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pincode_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


class _PincodeResolver:
    """Resolves (area, city) -> pincode using a disk cache + Nominatim.

    Self-disables after the first network error so a report is never blocked
    by an offline machine or a slow API.
    """

    def __init__(self, max_lookups: int = 30, time_budget_secs: float = 20.0):
        self.cache = _load_pincode_cache()
        self.net_ok = True
        self.dirty = False
        self.budget = max_lookups
        # Wall-clock deadline so a cold cache never blocks a report for long.
        self.deadline = __import__("time").monotonic() + time_budget_secs

    def resolve(self, area: str, city: str) -> str:
        area, city = area.strip(), city.strip()
        if not (area or city):
            return ""
        ck = f"{area.lower()}|{city.lower()}"
        if ck in self.cache:
            return self.cache[ck]
        if not self.net_ok or self.budget <= 0:
            return ""
        if __import__("time").monotonic() > self.deadline:
            return ""
        pin = self._lookup(area, city)
        self.budget -= 1
        self.cache[ck] = pin
        self.dirty = True
        return pin

    def _lookup(self, area: str, city: str) -> str:
        try:
            import requests
        except Exception:
            self.net_ok = False
            return ""
        q = ", ".join(p for p in (area, city, "India") if p)
        try:
            resp = requests.get(
                _NOMINATIM_URL,
                params={"q": q, "format": "json", "addressdetails": 1, "limit": 1},
                headers={"User-Agent": "pikorua-adflow-crm-analytics/1.0"},
                timeout=6,
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                addr = data[0].get("address", {})
                return addr.get("postcode", "") or ""
        except Exception:
            self.net_ok = False
        return ""

    def flush(self) -> None:
        if self.dirty:
            _save_pincode_cache(self.cache)


# --------------------------------------------------------------------------- #
# 1. Lead volume trend
# --------------------------------------------------------------------------- #
def lead_volume_trend(leads: list[dict]) -> dict:
    """Monthly lead counts parsed from the Received date, with growth signals."""
    rows = _normalize(leads)
    monthly: Counter = Counter()
    dated = 0
    for r in rows:
        dt = _parse_date(r.get("received", ""))
        if dt:
            dated += 1
            monthly[f"{dt.year:04d}-{dt.month:02d}"] += 1

    ordered = dict(sorted(monthly.items()))
    peak_month = max(monthly, key=monthly.get) if monthly else None

    growth_rate = None
    if len(ordered) >= 2:
        vals = list(ordered.values())
        prev, last = vals[-2], vals[-1]
        if prev:
            growth_rate = round((last - prev) / prev * 100, 1)

    return {
        "monthly": ordered,
        "peak_month": peak_month,
        "peak_count": monthly[peak_month] if peak_month else 0,
        "growth_rate": growth_rate,
        "total_dated": dated,
        "undated": len(rows) - dated,
    }


# --------------------------------------------------------------------------- #
# 2. Geographic distribution
# --------------------------------------------------------------------------- #
def geographic_distribution(leads: list[dict], resolve_pincodes: bool = True) -> dict:
    """City / area breakdown with optional pincode enrichment via Nominatim."""
    rows = _normalize(leads)
    city_counts: Counter = Counter()
    area_counts: Counter = Counter()
    # (area, city) pair counts so we can enrich the busiest areas with a pincode
    pair_counts: Counter = Counter()

    for r in rows:
        city = (r.get("city") or r.get("currentcity") or "").strip()
        area = (r.get("currentarea") or "").strip()
        if city:
            city_counts[city.title()] += 1
        if area:
            area_counts[area.title()] += 1
        if city or area:
            pair_counts[(area.title(), city.title())] += 1

    geo_list: list[dict] = []
    pincode_counts: Counter = Counter()
    resolver = _PincodeResolver() if resolve_pincodes else None
    # Enrich most-common areas first so the lookup budget is spent where it matters.
    for (area, city), count in pair_counts.most_common():
        pincode = ""
        if resolver and area:
            pincode = resolver.resolve(area, city)
            if pincode:
                pincode_counts[pincode] += count
        geo_list.append({"area": area, "city": city, "pincode": pincode, "count": count})
    if resolver:
        resolver.flush()

    return {
        "city_counts": dict(city_counts.most_common()),
        "area_counts": dict(area_counts.most_common()),
        "pincode_counts": dict(pincode_counts.most_common()),
        "top_cities": city_counts.most_common(8),
        "geo_list": geo_list[:60],
    }


# --------------------------------------------------------------------------- #
# 3. Budget segments
# --------------------------------------------------------------------------- #
def budget_segments(leads: list[dict]) -> dict:
    """Bucket leads by budget and report count, share and quality within each."""
    rows = _normalize(leads)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        buckets[budget_bucket(r.get("budget", ""))].append(r)

    total = len(rows) or 1
    out: dict[str, dict] = {}
    for bucket in _BUDGET_BUCKETS:
        members = buckets.get(bucket, [])
        out[bucket] = {
            "count": len(members),
            "pct": round(len(members) / total * 100, 1),
            "avg_quality_score": _quality_rate(members),
        }
    return out


# --------------------------------------------------------------------------- #
# 4. Profession / industry breakdown
# --------------------------------------------------------------------------- #
def profession_industry_breakdown(leads: list[dict]) -> dict:
    """Group leads into industries (with company fallback) sorted by volume."""
    rows = _normalize(leads)
    industries: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        prof = r.get("profession", "")
        comp = r.get("company", "")
        if not prof and not comp:
            industries["Unknown"].append(r)
            continue
        industries[profession_to_industry(prof, comp)].append(r)

    total = len(rows) or 1
    rows_out = []
    for industry, members in industries.items():
        rows_out.append({
            "industry": industry,
            "count": len(members),
            "pct": round(len(members) / total * 100, 1),
            "quality_rate": _quality_rate(members),
        })
    rows_out.sort(key=lambda x: x["count"], reverse=True)
    return {"industries": rows_out, "total": len(rows)}


# --------------------------------------------------------------------------- #
# 5. Lead quality funnel
# --------------------------------------------------------------------------- #
_CALL_NEGATIVE = {"notconnected", "notreachable", "switchedoff", "busy",
                  "notpicked", "ringing", "invalid", "wrongnumber", "notanswered",
                  "noresponse", "dnd", "notspoken", "nothspoken", "notcalled",
                  "pending", "new", "notyetcalled"}
_TRUE_VALUES = {"yes", "y", "true", "1", "done", "hwc", "had word"}


def _is_spoken(r: dict) -> bool:
    cs = r.get("callstatus", "")
    if not cs:
        return False
    return _key(cs) not in _CALL_NEGATIVE


def _is_hwc(r: dict) -> bool:
    v = r.get("hwc", "").lower()
    return bool(v) and (_key(v) in {_key(t) for t in _TRUE_VALUES} or "word" in v)


def lead_quality_funnel(leads: list[dict]) -> dict:
    """Conversion funnel: Received -> Spoken -> HWC -> Exploring -> Hot."""
    rows = _normalize(leads)
    received = len(rows)
    spoken = sum(1 for r in rows if _is_spoken(r))
    hwc = sum(1 for r in rows if _is_hwc(r))
    exploring = sum(1 for r in rows if r.get("buyingstatus", "").lower() in ("exploring", "warm"))
    hot = sum(1 for r in rows if r.get("buyingstatus", "").lower() == "hot")

    stage_order = [("Received", received), ("Spoken", spoken), ("HWC", hwc),
                   ("Exploring", exploring), ("Hot", hot)]
    stages = []
    prev = received
    for name, count in stage_order:
        drop = round((prev - count) / prev * 100, 1) if prev else 0.0
        stages.append({
            "stage": name,
            "count": count,
            "pct_of_total": round(count / received * 100, 1) if received else 0.0,
            "drop_off_pct": max(drop, 0.0),
        })
        prev = count
    return {"stages": stages, "total": received}


# --------------------------------------------------------------------------- #
# 6. Campaign / source attribution
# --------------------------------------------------------------------------- #
def campaign_source_attribution(leads: list[dict]) -> dict:
    """Per-campaign volume, quality rate, dominant budget bucket and professions."""
    rows = _normalize(leads)
    by_campaign: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_campaign[r.get("campaign", "") or "Unknown"].append(r)

    out: dict[str, dict] = {}
    for campaign, members in by_campaign.items():
        budget_counter: Counter = Counter(budget_bucket(m.get("budget", "")) for m in members)
        prof_counter: Counter = Counter(
            profession_to_industry(m.get("profession", ""), m.get("company", ""))
            for m in members if m.get("profession") or m.get("company")
        )
        src_counter: Counter = Counter(m.get("source", "") or "Unknown" for m in members)
        out[campaign] = {
            "count": len(members),
            "quality_rate": _quality_rate(members),
            "avg_budget_bucket": budget_counter.most_common(1)[0][0] if budget_counter else "Unknown",
            "top_professions": [p for p, _ in prof_counter.most_common(3)],
            "top_sources": [s for s, _ in src_counter.most_common(3)],
        }
    # Sort by lead count, busiest first
    ordered = dict(sorted(out.items(), key=lambda kv: kv[1]["count"], reverse=True))
    return ordered


# --------------------------------------------------------------------------- #
# 7. Per-project analytics
# --------------------------------------------------------------------------- #
def project_analytics(leads: list[dict], project_name: str) -> dict:
    """Full breakdown filtered to leads whose Campaign fuzzily matches a project."""
    needle = _key(project_name)
    matched = []
    for row in leads or []:
        norm = _normalize([row])[0]
        hay = _key(norm.get("campaign", ""))
        if needle and (needle in hay or hay in needle):
            matched.append(row)

    return {
        "project": project_name,
        "matched_leads": len(matched),
        "geography": geographic_distribution(matched, resolve_pincodes=False),
        "budget_segments": budget_segments(matched),
        "professions": profession_industry_breakdown(matched),
        "funnel": lead_quality_funnel(matched),
    }


# --------------------------------------------------------------------------- #
# 8. Top converting profiles
# --------------------------------------------------------------------------- #
def top_converting_profiles(leads: list[dict], top_n: int = 5, min_count: int = 3) -> list[dict]:
    """
    Highest-quality (industry + budget bucket + city) combinations.

    Drives Meta interest/geo targeting recommendations. Only profiles with at
    least `min_count` leads qualify so a single hot lead can't top the list.
    """
    rows = _normalize(leads)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        industry = profession_to_industry(r.get("profession", ""), r.get("company", ""))
        bucket = budget_bucket(r.get("budget", ""))
        city = (r.get("city") or r.get("currentcity") or "Unknown").title()
        groups[(industry, bucket, city)].append(r)

    profiles = []
    for (industry, bucket, city), members in groups.items():
        if len(members) < min_count:
            continue
        profiles.append({
            "profile": {"industry": industry, "budget": bucket, "city": city},
            "quality_rate": _quality_rate(members),
            "count": len(members),
            "sample_leads": [m.get("name", "") for m in members[:3] if m.get("name")],
        })
    profiles.sort(key=lambda x: (x["quality_rate"], x["count"]), reverse=True)
    return profiles[:top_n]


# --------------------------------------------------------------------------- #
# 8b. Campaign lead matching + profile-match quality (autopilot north-star)
# --------------------------------------------------------------------------- #
def match_meta_leads(leads: list[dict], campaign_name: str) -> list[dict]:
    """
    Return the CRM rows attributed to a given Meta campaign.

    The lead webhook stamps every inbound lead with its `campaign_name`, so we
    match on that (fuzzy, punctuation-insensitive) rather than re-hashing contacts.
    Falls back to an empty list when nothing matches — the caller then knows CRM
    coverage for this campaign is still sparse.
    """
    needle = _key(campaign_name)
    if not needle:
        return []
    out = []
    for row in leads or []:
        norm = _normalize([row])[0]
        hay = _key(norm.get("campaign", ""))
        if hay and (needle in hay or hay in needle):
            out.append(row)
    return out


def profile_match_score(leads: list[dict], best_profiles: list[dict]) -> dict:
    """
    Score how strongly a campaign's leads resemble the best-converting CRM profiles.

    `best_profiles` is top_converting_profiles() output. For each lead we form its
    (industry, budget bucket, city) signature and check whether it falls inside one
    of the high-quality profiles. Returns:
      {score: 0-100, matched: int, total: int, n_quality: int}
    score = share of leads matching a top profile, nudged by their own quality rate.
    This is the FALLBACK north-star used while CRM buying_status is still sparse.
    """
    rows = _normalize(leads)
    total = len(rows)
    if not total:
        return {"score": 0.0, "matched": 0, "total": 0, "n_quality": 0}

    profile_keys = set()
    for p in best_profiles or []:
        pr = p.get("profile", {})
        profile_keys.add((pr.get("industry", ""), pr.get("budget", ""), pr.get("city", "")))

    matched = 0
    n_quality = 0
    for r in rows:
        if _is_quality(r):
            n_quality += 1
        sig = (
            profession_to_industry(r.get("profession", ""), r.get("company", "")),
            budget_bucket(r.get("budget", "")),
            (r.get("city") or r.get("currentcity") or "Unknown").title(),
        )
        if sig in profile_keys:
            matched += 1
    base = matched / total * 100
    quality_nudge = (n_quality / total) * 20  # up to +20 for genuinely warm leads
    return {
        "score": round(min(100.0, base + quality_nudge), 1),
        "matched": matched, "total": total, "n_quality": n_quality,
    }


# --------------------------------------------------------------------------- #
# 9. Full report
# --------------------------------------------------------------------------- #
def full_report(leads: list[dict]) -> dict:
    """Bundle every analysis dimension into one JSON-serialisable report."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_leads": len(leads or []),
        "volume_trend": lead_volume_trend(leads),
        "geography": geographic_distribution(leads),
        "budget_segments": budget_segments(leads),
        "professions": profession_industry_breakdown(leads),
        "lead_quality": lead_quality_funnel(leads),
        "attribution": campaign_source_attribution(leads),
        "top_profiles": top_converting_profiles(leads),
    }


# --------------------------------------------------------------------------- #
# Convenience: pull live rows from this project's CRM source
# --------------------------------------------------------------------------- #
def get_leads() -> tuple[list[dict], str]:
    """Return (leads, source_label) from crm_source (Supabase-first, CSV fallback)."""
    try:
        from pikorua_adflow.utils import crm_source
        return crm_source.fetch_rows()
    except Exception as exc:  # pragma: no cover - defensive
        return [], f"CRM source unavailable ({exc})"
