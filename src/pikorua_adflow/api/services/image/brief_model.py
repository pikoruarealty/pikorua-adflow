"""
Stage 1 — BriefModel: one schema + extractor for the canonical ad-text fields.

Every downstream module (art_director, compositor, baked_prompt) reads from this
model; nothing reaches into the raw run brief dict. Optional fields render only when
present — they are never fabricated (§1, §3).
"""

from __future__ import annotations

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
        return f"₹{self.price_cr} Cr" if self.price_cr else ""

    @property
    def locality_display(self) -> str:
        return self.locality.upper()

    @property
    def city_display(self) -> str:
        c = self.city.upper()
        return c if c and c != self.locality.upper() else ""

    @property
    def config_display(self) -> str:
        """Config plus the (pluralised) property type, e.g. '4 & 5 BHK APARTMENTS'."""
        cfg = self.config.strip()
        if not cfg:
            return ""
        pt = self.property_type.strip()
        plural = {
            "apartment": "Apartments", "flat": "Apartments", "bungalow": "Bungalows",
            "villa": "Villas", "penthouse": "Penthouses", "duplex": "Duplexes",
            "plot": "Plots", "residence": "Residences",
        }.get(pt.lower(), (pt + "s") if pt and not pt.lower().endswith("s") else pt)
        return f"{cfg} {plural}".strip().upper() if plural else cfg.upper()

    @property
    def cta_text(self) -> str:
        """Badge wording when sample_ready; explicit cta otherwise."""
        if self.sample_ready:
            return (self.cta or f"Sample {self.property_type} Ready").strip().upper()
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
        return cls(
            locality=_clean(brief.get("locality") or brief.get("city")),
            city=_clean(brief.get("city")),
            price_cr=_clean(brief.get("price_cr")),
            config=_clean(brief.get("config") or brief.get("configuration")),
            headline=_clean(headline),
            eyebrow=_clean(eyebrow),
            cta=_clean(cta or brief.get("sample_ready_cta")),
            size_sqft=_clean(brief.get("size_sqft") or brief.get("size")),
            usps=[str(u).strip() for u in usps if str(u).strip()],
            sample_ready=sample_ready,
            cheque_only=bool(brief.get("cheque_only", False)),
            property_name=_clean(brief.get("property_name")),
            property_type=_clean(brief.get("property_type")) or "Apartment",
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
        }
