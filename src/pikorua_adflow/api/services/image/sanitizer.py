"""
Sanitizer — one data-driven ban list, no inline rule prose (§11).

RENDER mode: only the scene_prose (the Ideogram scene prompt) is validated, because
text strings come from the brief and are never LLM-generated. BAKED mode: the full
assembled prompt is validated before it is sent to Ideogram.
"""

from __future__ import annotations

import re
from typing import Optional

# ── The single ban list ───────────────────────────────────────────────────────
# Each entry: a regex (whole-match removed). Conditional bans (RERA, possession,
# awards) are only stripped when the brief does NOT verify them.

# Always-banned fabrications regardless of brief.
_ABSOLUTE_PATTERNS = [
    r"\b\d{3,5}\s*(?:[-–]\s*\d{3,5}\s*)?sq\.?\s*ft\.?\b",          # sq ft in isolation
    r"\b\+?\d[\d\s\-]{7,}\d\b",                                     # phone numbers
    r"\bhttps?://\S+\b", r"\bwww\.\S+\b", r"\b\S+\.(?:com|in|co)\b",  # URLs
    r"\bRERA[\s:#A-Z0-9]*\b",                                       # RERA numbers/refs
    r"\b\d{1,3}\s*(?:floors?|storey?s?|stories|towers?)\b",         # floor/storey counts
    r"\bpossession\s+(?:by|in|from)?\s*\w*\s*\d{4}\b",              # possession dates
    r"\b\d{1,2}\s*km\s+from\b[^.]*",                                # invented distances
    r"\b(?:best|finest|number\s*one|no\.?\s*1|india'?s\s+finest|world'?s\s+best)\b",
    r"\bguaranteed\s+(?:returns?|appreciation)\b",
]

# Technical noise that must never reach Ideogram (logo/brand/font/pixel instructions).
_TECH_NOISE_RE = re.compile(
    r"""(?ix)
    \b\d{3,4}\s*[x×]\s*\d{3,4}\s*px?\b
    | \b\d+\s*pt\b
    | [^.]*\b(logo|wordmark|brand\s*mark|emblem|monogram|watermark|PIKORUA)\b[^.]*\.?
    """,
    re.VERBOSE,
)

# substring -> brief field that, if truthy, permits it (None = never permitted)
_CONDITIONAL_KEYWORDS: dict[str, Optional[str]] = {
    "rera": "rera_verified",
    "award": "verified_awards",
    "certified": "verified_certifications",
    "metro station": None,
    "landmark": "verified_landmarks",
}

_ANTI_LOGO_GUARD = (
    " Do not render any company logo, brand wordmark, emblem, monogram, or watermark. "
    "Do not invent brand names. Do not render 'PIKORUA' or any advisory/company name "
    "as visible text. Do not render any text, number, label, or caption that is "
    "not explicitly provided with exact wording in this prompt."
)

# Brand name — always stripped from composition prose (regardless of strip_tech_noise flag).
_BRAND_NAME_RE = re.compile(
    r"(?i)\b(pikorua)\b",
)
# Also strip any sentence that asks to render the brand as a text element.
_BRAND_TEXT_RE = re.compile(
    r"(?i)[^.]*\b(pikorua|company\s+name|brand\s+name|advisory\s+name)\b[^.]*\."
)


def _strip_absolute(text: str) -> str:
    for pat in _ABSOLUTE_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    return text


def _strip_conditional_sentences(text: str, brief: dict) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for s in sentences:
        low = s.lower()
        drop = any(
            kw in low and (field is None or not brief.get(field))
            for kw, field in _CONDITIONAL_KEYWORDS.items()
        )
        if not drop:
            kept.append(s)
    return " ".join(kept)


def _strip_project_name(text: str, brief: dict) -> str:
    name = str(brief.get("property_name", "")).strip()
    if name and name.lower() in text.lower():
        text = re.sub(re.escape(name), "", text, flags=re.IGNORECASE)
    return text


_SPLIT_MARKER = "TEXT STRINGS —"

def _filter_usp_clusters(text: str, brief: dict) -> str:
    """
    Remove any quoted USP clusters that contain labels not present in brief.usps.
    Works on any prose fragment (no marker required) — used both on individual
    LLM-authored fields pre-assembly and on the composition section of a fully
    assembled prompt.
    """
    usps = brief.get("usps") or []
    if not usps:
        return text

    valid = {u.strip().upper() for u in usps}

    # Pattern: a single-quoted run of UPPERCASE items joined by · or ,
    # e.g. 'PRELAUNCH ENTRY · CITY VIEWS · GARDEN ENCLAVE'
    def _filter_cluster(m: re.Match) -> str:
        inner = m.group(1)
        items = [p.strip() for p in re.split(r"[·,]", inner) if p.strip()]
        # keep only items that fuzzy-match a brief USP
        kept = [it for it in items if any(
            it.upper() in v or v in it.upper() for v in valid
        )]
        if not kept:
            return ""
        return "'" + " · ".join(kept) + "'"

    return re.sub(r"'([A-Z][A-Z\s·,\-]{4,})'", _filter_cluster, text)


def _strip_fabricated_usps(text: str, brief: dict) -> str:
    """
    Remove any quoted USP clusters from the composition block that contain labels
    not present in brief.usps. Operates only on the composition section (before the
    text-strings marker) so the actual spec strip items are never touched.
    """
    if _SPLIT_MARKER not in text:
        return text
    comp, strings_tail = text.split(_SPLIT_MARKER, 1)
    comp = _filter_usp_clusters(comp, brief)
    return comp + _SPLIT_MARKER + strings_tail


def _strip_brand_name(text: str) -> str:
    """Always-on: remove brand name text from composition prose (before the strings marker)."""
    if _SPLIT_MARKER in text:
        comp, tail = text.split(_SPLIT_MARKER, 1)
        comp = _BRAND_TEXT_RE.sub("", comp)
        comp = _BRAND_NAME_RE.sub("", comp)
        return comp + _SPLIT_MARKER + tail
    return _BRAND_NAME_RE.sub("", text)


def _cleanup(text: str) -> str:
    text = re.sub(r'["“”]+', "", text)
    text = re.sub(r"#(?![0-9A-Fa-f]{6}\b)\w+", "", text)        # hashtags (keep hex)
    text = re.sub(r"[\U0001F300-\U0001FAFF]", "", text)         # emoji
    text = re.sub(r"\s*\.(?:\s*\.)+", ".", text)
    text = re.sub(r"\s+([.,])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def sanitize_llm_field(text: str, brief: dict) -> str:
    """
    Sanitize a single LLM-generated field (scene_prose or composition prose) — no marker
    protection needed. Returns cleaned text WITHOUT the anti-logo guard (caller appends it).
    """
    brief = brief or {}
    out = _strip_absolute(text)
    out = _strip_conditional_sentences(out, brief)
    out = _TECH_NOISE_RE.sub("", out)
    out = _strip_project_name(out, brief)
    out = _strip_brand_name(out)
    out = _filter_usp_clusters(out, brief)
    return _cleanup(out)


def sanitize(text: str, brief: dict, strip_tech_noise: bool = True) -> str:
    """
    Run the ban list over `text` and append the anti-logo guard.

    strip_tech_noise=True for scene prose / BAKED prompts (free LLM prose).
    Pass the BriefModel.sanitizer_brief() dict (or any dict with the verify flags).

    IMPORTANT: absolute patterns and conditional-sentence stripping apply ONLY to the
    composition/scene section (before the "Render exactly these text strings" marker) —
    never to the text strings block itself, which contains brief facts that must not be
    altered (e.g. "3,500–8,500 SQ FT" must survive the sq-ft ban pattern).
    """
    brief = brief or {}

    # Split at the text-strings marker so we protect the brief-data section.
    if _SPLIT_MARKER in text:
        comp_section, strings_section = text.split(_SPLIT_MARKER, 1)
        comp_section = _strip_absolute(comp_section)
        comp_section = _strip_conditional_sentences(comp_section, brief)
        if strip_tech_noise:
            comp_section = _TECH_NOISE_RE.sub("", comp_section)
        comp_section = _strip_project_name(comp_section, brief)
        comp_section = _strip_brand_name(comp_section)
        comp_section = _strip_fabricated_usps(comp_section + _SPLIT_MARKER + strings_section, brief)
        out = comp_section  # _strip_fabricated_usps re-joins
    else:
        out = _strip_absolute(text)
        out = _strip_conditional_sentences(out, brief)
        if strip_tech_noise:
            out = _TECH_NOISE_RE.sub("", out)
        out = _strip_project_name(out, brief)
        out = _strip_brand_name(out)

    out = _cleanup(out)
    return out + _ANTI_LOGO_GUARD
