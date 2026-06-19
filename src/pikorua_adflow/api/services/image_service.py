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


def _build_typography_block(entry: dict, brief: dict) -> str:
    """
    Build the Typography hierarchy section from property brief data.
    Each text line is on its own quoted line so gpt-image-1 stacks them as
    distinct typographic layers within each module (not a single run-on string).
    All values come from brief — nothing invented.
    """
    locality = (brief.get("locality") or brief.get("city") or "").upper()
    city = (brief.get("city") or "").upper()
    price_cr = str(brief.get("price_cr") or "").strip()
    config_val = str(brief.get("config") or brief.get("configuration") or "").strip()
    sample_ready = bool(brief.get("sample_ready"))
    headline = (entry.get("headline") or "").strip()
    eyebrow = (entry.get("eyebrow") or "").strip()

    # USP for right module — check common field names
    usps = (
        brief.get("usps")
        or brief.get("key_selling_points")
        or brief.get("key_usps")
        or []
    )
    if isinstance(usps, str):
        usps = [usps]
    usp = usps[0].strip() if usps else ""

    lines = ["Typography hierarchy:", ""]

    if eyebrow:
        lines += ["Top Eyebrow:", f'"{eyebrow}"', ""]

    lines += ["Primary Headline:", f'"{locality}"', ""]

    if city and city != locality:
        lines += ["City:", f'"{city}"', ""]

    if headline:
        lines += ["Lifestyle Headline:", f'"{headline}"', ""]

    if price_cr:
        lines += ["Pricing Module:", f'"₹{price_cr} Cr"', '"ONWARDS"', ""]

    # Bottom information band — only if we have at least config or price
    has_band = bool(config_val or price_cr or usp)
    if has_band:
        lines.append("Bottom Information Band:")
        lines.append("")
        if config_val:
            # Split "3 & 4 BHK" into two stacked lines: "3 & 4" / "BHK RESIDENCES"
            # rsplit from right so last word (BHK, VILLA, etc.) is the type line
            parts = config_val.rsplit(" ", maxsplit=1)
            top_part = parts[0]                      # e.g. "3 & 4"
            bottom_part = parts[1] if len(parts) > 1 else ""  # e.g. "BHK"
            lines.append("Left Module:")
            lines.append(f'"{top_part}"')
            if bottom_part:
                lines.append(f'"{bottom_part} RESIDENCES"')
            lines.append("")

        if price_cr:
            lines += [
                "Centre Module:",
                '"STARTING AT"',
                f'"₹{price_cr} Cr"',
                "",
            ]

        if usp:
            # Split on "/" if present, else use as single line
            usp_parts = [p.strip() for p in usp.split("/", 1)] if "/" in usp else [usp]
            lines.append("Right Module:")
            for part in usp_parts:
                lines.append(f'"{part}"')
            lines.append("")

    if sample_ready:
        lines += [
            "Centre Floating Badge:",
            '"SAMPLE APARTMENT"',
            '"READY"',
            '"VISIT TODAY"',
            "",
        ]

    lines.append(
        "NOTE: Render ONLY the text elements listed above — do not invent any additional "
        "text, numbers, phone numbers, sq ft, URLs, or property details."
    )
    return "\n".join(lines)


def build_gpt_image_prompt(entry: dict, brief: dict, variant_key: str) -> str:
    """
    Assemble the full structured gpt-image-1 prompt from:
      - entry["scene_prose"]  — 60-80 word photography description from the LLM
      - entry["headline"]     — headline from copy output
      - entry["eyebrow"]      — optional eyebrow line
      - entry["palette_tag"]  — selected colour palette
      - entry["tone_tag"]     — dark_luxury or bright_aspirational
      - brief                 — property data (locality, city, price_cr, config, etc.)
      - variant_key           — determines the ad structure

    This replaces the old approach of asking the LLM to write the full prompt.
    """
    scene_prose = (entry.get("scene_prose") or "").strip()
    palette_tag = (entry.get("palette_tag") or "navy_gold").strip()
    tone_tag = (entry.get("tone_tag") or "dark_luxury").strip()

    palette_config = PALETTE_CONFIGS.get(palette_tag, PALETTE_CONFIGS["navy_gold"])
    structure_name = _select_structure(variant_key, tone_tag)
    structure_config = AD_STRUCTURES.get(structure_name, AD_STRUCTURES["bordered_campaign"])
    typography_block = _build_typography_block(entry, brief)

    return (
        f"{scene_prose}\n\n"
        "Treat this as a finished luxury real estate advertisement, not merely a photograph.\n"
        "FIRST create a world-class architectural photograph.\n"
        "THEN transform it into a premium real estate marketing campaign creative.\n\n"
        "Design language:\n"
        "Ultra-premium developer advertisement, luxury property brochure aesthetic.\n"
        "High-end Indian real estate campaign — Lodha / Shivalik / Iscon / Swati style.\n"
        "Premium property Instagram/Facebook ad. Professional sales creative.\n\n"
        "Layout structure:\n"
        f"{structure_config}\n\n"
        f"{typography_block}\n\n"
        "Colour palette:\n"
        f"{palette_config}\n\n"
        "Design treatment:\n"
        "• Premium serif typography throughout — bold or medium weight, never ultra-light\n"
        "• Rich serif typography hierarchy — headline weight anchors the composition, "
        "body weight carries the detail\n"
        "• Real estate brochure quality — print-ready, not digital-casual\n"
        "• High-end luxury developer advertisement — Lodha / Shivalik / Swati campaign standard\n"
        "• Structured spacing — clear grid alignment, marketing-agency level composition\n"
        "• Every text element perfectly legible at mobile thumbnail size within 2 seconds\n"
        "• Every module in the bottom strip distinct and readable at mobile size\n"
        "• Information modules feel designed by an agency, not automatically placed\n"
        "• No random decorative clutter — every graphic element serves the hierarchy\n"
        "• Professional sales campaign aesthetic throughout\n"
        "• No logos. No watermarks. No random icons or decorative symbols.\n"
        "• No invented text — render ONLY the text strings listed in Typography hierarchy above.\n\n"
        "Aspect ratio 4:5."
    )


# ── OpenAI gpt-image-1 ────────────────────────────────────────────────────────

def call_gpt_image_1(
    prompt: str, api_key: str, aspect: str = "4x5", quality: str = "high"
) -> bytes:
    """OpenAI gpt-image-1 — best text rendering for ad banners.

    Returns raw PNG bytes.  Size is chosen to match the requested aspect ratio;
    gpt-image-1 only supports square, landscape, and portrait sizes.
    """
    import base64
    import urllib.error
    import urllib.request

    _SIZE_MAP = {
        "1x1": "1024x1024",
        "4x5": "1024x1536",
        "5x4": "1536x1024",
        "16x9": "1536x1024",
        "9x16": "1024x1536",
        "2x3": "1024x1536",
        "3x2": "1536x1024",
    }
    clean = (aspect or "4x5").lower().replace(":", "x")
    size = _SIZE_MAP.get(clean, "1024x1536")

    payload = json.dumps({
        "model": "gpt-image-1",
        "prompt": prompt,
        "size": size,
        "n": 1,
        "quality": quality if quality in ("low", "medium", "high") else "high",
        "output_format": "png",
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(
            f"gpt-image-1 request failed [{e.code}]: {detail}"
        ) from e

    b64 = data["data"][0].get("b64_json", "")
    if not b64:
        raise RuntimeError("gpt-image-1 returned no image data")
    return base64.b64decode(b64)


# ── Ideogram API calls ────────────────────────────────────────────────────────

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
    prompt: str, key: str, speed: str = "QUALITY", aspect: str = "4x5"
) -> bytes:
    """Ideogram 4.0 API — multipart/form-data payload."""
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

    body = (
        _field("text_prompt", prompt)
        + _field("resolution", resolution)
        + _field("rendering_speed", speed)
        + f"--{boundary}--\r\n".encode("utf-8")
    )
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
