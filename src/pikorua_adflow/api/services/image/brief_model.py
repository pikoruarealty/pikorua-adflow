"""
Stage 1 — BriefModel: one schema + extractor for the canonical ad-text fields.

Every downstream module (art_director, compositor, baked_prompt) reads from this
model; nothing reaches into the raw run brief dict. Optional fields render only when
present — they are never fabricated (§1, §3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


def _clean(v) -> str:
    return str(v or "").strip()


@dataclass
class BriefModel:
    # Required (always rendered)
    locality: str = ""            # dominant location name (printed large)
    city: str = ""                # secondary location
    price_cr: str = ""            # numeric string; formatted as "₹{price_cr} Cr"
    config: str = ""              # BHK string, e.g. "3 & 4 BHK"
    headline: str = ""            # one line from copy crew output
    eyebrow: str = ""             # short aspirational line (may be empty)
    cta: str = ""                 # call-to-action badge text

    # Optional (render only if present)
    size_sqft: str = ""
    usps: list[str] = field(default_factory=list)
    amenities: list[str] = field(default_factory=list)  # depictable features (scene direction, not footer text)
    sample_ready: bool = False
    cheque_only: bool = False

    # Internal-only — never rendered, used by sanitizer to strip the project name.
    property_name: str = ""
    property_type: str = "Apartment"

    # Logo placement: if True, baked_prompt will ask Ideogram to leave a clear zone
    # at `logo_zone` for the brand mark; compositor places the logo there after generation.
    # If False (default), no brand name or logo zone appears in the ad at all.
    has_logo: bool = False
    logo_zone: str = "top_left"   # top_left | top_right | bottom_left | bottom_right

    # ── Derived display helpers ──────────────────────────────────────────────
    @property
    def price_display(self) -> str:
        """Format price_cr as a clean ad string regardless of how the user entered it.

        Handles: "3", "3.5", "3-6", "3 to 6", "3 for 4 bhk and 4.5 for 5 bhk onwards",
                 "starting at 3 crore", "3 Cr onwards", "₹3–4.5 Cr", etc.
        Strategy: extract ALL decimal numbers, remove any that immediately precede 'bhk'
        (those are configuration counts, not prices). A single explicit numeric range
        ("3-6") is rendered as-is, but multiple per-config starting prices (e.g. one
        price per BHK type) are not a real min-max band — they're each a floor, so we
        show the lowest one with "onwards" rather than implying the top number is a cap.
        """
        raw = (self.price_cr or "").strip()
        if not raw:
            return ""
        # Strip leading ₹ if the user included it
        raw_clean = raw.lstrip("₹").strip()
        # Simple case: already just numbers/range with no text
        if re.fullmatch(r'[\d.,\-–/ ]+', raw_clean):
            nums = re.findall(r'\d+(?:\.\d+)?', raw_clean)
            if len(nums) == 1:
                return f"₹{nums[0]} Cr"
            return f"₹{nums[0]} – {nums[-1]} Cr"
        # Complex free-text: separate price numbers from BHK config numbers.
        # Numbers immediately before "bhk"/"BHK" are configuration counts, not prices.
        all_nums = re.findall(r'\d+(?:\.\d+)?', raw_clean)
        bhk_counts = set(re.findall(r'(\d+(?:\.\d+)?)\s*bhk', raw_clean, re.IGNORECASE))
        prices = [n for n in all_nums if n not in bhk_counts]
        if not prices:
            prices = all_nums  # everything labelled bhk — use all as fallback
        if not prices:
            return ""
        if len(prices) == 1:
            return f"₹{prices[0]} Cr"
        # Multiple per-config prices found in free text: each is a starting price for
        # its own configuration, not a min-max band, so lead with the lowest + "onwards".
        lowest = min(prices, key=float)
        return f"₹{lowest} Cr onwards"

    # Common Indian place-name suffixes — used to find the natural split point.
    _LOCALITY_SUFFIXES = (
        "nagar", "pur", "pura", "bad", "abad", "ganj", "wadi", "wada",
        "palli", "puram", "pete", "halli", "patnam", "kota", "garh",
        "dabad", "kheda", "chowk", "gunj",
    )

    @property
    def locality_display(self) -> str:
        return self.locality.upper()

    @property
    def locality_split_hint(self) -> str:
        """
        For locality names in vertical rail layouts: return a natural two-part split
        hint (e.g. "NEHRU / NAGAR") so the LLM and Ideogram split at the correct word
        boundary — not a random syllable boundary.

        A locality that is ALREADY two separate words (e.g. "Nehru Nagar") is genuinely
        splittable and always gets a hint at that real word boundary. A locality that is
        one single compound word (e.g. "Vastrapur", "Nehrunagar") must never be broken —
        it only gets a hint if it's long enough that Ideogram would otherwise be forced
        to wrap it awkwardly mid-word.
        """
        loc = self.locality
        words = loc.split()
        if len(words) >= 2:
            # Real multi-word name: split into two balanced halves at word boundaries,
            # never mid-word.
            mid = (len(words) + 1) // 2
            return f"{' '.join(words[:mid]).upper()} / {' '.join(words[mid:]).upper()}"
        if len(loc) <= 15:
            return ""
        low = loc.lower()
        for suffix in self._LOCALITY_SUFFIXES:
            if low.endswith(suffix) and len(loc) - len(suffix) >= 3:
                p1 = loc[:len(loc) - len(suffix)].strip().upper()
                p2 = loc[len(loc) - len(suffix):].strip().upper()
                return f"{p1} / {p2}"
        # Fallback: split at the nearest vowel→consonant boundary around the midpoint.
        mid = len(loc) // 2
        vowels = "aeiouAEIOU"
        for offset in range(0, min(mid, 4)):
            for pos in [mid + offset, mid - offset]:
                if 2 <= pos <= len(loc) - 2:
                    if loc[pos - 1] in vowels and loc[pos] not in vowels:
                        return f"{loc[:pos].upper()} / {loc[pos:].upper()}"
        return ""

    @property
    def city_display(self) -> str:
        c = self.city.upper()
        return c if c and c != self.locality.upper() else ""

    @property
    def config_display(self) -> str:
        """Config BHK string, e.g. '4 & 5 BHK APARTMENTS'.

        When `config` is empty, falls back to extracting BHK numbers from property_type
        so campaigns created with property_type = '4 bhk and 5 bhk apartments' still
        get a readable config pill.
        """
        cfg = self.config.strip()
        if not cfg:
            # Try to extract BHK numbers from property_type as fallback
            bhk_nums = re.findall(r'(\d+)\s*bhk', self.property_type, re.IGNORECASE)
            if bhk_nums:
                cfg = " & ".join(bhk_nums) + " BHK"
            else:
                return ""
        pt = self.property_type.strip()
        # Determine a clean property type label (strip sqft/bhk noise from property_type)
        # Use known singular labels; fall back to Apartments if nothing matches.
        _PT_MAP = {
            "apartment": "Apartments", "flat": "Apartments", "bungalow": "Bungalows",
            "villa": "Villas", "penthouse": "Penthouses", "duplex": "Duplexes",
            "plot": "Plots", "residence": "Residences",
        }
        pt_label = ""
        for key, label in _PT_MAP.items():
            if key in pt.lower():
                pt_label = label
                break
        if not pt_label and pt and not any(
            kw in pt.lower() for kw in ("sqft", "sq ft", "bhk", "bedroom")
        ):
            # Clean property_type — use it directly (pluralise if needed)
            pt_label = (pt + "s") if not pt.lower().endswith("s") else pt
        return f"{cfg} {pt_label}".strip().upper() if pt_label else cfg.upper()

    @property
    def cta_text(self) -> str:
        """Badge wording when sample_ready; explicit cta otherwise."""
        if self.sample_ready:
            if self.cta:
                return self.cta.strip().upper()
            # Build a clean fallback from a known property-type label, not the raw
            # property_type field which may contain sqft/bhk noise.
            _PT_LABELS = {
                "apartment": "Apartment", "flat": "Apartment", "bungalow": "Bungalow",
                "villa": "Villa", "penthouse": "Penthouse", "duplex": "Duplex",
                "plot": "Plot", "residence": "Residence",
            }
            pt_raw = self.property_type.lower()
            pt_label = next((lbl for key, lbl in _PT_LABELS.items() if key in pt_raw), "Home")
            return f"SAMPLE {pt_label.upper()} READY"
        return self.cta.strip().upper()

    def footer_items(self) -> list[str]:
        """Up to 3 USP strings for the footer row; cheque flag appended when set."""
        items: list[str] = []
        for u in self.usps:
            for part in str(u).split("/"):
                part = part.strip()
                if part:
                    items.append(part)
        if self.cheque_only and "100% CHEQUE PAYMENT" not in [i.upper() for i in items]:
            items.append("100% Cheque Payment")
        return items[:3]

    # ── Extractor ─────────────────────────────────────────────────────────────
    @classmethod
    def from_brief(
        cls,
        brief: dict,
        headline: str = "",
        eyebrow: str = "",
        cta: str = "",
        sample_ready_override: Optional[bool] = None,
    ) -> "BriefModel":
        """
        Build a BriefModel from a run's `brief` dict. headline / eyebrow / cta come
        from the copy layer (effective_meta), not the brief, so the route passes them.
        sample_ready_override lets the image-gen payload force the flag on/off.
        """
        brief = brief or {}
        usps = brief.get("usps") or brief.get("key_selling_points") or brief.get("key_usps") or []
        if isinstance(usps, str):
            usps = [usps]
        sample_ready = (
            bool(brief.get("sample_ready", False))
            if sample_ready_override is None else bool(sample_ready_override)
        )
        raw_config = _clean(brief.get("config") or brief.get("configuration"))
        raw_property_type = _clean(brief.get("property_type")) or "Apartment"

        if not raw_config:
            # Extract BHK config from property_type when not provided as a separate field.
            # Handles "luxury apartments 4&5 bhk", "4 BHK flat", "4-5 BHK villa",
            # and any number of configs — "3, 4 & 5 BHK" — not just a pair.
            bhk_m = re.search(r'((?:\d+\s*[&,\-–]\s*)+\d+|\d+)\s*BHK', raw_property_type, re.IGNORECASE)
            if bhk_m:
                raw_config = bhk_m.group(0).strip()
                # Normalise every "<num> BHK" run into "n1, n2 & n3"-style joins so no
                # config number is silently dropped, then uppercase BHK.
                nums = re.findall(r'\d+', bhk_m.group(1))
                joined = ", ".join(nums[:-1]) + " & " + nums[-1] if len(nums) > 1 else nums[0]
                raw_config = f"{joined} BHK"
                # Strip the extracted BHK part from property_type so it doesn't duplicate
                raw_property_type = re.sub(
                    re.escape(bhk_m.group(0)), '',
                    raw_property_type, flags=re.IGNORECASE,
                ).strip()

        # Normalise property_type to a clean noun (strip leading adjectives like "luxury")
        clean_pt = re.sub(r'^(luxury|premium|ultra\s+luxury|affordable)\s+', '', raw_property_type, flags=re.IGNORECASE).strip()

        return cls(
            locality=_clean(brief.get("locality") or brief.get("city")),
            city=_clean(brief.get("city")),
            price_cr=_clean(brief.get("price_cr")),
            config=raw_config,
            headline=_clean(headline),
            eyebrow=_clean(eyebrow),
            cta=_clean(cta or brief.get("sample_ready_cta")),
            size_sqft=_clean(brief.get("size_sqft") or brief.get("size")),
            usps=[str(u).strip() for u in usps if str(u).strip()],
            amenities=[str(a).strip() for a in (brief.get("amenities") or []) if str(a).strip()],
            sample_ready=sample_ready,
            cheque_only=bool(brief.get("cheque_only", False)),
            property_name=_clean(brief.get("property_name")),
            property_type=clean_pt or "Apartment",
        )

    def sanitizer_brief(self) -> dict:
        """The flat dict shape the sanitizer's claim-checks expect."""
        return {
            "locality": self.locality,
            "city": self.city,
            "property_type": self.property_type,
            "price_cr": self.price_cr,
            "sample_ready": self.sample_ready,
            "config": self.config,
            "usps": self.usps,
            "property_name": self.property_name,
            # License real structure counts (storeys/towers/sq ft) through the sanitizer
            # only when the property's own amenities/type actually describe them — so an
            # on-brief "four 30-storey towers" survives, but a thin brief still can't
            # hallucinate a storey count.
            "allow_structure_counts": self._has_structure_counts(),
        }

    def _has_structure_counts(self) -> bool:
        from .sanitizer import _STRUCTURE_KEYWORDS
        hay = " ".join([*self.amenities, self.property_type, self.size_sqft]).lower()
        return any(k in hay for k in _STRUCTURE_KEYWORDS)
