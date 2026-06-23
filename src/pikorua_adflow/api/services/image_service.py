"""
Banner-image services: sanitisation, reference-image vision analysis,
Ideogram generation, and logo compositing.

These functions are pure (no imports from campaign_service), so the route layer
composes them with the copy-overlay helpers when generating images.  That keeps the
service dependency graph acyclic.

Sanitisation architecture
-------------------------
Hard bans are loaded once at import time from image_variants.yaml — that file is the
single source of truth.  The sanitiser pipeline has seven stages:

  1. strip absolute-banned claims
  2. strip conditional claims lacking verification
  3. strip never-invent sentences lacking verification
  4. strip technical noise (logo/brand/font/pixel instructions that leak into prompts)
  5. normalize/validate locality against allow-list
  6. enforce sample-ready language consistency
  7. enforce canonical price string + final whitespace/emoji/hashtag cleanup

sanitize_structured_output() is a thin wrapper that operates on the ideogram_prompt
field of the visual_prompter's structured JSON output and returns the full dict intact.
"""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Optional

import litellm
import yaml

from ..config import BRAND_LOGO_PATH, LOGO_DIR, REFERENCE_IMAGES_DIR

# ── Load hard bans from image_variants.yaml at import time ───────────────────
_IMAGE_VARIANTS_PATH = (
    Path(__file__).parent.parent.parent
    / "crews" / "content_crew" / "config" / "image_variants.yaml"
)


def _load_hard_bans() -> dict:
    with open(_IMAGE_VARIANTS_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("hard_bans", {})


def _load_variants_config() -> dict:
    with open(_IMAGE_VARIANTS_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("variants", {})


_HARD_BANS = _load_hard_bans()
_VARIANTS_CONFIG = _load_variants_config()


def _phrase_pattern(phrase: str) -> str:
    """Turn a literal phrase into a whitespace-tolerant regex pattern."""
    return re.escape(phrase).replace(r"\ ", r"\s+")


_ABSOLUTE_PATTERNS = [
    _phrase_pattern(p) for p in _HARD_BANS.get("claims_absolute", [])
]
_CONDITIONAL_PHRASES: dict = _HARD_BANS.get("claims_conditional", {})

# keyword substring → brief field that, if truthy, allows it (None = never allowed)
NEVER_INVENT_KEYWORDS: dict[str, Optional[str]] = {
    k: (None if v == "null" or v is None else v)
    for k, v in _HARD_BANS.get("fabrication_never_invent", {}).items()
}

# Regex for technical noise that must never reach Ideogram
_TECH_NOISE_RE = re.compile(
    r"""(?ix)
    \b\d{3,4}\s*[x×]\s*\d{3,4}\s*px?\b
    | \b\d+K\b
    | [^.]*\b(logo|wordmark|word\s*mark|brand\s*mark|emblem|monogram|watermark
        |company\s*name|brand\s*text|brand\s*name|brand\s*logo|PIKORUA|PIKURUA
        |include\s+(?:the\s+)?brand|add\s+(?:the\s+)?brand|brand\s+instruction
        |brand\s+corner|brand\s+mark\s+instruction)\b[^.]*\.?
    | [^.]*\b\d{1,3}\s*pt\b[^.]*\.?
    | [^.]*\b(Cormorant|Garamond|Didot|Helvetica|Futura|Bodoni|sans.serif|serif\s+at\s+\d)\b[^.]*\.?
    """,
    re.VERBOSE,
)

# Appended to every sanitised prompt so Ideogram never renders a logo or invented text
_ANTI_LOGO_GUARD = (
    " Do not render any company logo, brand wordmark, emblem, monogram, or watermark. "
    "Do not invent brand names.  Do not render any text, number, label, or caption that "
    "is not explicitly provided with exact wording in this prompt."
)

# ── Ideogram knobs ────────────────────────────────────────────────────────────
IDEOGRAM_SPEEDS = {"TURBO", "DEFAULT", "QUALITY"}
IDEOGRAM_RATIOS = {"4x5", "1x1", "16x9", "9x16"}


# ── Reference-image vision analysis ──────────────────────────────────────────

def ref_description_path(img_path: Path) -> Path:
    return img_path.with_suffix(".desc.txt")


def analyze_reference_image(img_path: Path) -> str:
    """Describe a reference image with a vision-capable model.  Cached to disk."""
    desc_path = ref_description_path(img_path)
    if desc_path.exists():
        return desc_path.read_text(encoding="utf-8").strip()
    vision_model = os.getenv(
        "VISION_MODEL", os.getenv("CREATIVE_MODEL", "openrouter/openai/gpt-4o-mini")
    )
    try:
        import base64 as _b64
        img_bytes = img_path.read_bytes()
        b64 = _b64.b64encode(img_bytes).decode()
        ext = img_path.suffix.lstrip(".").lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
        resp = litellm.completion(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "You are a luxury real estate creative director analysing a reference "
                        "ad for creative inspiration — NOT to copy its layout. Describe what "
                        "makes this ad feel premium and desirable, covering: "
                        "(1) PHOTOGRAPHY MOOD — emotion, light quality, atmosphere, texture. "
                        "(2) TYPOGRAPHIC CHARACTER — not positions, but feel: bold/refined, "
                        "decorative elements (gold hairlines, ornate borders, scale contrast). "
                        "(3) COLOUR AND PALETTE ENERGY — dominant emotional colour register. "
                        "(4) PREMIUM SIGNAL — what one element signals genuine luxury. "
                        "DO NOT describe: exact layout positions, column placements, or module "
                        "names. Describe the CREATIVE ENERGY only. Max 200 words. No preamble."
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                ],
            }],
            temperature=0.3,
            max_tokens=300,
        )
        desc = resp.choices[0].message.content.strip()
    except Exception:
        desc = ""
    if desc:
        desc_path.write_text(desc, encoding="utf-8")
    return desc


def ref_layout_path(img_path: Path) -> Path:
    return img_path.with_suffix(".layout.txt")


def extract_reference_ad_layout(img_path: Path) -> str:
    """Extract the ad element layout rules from a reference image using vision LLM.

    Returns 150-200 words of composition_notes — where location name, price, badge,
    footer and other text elements sit in the frame, and how they are styled.
    This is cached to .layout.txt so the vision call happens once per image.
    """
    layout_path = ref_layout_path(img_path)
    if layout_path.exists():
        return layout_path.read_text(encoding="utf-8").strip()

    vision_model = os.getenv(
        "VISION_MODEL", os.getenv("CREATIVE_MODEL", "openrouter/openai/gpt-4o-mini")
    )
    try:
        import base64 as _b64
        img_bytes = img_path.read_bytes()
        b64 = _b64.b64encode(img_bytes).decode()
        ext = img_path.suffix.lstrip(".").lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
        resp = litellm.completion(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "You are a luxury ad art director extracting layout rules from a "
                        "real estate advertisement image so the same layout can be recreated "
                        "in a different photograph.\n\n"
                        "Describe ONLY the positions and styling of text elements. "
                        "Be concrete and spatial — give approximate frame positions "
                        "(top/middle/bottom third, left/centre/right, % height). "
                        "Cover:\n"
                        "• Location/city name — size, weight, position in frame, colour, "
                        "any backing or container\n"
                        "• Price — where it sits, container style (pill, card, strip, floating), "
                        "size relative to location name\n"
                        "• Headline/tagline — position, size, relationship to other elements\n"
                        "• Configuration (BHK) — if visible, where and how it sits\n"
                        "• Footer/spec row — if present: how many items, treatment, position\n"
                        "• Badge or CTA — if present: shape, position, size\n"
                        "• Clean corner — which corner has no text (for logo)\n\n"
                        "Write as concrete imperative instructions (150-200 words), as if "
                        "briefing a designer who will recreate this layout in a new photo. "
                        "Do NOT describe the scene, photography, or colours of the image. "
                        "No preamble."
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                ],
            }],
            temperature=0.2,
            max_tokens=400,
        )
        layout = resp.choices[0].message.content.strip()
    except Exception:
        layout = ""

    if layout:
        layout_path.write_text(layout, encoding="utf-8")
    return layout


def build_reference_images_context() -> str:
    """Text block describing uploaded reference images, injected into crew input."""
    if not REFERENCE_IMAGES_DIR.exists():
        return "None uploaded."
    descs = []
    for p in sorted(REFERENCE_IMAGES_DIR.glob("*")):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        dp = ref_description_path(p)
        if dp.exists():
            descs.append(f"• [{p.name}] {dp.read_text(encoding='utf-8').strip()}")
    if not descs:
        return "None uploaded."
    return (
        "Reference ads for creative inspiration — study these for the visual language "
        "of premium real estate advertising: how text zones relate to photography, how "
        "price and location are made unmissable, the quality of typographic containers "
        "and ornamental detail.  Let the references inspire the approach; the actual "
        "structure should emerge naturally from the image's own composition and the "
        "available campaign data.\n"
        + "\n".join(descs)
    )


# ── Sanitisation pipeline ─────────────────────────────────────────────────────

def _strip_absolute_claims(prompt: str) -> str:
    for pattern in _ABSOLUTE_PATTERNS:
        prompt = re.sub(pattern, "", prompt, flags=re.IGNORECASE)
    return prompt


def _strip_conditional_claims(prompt: str, brief: dict) -> str:
    for phrase, verified_field in _CONDITIONAL_PHRASES.items():
        if not brief.get(verified_field):
            prompt = re.sub(_phrase_pattern(phrase), "", prompt, flags=re.IGNORECASE)
    return prompt


def _strip_never_invent(prompt: str, brief: dict) -> str:
    """Drop any sentence that references a never-invent category unless the brief
    explicitly supplies/verifies that field."""
    sentences = re.split(r"(?<=[.!?])\s+", prompt)
    kept = []
    for sentence in sentences:
        low = sentence.lower()
        drop = False
        for keyword, verified_field in NEVER_INVENT_KEYWORDS.items():
            if keyword in low:
                if verified_field is None or not brief.get(verified_field):
                    drop = True
                    break
        if not drop:
            kept.append(sentence)
    return " ".join(kept)


def _strip_tech_noise(prompt: str) -> str:
    """Remove logo/brand/font instructions and pixel dimensions from the prompt."""
    return _TECH_NOISE_RE.sub("", prompt)


def _normalize_locality(prompt: str, brief: dict, allowed_localities: set) -> str:
    """If the brief's locality is not on the allow-list, fall back to city-level."""
    locality = brief.get("locality", "")
    city = brief.get("city", "")
    if locality and allowed_localities and locality not in allowed_localities:
        prompt = re.sub(re.escape(locality), city, prompt, flags=re.IGNORECASE)
    return prompt


def _handle_sample_ready(prompt: str, brief: dict) -> str:
    if brief.get("sample_ready"):
        unit = brief.get("property_type", "Apartment")
        cta = brief.get("sample_ready_cta") or f"Sample {unit} Ready — Visit Today"
        already_present = "sample" in prompt.lower() and (
            "ready" in prompt.lower() or "visit" in prompt.lower() or "open" in prompt.lower()
        )
        if not already_present:
            prompt += f" Include a small badge element with the text '{cta}'."
        prompt = re.sub(r"\bunder construction\b", "fully finished", prompt, flags=re.IGNORECASE)
        prompt = re.sub(r"\bcoming soon\b", "ready to visit", prompt, flags=re.IGNORECASE)
    else:
        prompt = re.sub(r"sample\s+\w+\s+ready", "", prompt, flags=re.IGNORECASE)
        prompt = re.sub(r"visit today", "", prompt, flags=re.IGNORECASE)
    return prompt


def _enforce_price_format(prompt: str, brief: dict) -> str:
    """If the model paraphrased the price, force the canonical string back in."""
    price_cr = (brief.get("price_cr") or "").strip()
    if price_cr:
        canonical = f"₹{price_cr} Cr"
        if canonical not in prompt:
            prompt += f" The price line must read exactly '{canonical}'."
    return prompt


def _strip_common_noise(prompt: str) -> str:
    prompt = re.sub(r"\s+", " ", prompt).strip()
    prompt = re.sub(r'["“”]+', "", prompt)   # stray quote marks
    prompt = re.sub(r"#(?![0-9A-Fa-f]{6}\b)\w+", "", prompt)  # hashtags (not hex colors)
    prompt = re.sub(r"[\U0001F300-\U0001FAFF]", "", prompt)  # emoji
    # Collapse empty-sentence debris left after stripping
    prompt = re.sub(r"\s*\.(?:\s*\.)+", ".", prompt)
    prompt = re.sub(r"\s+([.,])", r"\1", prompt)
    prompt = re.sub(r"\s{2,}", " ", prompt)
    return prompt.strip()


def sanitize_image_prompt(
    raw_prompt: str,
    brief: dict,
    allowed_localities: Optional[set] = None,
    assembled: bool = False,
) -> str:
    """
    Sanitisation pipeline, then appends the anti-logo guard.

    assembled=True  — prompt was built by build_gpt_image_prompt(); only run claims
                      checking and tech-noise stages (1-5). Skips sample-ready
                      insertion and price enforcement because those are already baked
                      into the assembled prompt deterministically.
    assembled=False — legacy prose prompt; run all seven stages.

    brief keys used:
      locality, city, property_type, price_cr, sample_ready,
      rera_verified, verified_awards, verified_certifications,
      verified_landmarks, possession_date (all optional, default False/empty).
    """
    allowed_localities = allowed_localities or set()
    prompt = raw_prompt
    prompt = _strip_absolute_claims(prompt)
    prompt = _strip_conditional_claims(prompt, brief)
    prompt = _strip_never_invent(prompt, brief)
    if not assembled:
        # Tech-noise regex eats structure bullets that mention "logo compositing";
        # assembled prompts are Python-assembled, not LLM prose, so skip this stage.
        prompt = _strip_tech_noise(prompt)
    prompt = _normalize_locality(prompt, brief, allowed_localities)
    if not assembled:
        prompt = _handle_sample_ready(prompt, brief)
        prompt = _enforce_price_format(prompt, brief)
    prompt = _strip_common_noise(prompt)
    # Strip project name if it leaked into the prompt — it is internal-only
    property_name = brief.get("property_name", "").strip()
    if property_name and property_name.lower() in prompt.lower():
        import re as _re
        prompt = _re.sub(_re.escape(property_name), "", prompt, flags=_re.IGNORECASE).strip()
    return prompt + _ANTI_LOGO_GUARD


def sanitize_structured_output(
    structured: dict,
    brief: dict,
    allowed_localities: Optional[set] = None,
) -> dict:
    """
    Sanitise the ideogram_prompt field of a visual_prompter structured output dict
    and return the full dict intact.  Pass the parsed pydantic model dict or a raw
    dict with at least an 'ideogram_prompt' key.
    """
    out = dict(structured)
    out["ideogram_prompt"] = sanitize_image_prompt(
        out.get("ideogram_prompt", ""), brief, allowed_localities
    )
    return out


# ── Prompt assembly: palettes, ad structures, and gpt-image-1 brief builder ──

# Six luxury colour palettes — all within Indian luxury RE territory.
# Gold (#C9A84C) is always the primary accent; what varies is the structural
# backing colour and secondary text tone.
PALETTE_CONFIGS: dict[str, str] = {
    "navy_gold": (
        "• Primary text: warm gold (#C9A84C), bold tracked serif\n"
        "• Secondary text: pure white, medium weight\n"
        "• Accent and borders: gold (#C9A84C) hairlines\n"
        "• Structural backing (strip, panel, border): deep navy (#0D1B2A)\n"
        "• Overall feel: authoritative, formal, classic Indian luxury launch campaign"
    ),
    "charcoal_gold": (
        "• Primary text: warm gold (#C9A84C), bold tracked serif\n"
        "• Secondary text: warm white (#F8F4F0), medium weight\n"
        "• Accent and borders: gold (#C9A84C) hairlines\n"
        "• Structural backing: warm charcoal (#2B2420)\n"
        "• Overall feel: bold and contemporary, premium Indian developer quality"
    ),
    "forest_gold": (
        "• Primary text: warm gold (#C9A84C), bold tracked serif\n"
        "• Secondary text: cream (#F5F0E8), clean weight\n"
        "• Accent and borders: gold (#C9A84C) hairlines\n"
        "• Structural backing: deep forest green (#1C3325)\n"
        "• Overall feel: distinctive, premium, botanical luxury — DLF / prestige category"
    ),
    "burgundy_gold": (
        "• Primary text: brushed amber-gold (#B8860B), bold tracked serif\n"
        "• Secondary text: warm ivory (#F5F0E8), clean weight\n"
        "• Accent and borders: brushed bronze (#9A7B4F) hairlines\n"
        "• Structural backing: rich dark burgundy (#3D0C02) or deep wine (#2C0A1A)\n"
        "• Overall feel: heritage opulence, old-money, distinct from the standard navy category"
    ),
    "slate_cream": (
        "• Primary text: warm gold (#C9A84C), bold weight\n"
        "• Secondary text: platinum white (#E8E8E8), light-medium weight\n"
        "• Accent and borders: gold (#C9A84C) or platinum hairlines\n"
        "• Structural backing: cool dark slate (#1E2430)\n"
        "• Overall feel: contemporary premium, architectural, cooler — suits structural scenes"
    ),
    "ivory_warmth": (
        "• Primary text: warm gold (#C9A84C), bold tracked serif\n"
        "• Secondary text: deep charcoal (#2B2420), readable weight\n"
        "• Accent and borders: gold (#C9A84C) delicate hairline or none\n"
        "• Structural backing: NONE — no solid dark panels; text on natural scene surfaces\n"
        "• Overall feel: warm, bright, premium aspirational — morning or daylight energy"
    ),
}

# Compact palette reference — used when composition_notes drives layout.
# Only the colour tokens; placement is handled by the scene-specific composition_notes.
_PALETTE_COMPACT: dict[str, str] = {
    "charcoal_gold":  "Gold #C9A84C on warm charcoal #2B2420 backing. Secondary text warm white #F8F4F0. Gold hairline accents. These are the palette anchor colours — each element adapts for contrast based on its specific surface.",
    "burgundy_gold":  "Amber-gold #B8860B on dark burgundy #3D0C02 backing. Secondary text warm ivory #F5F0E8. Bronze hairlines #9A7B4F. These are the palette anchor colours — each element adapts for contrast based on its specific surface.",
    "forest_gold":    "Gold #C9A84C on deep forest green #1C3325 backing. Secondary text cream #F5F0E8. Gold hairlines. These are the palette anchor colours — each element adapts for contrast based on its specific surface.",
    "navy_gold":      "Gold #C9A84C on deep navy #0D1B2A backing. Secondary text pure white. Gold hairlines. These are the palette anchor colours — each element adapts for contrast based on its specific surface.",
    "slate_cream":    "Gold #C9A84C on cool dark slate #1E2430 backing. Secondary text platinum white #E8E8E8. Gold or platinum hairlines. These are the palette anchor colours — each element adapts for contrast based on its specific surface.",
    "ivory_warmth":   "Gold #C9A84C. Secondary text deep charcoal #2B2420. No solid dark panels anywhere — text sits on natural scene surfaces. These are the palette anchor colours — each element adapts for contrast based on its specific surface.",
}

# Three ad structures — all carry full developer-ad information density.
# They differ in how information is composed across the frame, not how much.
AD_STRUCTURES: dict[str, str] = {
    "bordered_campaign": (
        "• Full-bleed hero property photograph filling the entire canvas\n"
        "• Elegant gold hairline border framing the composition edge-to-edge\n"
        "• Strong visual hierarchy with editorial typography overlaid on the photograph's "
        "natural dark areas — sky, shadow zones, ground\n"
        "• Large location name as the primary typographic element — bold gold, all-caps\n"
        "• Secondary location descriptor and lifestyle headline\n"
        "• Dedicated luxury pricing panel\n"
        "• Premium sample apartment badge (only if applicable)\n"
        "• Bottom information strip containing property modules\n"
        "• One corner kept completely clean — reserved for logo compositing\n"
        "• All information elements feel agency-designed, not automatically placed"
    ),
    "structured_split": (
        "• Hero photograph filling the upper portion of the frame — no border\n"
        "• Structured information zone beneath — backed in the chosen palette colour\n"
        "• Location name dominant within the information zone — bold gold, all-caps\n"
        "• Lifestyle headline as the transition between photo and info zone\n"
        "• Pricing module and property details in the information zone\n"
        "• Sample apartment badge within the information zone (only if applicable)\n"
        "• One corner kept completely clean — reserved for logo compositing"
    ),
    "immersive_fullbleed": (
        "• Full-bleed hero photograph, completely edge-to-edge, no border, no solid backing zones\n"
        "• All text elements integrated into the photograph through natural composition\n"
        "• Lifestyle headline — the largest typographic element, positioned where the scene "
        "naturally creates space\n"
        "• Location name — gold, dominant, in the scene's darkest or most open natural area\n"
        "• City name — nearby, subordinate\n"
        "• Pricing module — refined container placed within the scene's natural composition, not forced\n"
        "• Configuration and key info — compact module strip using the scene's natural edge zone\n"
        "• Sample apartment badge floating in available negative space (only if applicable)\n"
        "• One corner kept completely clean — reserved for logo compositing\n"
        "• The photograph surrounds the typography rather than providing a dedicated panel\n"
        "• If the composition makes a specific element genuinely unreadable, omit it for this image\n"
        "  only — default to full developer ad density"
    ),
}


# ── Learned design grammar (recipes + detail library + vocabulary additions) ──
# Distilled from curated reference ads (see design_principles.yaml). The recipe is the
# coherent design bundle the LLM picks per variant; we weave its fields into the prompt.
# Vocabulary additions are merged into the palette/structure glossaries below so recipe
# references resolve. Loading is defensive: if the file is absent, the pipeline falls back
# to the legacy palette/structure behaviour with no recipe overlay.
_DESIGN_PRINCIPLES_PATH = (
    Path(__file__).parent.parent.parent
    / "crews" / "content_crew" / "config" / "design_principles.yaml"
)


def _load_design_principles() -> dict:
    try:
        with open(_DESIGN_PRINCIPLES_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


_DESIGN_PRINCIPLES = _load_design_principles()
_RECIPES_BY_NAME: dict[str, dict] = {
    r["name"]: r
    for r in _DESIGN_PRINCIPLES.get("recipes", [])
    if isinstance(r, dict) and r.get("name")
}
_LAYOUT_DISCIPLINE: list[str] = _DESIGN_PRINCIPLES.get("layout_discipline", []) or []
_DETAIL_PRINCIPLES: dict = _DESIGN_PRINCIPLES.get("detail_principles", {}) or {}

# Merge vocabulary additions into the palette / structure glossaries so recipe
# palette_family / structure_family references resolve to concrete descriptions.
_VOCAB = _DESIGN_PRINCIPLES.get("vocabulary_additions", {}) or {}
for _name, _desc in (_VOCAB.get("palettes") or {}).items():
    PALETTE_CONFIGS.setdefault(_name, f"• Overall colour world: {_desc}")
for _name, _desc in (_VOCAB.get("structures") or {}).items():
    AD_STRUCTURES.setdefault(_name, f"• {_desc}")

# Generic structure names used in recipes that map onto our canonical structures.
_STRUCTURE_ALIASES: dict[str, str] = {
    "full-bleed": "immersive_fullbleed",
    "full_bleed": "immersive_fullbleed",
    "fullbleed": "immersive_fullbleed",
    "immersive": "immersive_fullbleed",
    "split": "structured_split",
    "structured_split": "structured_split",
    "bordered": "bordered_campaign",
    "framing_device": "bordered_campaign",
}

# Recipe text_roles → which typography sections to emit. Anything not requested is
# suppressed, so moderate/teaser recipes render less text than full_detail ones.
_ALL_TEXT_ROLES = {"headline", "subhead", "price", "info_band", "cta", "badge"}


def _expand_structures(structure_family: list) -> str:
    """Expand a recipe's structure_family names into concrete layout descriptions."""
    if not structure_family:
        return ""
    seen: set[str] = set()
    out: list[str] = []
    for name in structure_family:
        key = _STRUCTURE_ALIASES.get(name, name)
        if key in seen:
            continue
        seen.add(key)
        desc = AD_STRUCTURES.get(key)
        if desc:
            out.append(f"[{name}]\n{desc}")
        else:
            out.append(f"[{name}] — compose the layout in this distinctive manner.")
    return "\n".join(out)


def _format_recipe_block(recipe: dict) -> str:
    """Render the chosen recipe's concrete art-direction fields as short Ideogram bullets.
    Deliberately omits palette_family (covered by palette_config) and why_it_works (prose
    that confuses image models into prioritising creative description over text callouts)."""
    lines = [f"Design treatment (recipe: {recipe.get('name', '')}):"]
    if recipe.get("lighting"):
        lines.append(f"• Light: {recipe['lighting']}")
    # Support both old field name (subject_treatment) and new (subject_rule)
    subject = recipe.get("subject_rule") or recipe.get("subject_treatment")
    if subject:
        lines.append(f"• Subject: {subject}")
    # Support both old (negative_space) and new (negative_space_rule)
    neg_space = recipe.get("negative_space_rule") or recipe.get("negative_space")
    if neg_space:
        lines.append(f"• Negative space: {neg_space}")
    # Support both old (type_move) and new (type_treatment)
    type_treat = recipe.get("type_treatment") or recipe.get("type_move")
    if type_treat:
        lines.append(f"• Type treatment: {type_treat}")
    if recipe.get("footer_backing"):
        lines.append(f"• Footer/panel backing colour: {recipe['footer_backing']}")
    return "\n".join(lines)


def _format_detail_principles(keys: tuple[str, ...]) -> str:
    """Fold a short slice of the detail-principle library into the prompt."""
    out: list[str] = []
    for k in keys:
        rules = _DETAIL_PRINCIPLES.get(k) or []
        for rule in rules:
            out.append(f"• {rule}")
    return "\n".join(out)


def _recipe_text_roles(recipe: Optional[dict]) -> Optional[set]:
    """The set of text roles a recipe carries, or None to render the full default set."""
    if not recipe:
        return None
    roles = recipe.get("text_roles") or []
    return {str(r).strip() for r in roles} or None


def _select_structure(variant_key: str, tone_tag: str) -> str:
    """Return the structure name for this variant + tone combination."""
    variant_cfg = _VARIANTS_CONFIG.get(variant_key, {})
    structure_map = variant_cfg.get("structure_map", {})
    # Default fallback: bordered_campaign for dark, immersive for bright
    defaults = {
        "dark_luxury": "bordered_campaign",
        "bright_aspirational": "immersive_fullbleed",
    }
    tone = tone_tag if tone_tag in ("dark_luxury", "bright_aspirational") else "dark_luxury"
    return structure_map.get(tone) or defaults[tone]


def _build_typography_block(
    entry: dict, brief: dict,
    allowed_roles: Optional[set] = None,
    info_band_style: Optional[str] = None,
    composition_driven: bool = False,
) -> str:
    """
    Build the Typography hierarchy section from property brief data.
    All values come from brief — nothing invented.

    composition_driven=True — emits ONLY the raw text strings with brief labels.
      No structural layout language (no band styles, no placement instructions).
      Used when composition_notes in the entry drives all layout decisions.

    composition_driven=False (default) — full templated output with band styles:
      allowed_roles — from a recipe's text_roles; gates which sections emit.
      info_band_style — controls the layout of the bottom information section:
        "column_footer"    — specs stack inside the sidebar column
        "icon_grid_strip"  — icon-above-label columns separated by gold rules
        "price_hero_strip" — price dominant in centre, specs flanking
        "asymmetric_band"  — large price left, stacked specs right
        "compact_spec_row" — one narrow spec row at very bottom
        "strip_three_col"  — standard 3-column footer strip (fallback)
        None               — defaults to strip_three_col
    """
    # ── composition_driven mode: flat text-string list only ───────────────────
    if composition_driven:
        locality = (brief.get("locality") or brief.get("city") or "").upper()
        city     = (brief.get("city") or "").upper()
        price_cr = str(brief.get("price_cr") or "").strip()
        config_v = str(brief.get("config") or "").strip()
        sample_ready = bool(brief.get("sample_ready"))
        # entry.badge_cta takes priority so the typography block and composition_notes
        # always agree on badge wording — prevents mismatched text wrapping in the pill.
        sample_cta = (
            entry.get("badge_cta") or
            brief.get("sample_ready_cta") or
            f"Sample {brief.get('property_type','Apartment')} Ready"
        ).strip().upper()
        headline = (entry.get("headline") or "").strip()
        eyebrow  = (entry.get("eyebrow")  or "").strip()
        usps = brief.get("usps") or []
        if isinstance(usps, str):
            usps = [usps]
        usp_parts = []
        for u in usps:
            usp_parts.extend([p.strip() for p in u.split("/") if p.strip()])
        config_parts = config_v.rsplit(" ", 1) if config_v else []
        config_top    = config_parts[0] if config_parts else ""
        config_bottom = (config_parts[1] + " RESIDENCES") if len(config_parts) > 1 else ""
        config_combined = f"{config_top} {config_bottom}".strip()

        lines = [
            "Text elements to render — exact wording only, placement per composition notes above:",
            "",
        ]
        if eyebrow:
            lines.append(f'Eyebrow: "{eyebrow}"')
        lines.append(f'Primary Headline (one unbroken line, never hyphenated — scale down to fit): "{locality}"')
        if city and city != locality:
            lines.append(f'City: "{city}"')
        if headline:
            lines.append(f'Campaign tagline: "{headline}"')
        if price_cr:
            lines.append(f'Price: "\u20b9{price_cr} Cr ONWARDS"')
        if sample_ready:
            lines.append(f'Sample badge: "{sample_cta}"')
        # Apartment configuration (BHK) is a PRIMARY photo-zone element — it is kept
        # OUT of the footer spec list so the model never double-places or shrinks it.
        if config_combined:
            lines.append(
                f'Apartment configuration (render as a LARGE standalone element in the '
                f'photo zone — never in the footer, never a corner label): "{config_combined}"'
            )
        # Footer / supporting specs: USPs only. BHK excluded above. Fill-to-3 applies
        # to THESE items so a strip/grid that IS rendered carries balanced content.
        spec_items = list(usp_parts)
        if brief.get("cheque_only"):
            spec_items.append("100% CHEQUE PAYMENT")
        # Pad to at least 3 spec items for sparse briefs — same fallback pool as
        # the legacy icon_grid_strip / compact_spec_row paths. Only relevant WHEN the
        # composition_notes render a strip/grid; floating-line footers ignore the 3rd.
        _COMP_FOOTER_DEFAULTS = ["GATED COMMUNITY", "SIGNATURE LIVING", "ELEVATED SPACES"]
        _used_spec = [s.upper() for s in spec_items]
        for _d in _COMP_FOOTER_DEFAULTS:
            if len(spec_items) >= 3:
                break
            if _d.upper() not in _used_spec:
                spec_items.append(_d)
                _used_spec.append(_d.upper())
        if spec_items:
            lines.append(
                f'Footer / supporting specs (only if the composition renders a strip or '
                f'icon grid; a floating spec line may use just the first two): '
                f'{", ".join(repr(s) for s in spec_items)}'
            )
        lines.append("")
        lines.append("Do not alter any wording. Do not render any text not listed above.")
        return "\n".join(lines)
    def want(role: str) -> bool:
        return allowed_roles is None or role in allowed_roles

    locality = (brief.get("locality") or brief.get("city") or "").upper()
    city = (brief.get("city") or "").upper()
    price_cr = str(brief.get("price_cr") or "").strip()
    config_val = str(brief.get("config") or brief.get("configuration") or "").strip()
    sample_ready = bool(brief.get("sample_ready"))
    property_type = brief.get("property_type", "Apartment")
    sample_cta = (
        brief.get("sample_ready_cta")
        or f"Sample {property_type} Ready — Visit Today"
    ).strip().upper()
    headline = (entry.get("headline") or "").strip()
    eyebrow = (entry.get("eyebrow") or "").strip()

    # Strip locality name from eyebrow — locality is always the Primary Headline and
    # must not appear twice. "NEHRUNAGAR PRELAUNCH" → "PRELAUNCH".
    if locality and locality.lower() in eyebrow.lower():
        eyebrow = re.sub(re.escape(locality), "", eyebrow, flags=re.IGNORECASE)
        eyebrow = re.sub(r"^[\s,\-—]+|[\s,\-—]+$", "", eyebrow).strip()

    usps = (
        brief.get("usps")
        or brief.get("key_selling_points")
        or brief.get("key_usps")
        or []
    )
    if isinstance(usps, str):
        usps = [usps]
    usp = usps[0].strip() if usps else ""
    usp_parts = [p.strip() for p in usp.split("/", 1)] if "/" in usp else ([usp] if usp else [])
    # "100% cheque payment" is a hard selling point for premium Indian buyers — surface
    # it as a spec/USP callout (never invented; only when the brief flags it).
    if brief.get("cheque_only") and "100% CHEQUE PAYMENT" not in [p.upper() for p in usp_parts]:
        usp_parts.append("100% Cheque Payment")

    # Config: "3 & 4 BHK" → top="3 & 4", bottom="BHK RESIDENCES"
    config_parts = config_val.rsplit(" ", maxsplit=1) if config_val else []
    config_top = config_parts[0] if config_parts else ""
    config_bottom = (config_parts[1] + " RESIDENCES") if len(config_parts) > 1 else ""
    config_combined = f"{config_top} {config_bottom}".strip()

    # Styles that place price INSIDE the band (suppress standalone Pricing Module above).
    # Only suppress the standalone when the recipe also wants price — otherwise
    # price disappears from the ad entirely.
    style = info_band_style or "strip_three_col"
    price_goes_in_band = style in ("price_hero_strip", "asymmetric_band") and want("price")

    lines = ["Typography hierarchy:", ""]

    # ── Eyebrow ───────────────────────────────────────────────────────────────
    if eyebrow and (want("subhead") or want("headline")):
        lines += ["Top Eyebrow:", f'"{eyebrow}"', ""]

    # ── Headline + city ───────────────────────────────────────────────────────
    lines += [
        "Primary Headline:",
        f'"{locality}"',
        "(CRITICAL: render this as a SINGLE UNBROKEN WORD on one line — never hyphenate, never split across lines. Scale font size down to fit; do not break the name.)",
        "",
    ]
    if city and city != locality:
        lines += ["City:", f'"{city}"', ""]

    # ── Campaign tagline ──────────────────────────────────────────────────────
    if headline and want("subhead"):
        lines += [
            "Campaign Tagline (secondary — body scale, NOT a second headline):",
            f'"{headline}"',
            "",
        ]

    # ── Standalone Pricing Module (suppressed for price-in-band styles) ───────
    pricing_above = False
    if price_cr and want("price") and not price_goes_in_band:
        lines += ["Pricing Module:", f'"₹{price_cr} Cr"', '"ONWARDS"', ""]
        pricing_above = True

    # ── Bottom information section ────────────────────────────────────────────
    has_content = want("info_band") and bool(config_val or price_cr or usp)
    badge_rendered = False

    if has_content:

        if style == "column_footer":
            # The headline card/pill holds ONLY headline + price + badge — keep it uncluttered.
            # Config and USPs overflow into the lower photo zone in a lighter typographic key.
            if sample_ready and want("badge"):
                lines += [
                    f'PROMINENT BADGE (anchored below the price, inside the headline card — bold pill, gold border, palette-matching backing, large and clearly readable): "{sample_cta}"',
                    "",
                ]
                badge_rendered = True

            # Spec details go into the photo zone bottom with a LEGIBLE backing treatment.
            # Never floating white text on a bright background — must be readable.
            spec_overflow: list[str] = []
            if config_val:
                spec_overflow.append(config_combined)
            for part in usp_parts:
                if part:
                    spec_overflow.append(part)
            if spec_overflow:
                spec_line = "  ·  ".join(spec_overflow)
                lines += [
                    "Bottom Photo Zone Specs — render in the lower photo area (NOT inside the column).",
                    "CRITICAL: the text MUST be legible. Choose one backing treatment that suits the scene:",
                    "  • Slim semi-transparent dark strip: spans the bottom 8% of the photo zone only (not full canvas) — text in gold or cream over it",
                    "  • Corner stamp or seal: small premium circular/hexagonal element, bottom-right corner, gold border, dark fill, spec text centred inside",
                    "  • Thin gold rule + dark vignette: a hairline gold rule above the text; a soft dark gradient behind the text only, enough to read against",
                    "Whatever treatment — light tracked uppercase, small scale, horizontal layout. Clearly readable at arm's length.",
                    f'Text: "{spec_line}"',
                    "",
                ]

        elif style == "price_hero_strip":
            # Badge anchored just above the band; price is the dominant centre element
            if sample_ready and want("badge"):
                lines += [
                    "PROMINENT BADGE (large pill, gold border, high-contrast backing matching the palette — anchored to bottom of photo zone, centred, must be clearly readable at arm's length):",
                    f'"{sample_cta}"',
                    "",
                ]
                badge_rendered = True
            lines += [
                "Bottom Band (dark backing — price dominant in centre, spec text flanking):",
                "",
            ]
            if config_val:
                lines.append(f'Left (small tracked caps): "{config_combined}"')
                lines.append("")
            if price_cr and want("price"):
                lines += ["Centre HERO (large gold):", f'"₹{price_cr} Cr"', '"ONWARDS"', ""]
            if usp_parts:
                right_text = " / ".join(p for p in usp_parts if p)
                lines.append(f'Right (small tracked caps): "{right_text}"')
                lines.append("")

        elif style == "asymmetric_band":
            # Badge anchored just above the band, right-aligned
            if sample_ready and want("badge"):
                lines += [
                    "PROMINENT BADGE (bold pill, gold border, contrasting backing from the palette — anchored to bottom-right of photo zone, just above the footer band, large enough to read at a glance):",
                    f'"{sample_cta}"',
                    "",
                ]
                badge_rendered = True
            lines += ["Bottom Strip (asymmetric dark backing — large price left, stacked specs right):", ""]
            if price_cr and want("price"):
                lines += [f'"₹{price_cr} Cr ONWARDS" — large, left block', ""]
            if config_val:
                lines += [f'"{config_combined}" — secondary, below price in left block', ""]
            if usp_parts:
                lines.append("Right column:")
                for part in usp_parts:
                    if part:
                        lines.append(f'"{part}"')
                lines.append("")

        elif style == "compact_spec_row":
            # Badge sits just above the spec row — must be large and readable
            if sample_ready and want("badge"):
                lines += [
                    f'PROMINENT BADGE (bold pill or rectangle, high-contrast against the scene — anchored above the spec row, centred or right-aligned, large and unmissable): "{sample_cta}"',
                    "",
                ]
                badge_rendered = True
            _LUXURY_FOOTER_DEFAULTS_SPEC = ["GATED COMMUNITY", "SIGNATURE LIVING", "ELEVATED SPACES"]
            spec_parts: list[str] = []
            if config_val:
                spec_parts.append(config_combined)
            if price_cr and not pricing_above:
                spec_parts.append(f"₹{price_cr} Cr ONWARDS")
            for part in usp_parts:
                if part:
                    spec_parts.append(part)
            # Pad to at least 2 items when brief is sparse
            _used_spec = [s.upper() for s in spec_parts]
            for _d in _LUXURY_FOOTER_DEFAULTS_SPEC:
                if len(spec_parts) >= 3:
                    break
                if _d.upper() not in _used_spec:
                    spec_parts.append(_d)
                    _used_spec.append(_d.upper())
            if spec_parts:
                spec_line = "  ·  ".join(spec_parts)
                lines += [
                    "Specification Row at very bottom of frame:",
                    "CRITICAL: this text MUST be legible at arm's length — not micro-print.",
                    "CRITICAL: Do NOT place any part of this specification (especially the apartment configuration) as a small corner watermark or tiny label elsewhere in the image. The spec row below is its ONLY placement.",
                    "Use a slim solid backing strip (full canvas width, 8-10% height) in the palette's darkest backing colour.",
                    "Text: BOLD ALL CAPS, tracked geometric sans, sized so each word is clearly readable without zooming.",
                    "Gold or cream text on the dark strip — never white text on a bright or busy background.",
                    f'"{spec_line}"',
                    "",
                ]

        elif style == "icon_grid_strip":
            # Icon-grid strip: amenities/features as icon+label columns separated by gold rules.
            # Price stays in the standalone Pricing Module above — not repeated here.
            _LUXURY_FOOTER_DEFAULTS = ["GATED COMMUNITY", "SIGNATURE LIVING", "ELEVATED SPACES"]
            grid_items: list[str] = []
            if config_val:
                grid_items.append(config_combined)
            for part in usp_parts:
                if part:
                    grid_items.append(part)
            if sample_ready and want("badge"):
                grid_items.append(sample_cta)
                badge_rendered = True
            # Pad to at least 2 items with always-true luxury attributes when brief is sparse
            _used = [g.upper() for g in grid_items]
            for _d in _LUXURY_FOOTER_DEFAULTS:
                if len(grid_items) >= 3:
                    break
                if _d.upper() not in _used:
                    grid_items.append(_d)
                    _used.append(_d.upper())
            if grid_items:
                col_str = "  |  ".join(f'"{g}"' for g in grid_items)
                lines += [
                    "Bottom Amenity Grid (dark backing strip, maximum 12% canvas height — slim branded footer, never dominant):",
                    "Each column: a THIN LINE-ART GOLD ICON relevant to the feature (distinct per column — e.g. sofa/amenities for clubhouse, ruler/area for size, shield/gate for gated community). Never use a generic or identical icon across columns.",
                    "FOOTER GRID GEOMETRY: The footer follows a strict column grid. A vertical gold hairline marks the exact centre between columns. Each column has its own axis — the icon is horizontally centred on that axis, and the label text is centred directly beneath the icon on the same axis. Both columns are identical in width with equal outer margins and equal distance from hairline to icon centre. Every element snaps to this invisible grid — nothing floats independently.",
                    "Typography: Bold or ExtraBold geometric sans, ALL CAPS, generously tracked. Gold on dark — fully legible from several feet away. Identical optical weight across all columns.",
                    "NEGATIVE CONSTRAINTS: Never allow icons to drift independently. Never use uneven padding between columns. Never vary icon scale between columns. Never offset one column lower than the other. Never make one label visually heavier than its opposite.",
                    f"Columns (left to right): {col_str}",
                    "",
                ]

        else:  # strip_three_col (default)
            lines += [
                "Bottom Information Band (BOLD ALL CAPS, three equal panels — use the palette's backing and text colours for this band):",
                "",
            ]
            if config_val:
                lines += ["Left Module:", f'"{config_top}"']
                if config_bottom:
                    lines.append(f'"{config_bottom}"')
                lines.append("")
            # Centre: badge if sample_ready (visually distinct); otherwise price if not shown above
            if sample_ready and want("badge"):
                lines += [
                    "Centre Module (PROMINENT — bold, large, high contrast, readable at arm's length):",
                    f'"{sample_cta}"',
                    "",
                ]
                badge_rendered = True
            elif price_cr and not pricing_above:
                lines += ["Centre Module:", '"STARTING AT"', f'"₹{price_cr} Cr"', ""]
            if usp_parts:
                lines.append("Right Module:")
                for part in usp_parts:
                    if part:
                        lines.append(f'"{part}"')
                lines.append("")

    # Fallback: badge wasn't embedded above but recipe wants it
    if sample_ready and want("badge") and not badge_rendered:
        lines += [
            "PROMINENT BADGE (large pill, gold border, palette-matched backing — placed in available negative space, large enough to be unmissable):",
            f'"{sample_cta}"',
            "",
        ]

    lines.append("NOTE: Render ONLY the text strings listed above. Do not invent or add anything.")
    return "\n".join(lines)


def build_ad_prompt(entry: dict, brief: dict, variant_key: str) -> str:
    """
    Assemble the full structured Ideogram ad prompt from:
      - entry["scene_prose"]  — 60-80 word photography description from the LLM
      - entry["headline"]     — headline from copy output
      - entry["eyebrow"]      — optional eyebrow line
      - entry["palette_tag"]  — selected colour palette (concrete text/accent treatment)
      - entry["tone_tag"]     — dark_luxury or bright_aspirational
      - entry["recipe_tag"]   — chosen design recipe (the learned, coherent design bundle)
      - brief                 — property data (locality, city, price_cr, config, etc.)
      - variant_key           — fallback ad structure when no recipe is chosen

    When a recipe is present it is the primary art-direction authority: its
    lighting / subject / negative-space / typographic move / colour world / structure
    drive composition, and its text_roles gate how much text is rendered. The
    detail-principle library and layout_discipline rules refine the result.
    When absent, the builder falls back to the legacy palette + structure behaviour.
    """
    scene_prose = (entry.get("scene_prose") or "").strip()
    palette_tag = (entry.get("palette_tag") or "navy_gold").strip()
    tone_tag = (entry.get("tone_tag") or "dark_luxury").strip()
    recipe = _RECIPES_BY_NAME.get((entry.get("recipe_tag") or "").strip())

    # Load variant config once — used only for structure fallback now. The design
    # language (palette, recipe, info-band layout) is dynamic, NOT pinned to the
    # variant: the variant fixes only the topic (scene + creative brief). Distinctness
    # across a batch is enforced in output_saver.dedupe_visual_batch().
    _variant_cfg = _VARIANTS_CONFIG.get(variant_key, {})

    palette_config = PALETTE_CONFIGS.get(palette_tag, PALETTE_CONFIGS["navy_gold"])

    # The bottom information layout is PART OF the chosen recipe's design bundle, so it
    # changes whenever the recipe changes. Falls back to strip_three_col when no recipe.
    _info_band_style = (recipe or {}).get("info_band_style")

    # Structure: recipe.structure_family (list, legacy) or recipe.layout_type (string, new)
    # supersedes the variant→tone default.
    recipe_structure = None
    if recipe:
        if recipe.get("structure_family"):
            recipe_structure = recipe["structure_family"]
        elif recipe.get("layout_type"):
            recipe_structure = [recipe["layout_type"]]
    if recipe_structure:
        structure_config = _expand_structures(recipe_structure)
    else:
        structure_name = _select_structure(variant_key, tone_tag)
        structure_config = AD_STRUCTURES.get(structure_name, AD_STRUCTURES["bordered_campaign"])

    typography_block = _build_typography_block(
        entry, brief, _recipe_text_roles(recipe), info_band_style=_info_band_style
    )

    recipe_block = (_format_recipe_block(recipe) + "\n\n") if recipe else ""
    layout_block = ""
    if recipe and recipe.get("text_tier") == "full_detail" and _LAYOUT_DISCIPLINE:
        layout_block = (
            "Layout discipline (keep the loaded ad uncluttered):\n"
            + "\n".join(f"• {r}" for r in _LAYOUT_DISCIPLINE)
            + "\n\n"
        )

    # Typography quality block — describes typeface characteristics without naming banned fonts.
    # The image model defaults to system fonts (Arial, Calibri) when not given specific guidance.
    _TYPEFACE_QUALITY = (
        "Typeface quality (premium luxury advertisement — system fonts are unacceptable):\n"
        "• Location name / primary headline: HEAVY or BLACK weight luxury display serif. "
        "Strokes must be thick and monumental — stroke width at minimum 15% of cap height. "
        "Scale it so each letter is individually legible at arm's length. "
        "If the name wraps across two lines, EACH line must be its own large, heavy typographic event. "
        "TYPOGRAPHIC INTEGRATION: Text must feel like it belongs to the scene — as if it were "
        "part of the image, not digitally pasted over it. The quality comes from how the text "
        "sits against its background: natural contrast from the scene beneath it, colour that "
        "complements the palette and lighting of the space, and weight proportional to the frame. "
        "AVOID: hard bevel edges, reflective metallic sheen, plastic gloss, over-rendered 3D "
        "depth effects, or anything that makes letterforms look like a video game title screen "
        "or a cheap flyer. The text colour follows the palette specified in this prompt — it is "
        "not always gold; match the tone of the scene.\n"
        "CRITICAL: Never use medium, regular, book, or light weight for the location name. It will look weak.\n"
        "• Campaign tagline: italic or oblique of the same display serif, medium-bold — "
        "refined but not thin.\n"
        "• Price: same HEAVY display serif as the location name. Large. Gold. Unmissable.\n"
        "• Apartment config (e.g. '4 & 5 BHK') when featured in the photo zone as a large "
        "typographic element: use the same HEAVY display serif as the location name — NOT "
        "geometric sans. A BHK callout in the photo zone must carry the same visual weight "
        "and typeface family as the surrounding ad typography. Geometric sans for this element "
        "will look like a Word document label dropped into a luxury photograph.\n"
        "• Spec text, eyebrow, labels (footer strip only): Bold or ExtraBold geometric monolinear "
        "sans-serif — perfectly circular O, uniform stroke, zero humanist influence. "
        "Uppercase, generously tracked. Never Regular or Medium weight — footer text must be "
        "optically heavy enough to read clearly from several feet away.\n"
        "• Badge / CTA: same Bold geometric sans, medium-bold, clearly legible at arm's length. "
        "NEVER smaller than spec text.\n"
        "• NUMBER DISAMBIGUATION: If the composition contains '3,300' or '3300', this refers to "
        "apartment size in square feet — NOT the price. The price is Rs 3 Cr. Treat these as "
        "two entirely separate elements in different positions.\n"
        "• NEVER: thin serifs, light weights, rounded soft fonts, or any font from a presentation deck.\n"
        "• EDITORIAL ACCENTS (optional, scene-permitting): Where the palette and composition "
        "genuinely support it, tasteful typographic accents add craft and warmth — for example: "
        "thin gold hairlines flanking a sub-header ('— AHMEDABAD —' style), a very soft "
        "drop shadow on a photo-zone callout to lift it from a busy background, a refined "
        "stroke weight or fine outline on a display serif that complements the scene palette. "
        "Use these as editorial choices, not as decoration for its own sake. "
        "Never apply them uniformly to every element — one or two accents per ad, placed where "
        "they add visual richness without cluttering the composition.\n"
    )

    # composition_notes — scene-specific creative direction written by the visual_prompter.
    # When present:
    #   - replaces the generic structure_config template block
    #   - switches typography block to composition_driven mode (raw strings only, no layout language)
    #   - compacts palette to hex tokens only (placement already covered by composition_notes)
    #   - drops the redundant layout_discipline block
    composition_notes = (entry.get("composition_notes") or "").strip()
    if composition_notes:
        # Rebuild typography block in composition_driven mode
        typography_block = _build_typography_block(
            entry, brief,
            allowed_roles=_recipe_text_roles(recipe),
            info_band_style=_info_band_style,
            composition_driven=True,
        )
        palette_section  = _PALETTE_COMPACT.get(palette_tag, palette_config)
        layout_section   = f"Composition and layout — how to compose this specific scene:\n{composition_notes}"
        recipe_section   = ("Design grammar reference (inform the approach, do not override the composition notes):\n"
                            + _format_recipe_block(recipe) + "\n") if recipe else ""
        return (
            f"{scene_prose}\n\n"
            "Produce a finished luxury real estate advertisement at premium Indian developer quality "
            "(Lodha / Shivalik / Iscon / Swati). Photography is the hero. "
            "Typography is placed by reading the scene — shadow pools, open walls, sky, floor — "
            "not by applying a template. Every placement decision in this prompt is scene-specific.\n\n"
            "Human subjects: tailored suits, slim-fit blazers, silk resort-wear, elegant dresses only. "
            "No traditional Indian clothing.\n\n"
            "CLEAN GLAZING ZONE: If the scene uses floor-to-ceiling glazing or a large window as "
            "the primary light source or typography backdrop, keep the glass-to-sky transition "
            "completely unobstructed. No visible curtain tracks, ceiling-mounted rails, drape "
            "hardware, or pelmet boxes crossing the glazing or sky zone. These create horizontal "
            "bars that interrupt the typography. Sheers or curtains may appear at the very edge "
            "of frame only — never across the glazing face.\n\n"
            "PEOPLE DO NOT DISPLACE TEXT: The presence of human subjects in the scene does NOT "
            "justify reducing, hiding, or removing any required text element. All text listed "
            "below is mandatory — location name, headline, price, and spec row must appear at "
            "full size and weight regardless of how many people are in the frame. Work the "
            "typography around the people using the scene's natural negative space. Never "
            "sacrifice a text element to accommodate a figure.\n\n"
            f"{layout_section}\n\n"
            f"Text strings to render (exact wording, placement per composition notes):\n"
            f"{typography_block}\n\n"
            f"Colour tokens: {palette_section}\n"
            "TEXT COLOUR IS PER-ELEMENT: each text element independently picks the colour "
            "that maximises contrast on its specific backing surface. The headline may be "
            "gold, the CTA badge may be white or cream, the price may be cream, a spec line "
            "may be charcoal on a pale surface — they do NOT all need to match each other. "
            "SPEC VISIBILITY IS CRITICAL: footer/spec text is already small — if the surface "
            "behind the specs is dark, mid-dark, or the same tonal family as gold (dark green, "
            "dark charcoal, dark brown), do NOT use gold for specs. Use cream or white instead. "
            "Gold spec text on a dark surface at small size disappears. Contrast for small "
            "text must be absolute, not approximate. Gold hairlines are the accent thread, not "
            "the mandatory text colour for every element. On a very light surface, deep charcoal "
            "or navy is often more premium than forced gold. Choose colour per element.\n\n"
            f"{_TYPEFACE_QUALITY}\n"
            f"{recipe_section}"
            "COMPOSITIONAL DISTRIBUTION: Supporting elements do not all need to cluster at "
            "one edge. The sample badge has no fixed corner — place it where the scene "
            "creates natural room (mid-frame beside a figure, above a furniture grouping, "
            "against a clear wall). Distributing elements across the frame does NOT mean "
            "shrinking them — a distributed element is still rendered at full, prominent size. "
            "Let the scene geometry decide placement — not a fixed template, and never a "
            "samey skeleton (location-top → BHK-side → headline → tiny-price → footer-strip) "
            "repeated across every variant.\n\n"
            "PRICE & CTA PROMINENCE (non-negotiable): The price and the sample/CTA badge are "
            "the two primary conversion elements — they must read INSTANTLY, at a single "
            "glance, from across a room. 'Prominent' means BOTH large AND high-contrast: the "
            "price is among the two or three largest text events in the whole ad (second only "
            "to the location name), and the CTA badge text is at least as large as the footer "
            "spec labels — never a small lozenge or a grey blob. Contrast is GUARANTEED, not "
            "hoped for: if an element's colour does not clearly pop against the surface behind "
            "it (gold on a mid-brown wall, pale text on pale stone, a dark pill on a bright "
            "ceiling), give it a solid palette pill with a gold hairline, OR a strong dark "
            "halo / soft glow behind it — do NOT leave bare text blending into a similar-toned "
            "surface. Bare text with no backing is allowed ONLY on a genuinely high-contrast "
            "surface (gold on a near-black floor, dark text on white). When in doubt, back it. "
            "CENTRAL-FOCUS CONSTRAINT: both price and CTA sit within the central 70-80% focus "
            "area — never an extreme corner, never against the frame edge.\n\n"
            "SCENE NEGATIVE SPACE (compose room for the text): Do NOT let the furniture and "
            "architecture fill every usable surface so the text elements get crammed into "
            "whatever narrow strip is left — a thin side wall, a corner, a bright ceiling. "
            "Frame and compose the scene so it deliberately holds calm, uncluttered zones, "
            "each sized to carry the BHK, the price and the CTA at full prominent scale, and "
            "SPREAD these elements across the frame rather than stacking two or three of them "
            "down one edge. If the scene's natural surfaces cannot host an element at full "
            "size, reframe wider or open a calm darker zone to make room. Negative space is a "
            "designed asset created on purpose — not the leftover gaps between objects.\n\n"
            "ELEMENT SPACING (intentional negative space): Every text element keeps a clear, "
            "deliberate margin — at least 3-4% of canvas — from every other element and from "
            "the frame edge. No two elements touch, overlap, or collide by accident. If two "
            "would clash, reposition or resize one so the spacing reads as a designed decision, "
            "not a crowding accident.\n\n"
            "Legibility rule: every element must be readable at arm's length. "
            "Read the scene surface before choosing a contrast method. "
            "A bright surface (pale sky, white wall, frosted glass) is an opportunity "
            "for dark-toned text — navy, deep charcoal, forest green placed directly "
            "on the bright field is often more premium than any backing treatment. "
            "When contrast aid is genuinely needed, prefer a very soft shadow or thin "
            "vignette that darkens just enough, or a hairline border. "
            "NEVER a solid rectangular backing strip or per-letter dark panel placed "
            "over a bright surface — this creates a cheap sign-board effect regardless "
            "of the colour used. Never force a dark panel where the palette or "
            "composition notes do not call for one.\n\n"
            "CONFIGURATION TYPE RULE: The apartment configuration (e.g. '4 & 5 BHK') is a "
            "PRIMARY selling point — it must appear at a prominent, clearly readable size. "
            "Never place it as tiny corner text, a small watermark-style label, or smaller "
            "than any other spec element. If it appears in the footer strip, it belongs "
            "at the same visual weight as the other footer items, not shrunken.\n\n"
            "FOOTER GRID GEOMETRY: If the footer uses an icon-grid layout (icon above label, "
            "multiple columns), every element must snap to a strict invisible column grid. "
            "A vertical hairline marks the exact centre between columns. Each column has its "
            "own axis — the icon is horizontally centred on that axis, and the label text is "
            "centred directly beneath it on the same axis. Both columns are identical in width "
            "with equal outer margins and equal distance from the hairline to each icon centre. "
            "NEVER allow icons to drift independently. NEVER use uneven padding between columns. "
            "NEVER vary icon scale between columns. NEVER offset one column lower than the other. "
            "Nothing in the footer floats — every element is optically anchored within the grid.\n\n"
            "MINIMUM SCALE (non-negotiable): Typography must be sized for actual legibility, "
            "not theoretical legibility. Do not scale down to 'fit neatly' — scale up to "
            "'read instantly'. Specific minimums: (1) Location name SINDHUBHAVAN ROAD must "
            "span at least 75% of canvas width — each individual letter must be legible at "
            "arm's length; (2) Campaign headline must be at least 3% of total canvas height — "
            "never a whisper, always a confident statement; (3) City name (AHMEDABAD) is "
            "readable tracked caps — never a faint micro-line; (4) Apartment configuration "
            "(4 & 5 BHK) is a LARGE photo-zone selling point — at least ~50% of the location "
            "name's cap height; (5) Footer text (spec strip labels) must fill the available "
            "column width — Bold or ExtraBold, never condensed or compressed to save space; "
            "(6) Price badge AND sample/CTA badge text must each be PROMINENT and instantly "
            "readable at across-a-table distance — never a small fraction of the layout, never "
            "shrunk to fit; (7) DISTRIBUTED FLOATING SPEC LINES — when specs are not in a strip "
            "or grid but float as individual lines, each line must be rendered at the same optical "
            "weight and cap height as the CTA badge text. 'Slim', 'fine', 'engraved', or 'delicate' "
            "are NEVER valid size descriptors for spec lines — they mean illegible. Bold or "
            "ExtraBold geometric sans, generously tracked, clearly readable at arm's length. "
            "If in doubt, size up — never down.\n\n"
            "No invented text, no logos, no watermarks. One corner kept clean for logo compositing.\n\n"
            + (
                f"PROJECT NAME BAN: The name '{brief.get('property_name', '')}' is an internal "
                f"project identifier — it must NEVER appear anywhere in the rendered image. "
                f"Not in the footer, not in the photo zone, not as a label. The ad shows only "
                f"the locality, city, specs, and campaign copy.\n\n"
                if brief.get("property_name") else ""
            )
            + (
                f"TEXT FIDELITY (non-negotiable — enforced above all styling decisions): "
                f"Every text element must be rendered character-for-character exactly as supplied. "
                f"Do not substitute, omit, duplicate, merge, split, transpose, or hallucinate any letter. "
                + (
                    f"'{(brief.get('locality') or '').upper()}' is a proper place name — render every "
                    f"one of its characters in exact sequence as a single continuous typographic word. "
                    f"Common failure modes to explicitly avoid: inserting a slash, hyphen, full-stop, "
                    f"or decorative rule mid-word; doubling a letter (extra vowel or consonant); "
                    f"dropping a letter; splitting the word across two lines or two typographic zones "
                    f"as if it were two separate words. It is one unbroken word. "
                    if brief.get("locality") else ""
                )
                + "Text correctness has higher priority than artistic styling or decorative effects. "
                "If a ligature, swash, decorative separator, or dimensional treatment conflicts with "
                "accurate character rendering, drop the decoration — never alter the character. "
                "Verify every rendered character against the supplied string before finalising.\n\n"
            )
            + "Aspect ratio 4:5."
        )

    # ── Legacy path: no composition_notes — use full template assembly ────────
    layout_section = f"Layout structure:\n{structure_config}"
    # Typography callouts come EARLY — before layout/recipe doctrine — so the image
    # model encounters the exact text strings before any atmospheric instructions.
    return (
        f"{scene_prose}\n\n"
        "A finished, world-class luxury real estate advertisement. "
        "Photography is the hero — the photograph fills 80–90% of the frame. "
        "Typography integrates with the photograph through the scene's own natural zones — "
        "shadow pools, open walls, bright floor surfaces, sky — never applied as a generic overlay. "
        "The headline or a key typographic element IS the creative device. "
        "Premium Indian developer campaign quality (Lodha / Shivalik / Iscon / Swati).\n\n"
        "Human subjects: premium luxury Western attire only — tailored suits, slim-fit blazers, silk resort-wear, elegant dresses. "
        "NEVER traditional Indian clothing (no kurta, no salwar-kameez, no saree, no dhoti). These are aspirational global luxury ads.\n\n"
        "Render these exact text elements into the design (do not alter the wording):\n"
        f"{typography_block}\n\n"
        f"{layout_section}\n\n"
        "Colour & text treatment:\n"
        f"{palette_config}\n\n"
        f"{_TYPEFACE_QUALITY}\n"
        f"{recipe_block}"
        f"{layout_block}"
        "Text legibility (non-negotiable): every text element must be clearly readable at arm's length. "
        "The badge / CTA must be large and bold — never smaller than the spec text. "
        "Wherever text sits over a busy or low-contrast background, add just enough contrast — "
        "a subtle shadow, a thin backing strip, a soft vignette — using colours from the palette. "
        "Do not force dark panels where the palette calls for a bright or warm feel. "
        "No text should require zooming to read.\n\n"
        "No invented text, no logos, no watermarks. One corner kept clean for logo compositing.\n\n"
        "Aspect ratio 4:5."
    )


# Backward-compatible alias: callers/tests that reference the old name keep working.
build_gpt_image_prompt = build_ad_prompt


# ── Ideogram API calls ────────────────────────────────────────────────────────

# Optional recipe-scoped style reference (quality booster, default OFF). When
# IDEOGRAM_STYLE_REF=1, 1-2 curated exemplar images for the chosen recipe are attached
# to the Ideogram request as native style references. Off by default to keep the
# distilled text grammar — not pixel copying — as the primary mechanism.
_STYLE_REF_DIR = Path(
    os.getenv("IDEOGRAM_STYLE_REF_DIR")
    or (Path(__file__).parent.parent.parent.parent / "project_context" / "reference_ads")
)


def _collect_style_refs(recipe_tag: str) -> list[tuple[str, bytes]]:
    """Return up to 2 (filename, bytes) exemplar images for a recipe, or [] if the
    style-ref flag is off, the recipe has no exemplars, or files are missing."""
    if os.getenv("IDEOGRAM_STYLE_REF", "0") not in ("1", "true", "True"):
        return []
    recipe = _RECIPES_BY_NAME.get((recipe_tag or "").strip())
    if not recipe:
        return []
    names = [n for n in (recipe.get("exemplar_images") or []) if n]
    if not names:
        return []
    import random
    random.shuffle(names)
    out: list[tuple[str, bytes]] = []
    for name in names:
        if len(out) >= 2:
            break
        path = _STYLE_REF_DIR / name
        try:
            if path.exists():
                out.append((name, path.read_bytes()))
        except Exception:
            continue
    return out


def call_ideogram_v3(
    prompt: str, key: str, speed: str = "QUALITY", aspect: str = "4x5"
) -> bytes:
    """Ideogram v3 — multipart/form-data.  Better photorealism for scene-only prompts."""
    import time
    import urllib.error
    import urllib.request

    speed = speed.upper() if speed else "QUALITY"
    if speed not in ("TURBO", "DEFAULT", "QUALITY"):
        speed = "QUALITY"
    _V3_RATIOS = {"1x1", "4x5", "5x4", "16x9", "9x16", "2x3", "3x2", "3x4", "4x3"}
    clean_aspect = (aspect or "4x5").lower().replace(":", "x")
    aspect_code = clean_aspect if clean_aspect in _V3_RATIOS else "4x5"

    boundary = "IdeogramV3Boundary"
    parts = []
    for name, value in [
        ("prompt", prompt), ("aspect_ratio", aspect_code), ("rendering_speed", speed)
    ]:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )
    parts.append(f"--{boundary}--\r\n")
    body = "".join(parts).encode("utf-8")

    req = urllib.request.Request(
        "https://api.ideogram.ai/v1/ideogram-v3/generate",
        data=body,
        headers={
            "Api-Key": key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    data = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            is_rate_limit = e.code == 429 or (e.code == 403 and "1010" in detail)
            if is_rate_limit and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Ideogram v3 request failed [{e.code}]: {detail}") from e

    img_url = data["data"][0]["url"]
    img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(img_req, timeout=60) as img_resp:
        return img_resp.read()


def call_ideogram(
    prompt: str, key: str, speed: str = "QUALITY", aspect: str = "4x5",
    recipe_tag: str = "",
) -> bytes:
    """Ideogram 4.0 API — multipart/form-data payload.

    recipe_tag — when the IDEOGRAM_STYLE_REF flag is on, 1-2 curated exemplar images
    for this recipe are attached as native style references (quality booster). Off by
    default; missing files or flag-off degrade silently to a text-only request.
    """
    import time
    import urllib.error
    import urllib.request

    speed = speed.upper() if speed else "QUALITY"
    _RESOLUTION_MAP = {
        "1x1": "2048x2048",
        "4x5": "1792x2240",
        "16x9": "2560x1440",
        "9x16": "1440x2560",
        "2x3": "1664x2496",
        "3x2": "2496x1664",
    }
    clean_aspect = aspect.lower().replace(":", "x") if aspect else "4x5"
    resolution = _RESOLUTION_MAP.get(clean_aspect, "1792x2240")

    # v4 API requires multipart/form-data, not application/json
    boundary = "----PikoruaBoundary7Ma4YWxkTrZu0gW"
    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    def _file_field(name: str, filename: str, content: bytes) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content + b"\r\n"

    body = (
        _field("text_prompt", prompt)
        + _field("resolution", resolution)
        + _field("rendering_speed", speed)
    )
    for fname, content in _collect_style_refs(recipe_tag):
        body += _file_field("style_reference_images", fname, content)
    body += f"--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        "https://api.ideogram.ai/v1/ideogram-v4/generate",
        data=body,
        headers={
            "Api-Key": key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    data = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            is_rate_limit = e.code == 429 or (e.code == 403 and "1010" in detail)
            if is_rate_limit and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Ideogram image request failed [{e.code}]: {detail}") from e

    img_url = data["data"][0]["url"]
    img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(img_req, timeout=60) as img_resp:
            return img_resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(
            f"Ideogram image download failed [{e.code}]: {detail}"
        ) from e


def call_ideogram_inpaint(
    image_bytes: bytes,
    mask_bytes: bytes,
    prompt: str,
    key: str,
    speed: str = "QUALITY",
    aspect: str = "4x5",
) -> bytes:
    """Ideogram v4 inpainting — edit a masked region of an existing image.

    mask_bytes: PNG where white = regenerate, black = keep unchanged.
    Returns the full edited image as PNG bytes.
    """
    import json as _json
    import time
    import urllib.error
    import urllib.request

    speed = speed.upper() if speed else "QUALITY"
    _RESOLUTION_MAP = {
        "1x1": "2048x2048",
        "4x5": "1792x2240",
        "16x9": "2560x1440",
        "9x16": "1440x2560",
        "2x3": "1664x2496",
        "3x2": "2496x1664",
    }
    clean_aspect = aspect.lower().replace(":", "x") if aspect else "4x5"
    resolution = _RESOLUTION_MAP.get(clean_aspect, "1792x2240")

    boundary = "----PikoruaInpaintBoundary9Xk2mRwQ"

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    def _file_field(name: str, filename: str, content: bytes, mime: str = "image/png") -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + content + b"\r\n"

    body = (
        _file_field("image", "image.png", image_bytes)
        + _file_field("mask", "mask.png", mask_bytes)
        + _field("prompt", prompt)
        + _field("resolution", resolution)
        + _field("rendering_speed", speed)
        + f"--{boundary}--\r\n".encode("utf-8")
    )

    req = urllib.request.Request(
        "https://api.ideogram.ai/v1/ideogram-v4/edit",
        data=body,
        headers={
            "Api-Key": key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    data = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            is_rate_limit = e.code == 429 or (e.code == 403 and "1010" in detail)
            if is_rate_limit and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"Ideogram inpaint request failed [{e.code}]: {detail}"
            ) from e

    img_url = data["data"][0]["url"]
    img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(img_req, timeout=60) as img_resp:
            return img_resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(
            f"Ideogram inpaint download failed [{e.code}]: {detail}"
        ) from e


def call_ideogram_remix(
    image_bytes: bytes,
    prompt: str,
    key: str,
    speed: str = "DEFAULT",
    aspect: str = "4x5",
    image_weight: float = 0.5,
) -> bytes:
    """Ideogram v2 remix — generate a variant of a reference image guided by a text prompt.

    image_weight: 0.0 (ignore reference, follow prompt) → 1.0 (preserve reference maximally).
    Returns PNG bytes of the generated image.
    """
    import json as _json
    import time
    import urllib.error
    import urllib.request

    speed = speed.upper() if speed else "DEFAULT"
    _ASPECT_MAP = {
        "1x1": "ASPECT_1_1",
        "4x5": "ASPECT_4_5",
        "16x9": "ASPECT_16_9",
        "9x16": "ASPECT_9_16",
        "2x3": "ASPECT_2_3",
        "3x2": "ASPECT_3_2",
    }
    clean_aspect = aspect.lower().replace(":", "x") if aspect else "4x5"
    aspect_ratio = _ASPECT_MAP.get(clean_aspect, "ASPECT_4_5")
    # Ideogram remix uses integer image_weight 1-100
    weight_int = max(1, min(100, int(round(image_weight * 100))))

    image_request_json = _json.dumps({
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "rendering_speed": speed,
        "image_weight": weight_int,
        "model": "V_2",
    })

    boundary = "----PikoruaRemixBoundary3Qn7vRxK"

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    def _file_field(name: str, filename: str, content: bytes, mime: str = "image/png") -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + content + b"\r\n"

    body = (
        _file_field("image_file", "reference.png", image_bytes)
        + _field("image_request", image_request_json)
        + f"--{boundary}--\r\n".encode("utf-8")
    )

    req = urllib.request.Request(
        "https://api.ideogram.ai/v2/remix",
        data=body,
        headers={
            "Api-Key": key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    data = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            is_rate_limit = e.code == 429 or (e.code == 403 and "1010" in detail)
            if is_rate_limit and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"Ideogram remix request failed [{e.code}]: {detail}"
            ) from e

    img_url = data["data"][0]["url"]
    img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(img_req, timeout=60) as img_resp:
            return img_resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(
            f"Ideogram remix image download failed [{e.code}]: {detail}"
        ) from e


def assemble_reference_variant_prompt(brief: dict, headline: str = "") -> str:
    """Build an Ideogram remix prompt from campaign brief fields — no LLM required.

    Preserves the reference image's compositional structure and visual style while
    explicitly instructing Ideogram on the exact text strings to render from the brief.
    Without explicit text strings, Ideogram either renders no text or hallucinates text
    from the original reference image.
    """
    locality = (brief.get("locality", "") or brief.get("city", "")).upper()
    city = (brief.get("city", "") or "").upper()
    prop_type = brief.get("property_type", "")

    # Scene framing — what kind of image to produce
    scene_parts = [
        "Luxury real estate advertisement for an Indian premium residential development.",
        f"{prop_type}." if prop_type else "",
        "Preserve the photographic composition, lighting mood, and structural layout of the "
        "reference image exactly.",
        "Adapt the colour palette only where needed to complement the scene.",
        "Replace every piece of text in the reference with the exact strings listed below — "
        "no other text, no invented words, no retained text from the reference.",
    ]
    scene = " ".join(p for p in scene_parts if p)

    # Typography block — explicit text strings Ideogram must render
    # Re-use the composition_driven path of _build_typography_block which emits
    # the same flat list that build_ad_prompt uses when composition_notes are present.
    typo_entry = {"headline": headline, "eyebrow": ""}
    typo_block = _build_typography_block(typo_entry, brief, composition_driven=True)

    raw = scene + "\n\n" + typo_block + "\n\n" + _ANTI_LOGO_GUARD
    # Skip sanitizer stages that strip "assembled" prompts — the text block is already
    # drawn from brief fields and needs no further claims-stripping.
    return sanitize_image_prompt(raw, brief, assembled=True)


def image_backend(
    i: int, ideogram_key: str, replicate_token: str, together_key: str
) -> tuple[str, str]:
    """Return (backend_name, tier) for prompt index i (1-based), or ('', '')."""
    if i > 3 and replicate_token:
        return "replicate", "paid"
    if ideogram_key:
        return "ideogram", "paid"
    if together_key:
        return "together", "free"
    return "", ""


# ── Logo compositing ──────────────────────────────────────────────────────────

def composite_logo(
    image_path: Path,
    logo_path: Path,
    corner: str = "bottom-right",
) -> None:
    """
    Place the brand logo inside the safe-zone the visual_prompter was instructed
    to leave empty.  Adds a soft rounded scrim behind the logo for legibility on
    busy backgrounds.

    corner: "bottom-right" | "bottom-left" | "top-right" | "top-left"
            Must match the corner the prompt actually reserved (from logo_corner
            in the model's structured output).
    """
    from PIL import Image as _PIL, ImageDraw

    base = _PIL.open(image_path).convert("RGBA")
    logo = _PIL.open(logo_path).convert("RGBA")

    bw, bh = base.size
    logo_width_ratio = 0.16
    margin_ratio = 0.04

    target_w = int(bw * logo_width_ratio)
    scale = target_w / logo.width
    logo = logo.resize((target_w, max(1, int(logo.height * scale))), _PIL.LANCZOS)
    lw, lh = logo.size

    margin_x = int(bw * margin_ratio)
    margin_y = int(bh * margin_ratio)

    positions = {
        "bottom-right": (bw - lw - margin_x, bh - lh - margin_y),
        "bottom-left": (margin_x, bh - lh - margin_y),
        "top-right": (bw - lw - margin_x, margin_y),
        "top-left": (margin_x, margin_y),
    }
    x, y = positions.get(corner, positions["bottom-right"])

    pad = max(int(lw * 0.15), 8)
    scrim = _PIL.new("RGBA", (lw + pad * 2, lh + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scrim)
    draw.rounded_rectangle(
        [0, 0, scrim.width, scrim.height],
        radius=int(pad * 1.5),
        fill=(0, 0, 0, 90),
    )
    base.alpha_composite(scrim, (x - pad, y - pad))
    base.alpha_composite(logo, (x, y))
    base.convert("RGB").save(image_path, format="PNG")


# ── Logo / favicon image helpers ─────────────────────────────────────────────

_logo_cache: dict[str, bytes] = {}


def trimmed_png(path: Path, pad: int = 60) -> bytes:
    """PNG bytes with whitespace/transparency trimmed and small padding re-added."""
    key = str(path)
    if key in _logo_cache:
        return _logo_cache[key]
    from PIL import Image
    img = Image.open(path).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        l, t, r, b = bbox
        l = max(0, l - pad)
        t = max(0, t - pad)
        r = min(img.width, r + pad)
        b = min(img.height, b + pad)
        img = img.crop((l, t, r, b))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    _logo_cache[key] = data
    return data


def square_favicon(path: Path) -> bytes:
    """Square PNG favicon, content centred on a transparent background."""
    key = f"__favicon__{path}"
    if key in _logo_cache:
        return _logo_cache[key]
    from PIL import Image
    img = Image.open(path).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    side = max(img.width, img.height)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    buf = io.BytesIO()
    square.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    _logo_cache[key] = data
    return data
