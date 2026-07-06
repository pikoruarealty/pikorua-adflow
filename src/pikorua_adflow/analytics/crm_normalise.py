"""
CRM field normalisation — city and profession.

Fixes data-quality issues caused by inconsistent free-text entry:
- City: "Ahmedabad" appears as 5+ variants → splits the geo engine's signal
- Profession: "Business Owner" appears as 8+ variants → makes profile-match
  scoring see 8 small groups instead of one dominant buyer profile

Called from crm_source.fetch_rows() so every consumer (crm_analyser,
meta_audience_tool, geo_intelligence, adaptive_quality) gets clean data.
All matching is case-insensitive.  Unknown values are passed through unchanged.
"""
from __future__ import annotations

import json
import re
from difflib import get_close_matches

# ── City canonicalisation ─────────────────────────────────────────────────────
# Key   = canonical display name
# Value = known raw variants (lowercased, stripped)
_CITY_ALIASES: dict[str, list[str]] = {
    "Ahmedabad": [
        "ahmedabad", "ahemdabad", "ahmadabad", "amdavad", "ahemadabad",
        "ahmadabad", "ahemdabad", "ahmedabab", "ahmedabadcity", "ahemadabadad",
    ],
    "Mumbai": ["mumbai", "bombay", "mumb"],
    "Surat": ["surat"],
    "Rajkot": ["rajkot"],
    "Vadodara": ["vadodara", "baroda"],
    "Gandhinagar": ["gandhinagar", "gandhi nagar"],
    "Pune": ["pune", "poona"],
    "Delhi": ["delhi", "new delhi", "newdelhi"],
    "Bengaluru": ["bengaluru", "bangalore"],
    "Hyderabad": ["hyderabad"],
    "Chennai": ["chennai", "madras"],
    "Jaipur": ["jaipur"],
    "Kolkata": ["kolkata", "calcutta"],
    "Anand": ["anand"],
    "Mehsana": ["mehsana", "mahesana"],
    "Nadiad": ["nadiad"],
}

# Build a lookup: normalised_variant → canonical name
_CITY_LOOKUP: dict[str, str] = {}
for _canonical, _variants in _CITY_ALIASES.items():
    for _v in _variants:
        _CITY_LOOKUP[_v] = _canonical
    _CITY_LOOKUP[_canonical.lower()] = _canonical  # canonical itself


def _normalise_city(raw: str) -> str:
    if not raw:
        return raw
    key = raw.strip().lower()
    if key in _CITY_LOOKUP:
        return _CITY_LOOKUP[key]
    # Fuzzy fallback: if the lowercased input is close to a known variant,
    # map it.  Cutoff 0.82 avoids false positives between short city names.
    matches = get_close_matches(key, _CITY_LOOKUP.keys(), n=1, cutoff=0.82)
    if matches:
        return _CITY_LOOKUP[matches[0]]
    # Capitalise words as a cheap display fix (e.g. "AHMEDABAD" → "Ahmedabad")
    return raw.strip().title()


# ── Profession canonicalisation ───────────────────────────────────────────────
# Maps a set of raw profession strings → a single canonical label.
# The dominant buyer profile in the Pikorua CRM is business owners; keeping
# them fragmented across 8 labels makes profile-match scoring inaccurate.
_PROFESSION_GROUPS: list[tuple[str, list[str]]] = [
    ("Business Owner", [
        "business", "owner", "business owner", "self employed", "self-employed",
        "selfemployed", "businessman", "businesswoman", "businessperson",
        "entrepreneur", "proprietor", "md", "managing director", "managingdirector",
        "director", "co-director", "partner", "co-founder", "cofounder",
        "founder", "ceo", "coo", "cmo", "chairman",
    ]),
    ("Doctor", [
        "doctor", "physician", "surgeon", "dentist", "dr", "dr.",
        "medical", "mbbs", "md (doctor)", "consultant physician",
    ]),
    ("Engineer", [
        "engineer", "software engineer", "civil engineer", "it engineer",
        "mechanical engineer", "developer", "software developer",
    ]),
    ("Lawyer", [
        "lawyer", "advocate", "attorney", "legal", "solicitor",
    ]),
    ("Government / PSU", [
        "government", "govt", "ias", "ips", "civil servant", "psu",
        "public sector", "officer",
    ]),
    ("NRI", [
        "nri", "non resident", "non-resident indian", "overseas",
    ]),
    ("Retired", [
        "retired", "ex-serviceman", "ex serviceman",
    ]),
    ("Investor", [
        "investor", "investment", "portfolio", "fund manager",
    ]),
]

# Build lookup: normalised_raw → canonical label
_PROFESSION_LOOKUP: dict[str, str] = {}
for _label, _terms in _PROFESSION_GROUPS:
    for _t in _terms:
        _PROFESSION_LOOKUP[re.sub(r"[^a-z0-9]", "", _t.lower())] = _label
    _PROFESSION_LOOKUP[re.sub(r"[^a-z0-9]", "", _label.lower())] = _label


def _normalise_profession(raw: str) -> str:
    if not raw:
        return raw
    key = re.sub(r"[^a-z0-9]", "", raw.strip().lower())
    if key in _PROFESSION_LOOKUP:
        return _PROFESSION_LOOKUP[key]
    # Substring scan: if any known term appears inside the raw value, map it.
    for _term, _label in ((t, lbl) for lbl, terms in _PROFESSION_GROUPS for t in terms):
        norm_term = re.sub(r"[^a-z0-9]", "", _term.lower())
        if norm_term and norm_term in key:
            return _label
    return raw.strip()  # unknown → pass through unchanged


# ── Public API ────────────────────────────────────────────────────────────────

def _scalarize(value):
    """
    Coerce a field value to something every consumer (Excel, CSV, scoring) can
    handle. Supabase/PostgREST returns array/jsonb columns (e.g. Configuration)
    as native Python lists, which openpyxl and other scalar-only writers reject.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def normalise_rows(rows: list[dict]) -> list[dict]:
    """
    Return a new list of rows with City and Profession fields canonicalised,
    and every field coerced to a scalar value. Does NOT mutate the input rows.
    City is checked in both "City" and "CurrentCity" keys (crm_source maps
    both). Unknown values are preserved.
    """
    out: list[dict] = []
    for row in rows:
        r = {k: _scalarize(v) for k, v in row.items()}
        for city_key in ("City", "CurrentCity"):
            if r.get(city_key):
                r[city_key] = _normalise_city(r[city_key])
        if r.get("Profession"):
            r["Profession"] = _normalise_profession(r["Profession"])
        out.append(r)
    return out
