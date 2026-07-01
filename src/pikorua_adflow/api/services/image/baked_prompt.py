"""
Stage 6b — BakedPrompt: a single clean Ideogram prompt with text baked in (BAKED mode).

Used when IMAGE_MODE=baked (env or per-variant override). One coherent prompt, no
conflicting layers and NO hardcoded layout string: the LLM-authored composition (the ad
design) drives placement, backed by the chosen skeleton's design notes and the universal
ad-craft principles. We supply scene_prose verbatim + the exact brief text strings +
palette colours (as data). The full assembled prompt is sanitized before sending (§11).
"""

from __future__ import annotations

from . import sanitizer
from . import libraries as lib
from .art_director import AdSpec
from .brief_model import BriefModel


def _palette_line(palette: dict) -> str:
    return (
        f"Colour palette (use these and only these) — locality {palette.get('locality_color')}, "
        f"headline/body {palette.get('headline_color')}, eyebrow {palette.get('eyebrow_color')}, "
        f"price text {palette.get('price_text')} on {palette.get('price_bg')} with a "
        f"{palette.get('price_border')} border, CTA {palette.get('cta_text')} on {palette.get('cta_bg')}."
    )


_LOGO_ZONE_DESCRIPTIONS = {
    "top_left":     "top-left corner, inset 20px from the inner border edge",
    "top_right":    "top-right corner, inset 20px from the inner border edge",
    "bottom_left":  "bottom-left corner of the footer band, inset 16px from the bottom edge",
    "bottom_right": "bottom-right corner, inset 16px from the frame edge",
}

# Skeleton → natural logo zone (corner that least conflicts with the main text stack)
_SKELETON_LOGO_ZONE: dict[str, str] = {
    "editorial_rail":       "top_right",   # photo side, away from the text rail
    "dark_triptych":        "top_right",   # header band corner away from locality centre
    "photo_first_floating": "bottom_right",
    "framing_device":       "top_right",
    "lower_text_panel":     "top_right",   # photo zone corner
    "full_bleed_vignette":  "top_right",
    "corner_anchor_pyramid":"bottom_right",
    "split_canvas":         "top_right",
}


def _logo_zone_instruction(brief: BriefModel, skeleton: str) -> str:
    """Return a prompt clause asking Ideogram to leave a clear logo zone, or empty string."""
    if not brief.has_logo:
        return ""
    zone_key = brief.logo_zone or _SKELETON_LOGO_ZONE.get(skeleton, "top_right")
    zone_desc = _LOGO_ZONE_DESCRIPTIONS.get(zone_key, "top-right corner")
    return (
        f"\nLOGO ZONE: Leave a clear unprinted rectangular area approximately 130×40px "
        f"at the {zone_desc} — a quiet margin that will receive the brand mark composited "
        f"after generation. Do NOT print any text, ornament, or photo detail in this zone. "
        f"It should read as a natural calm corner, not a white box.\n"
    )


def _text_strings(brief: BriefModel) -> str:
    lines = [
        f'Locality name — THE DOMINANT ELEMENT: "{brief.locality_display}". '
        f'This must be physically the largest text on the entire ad — fill most of the '
        f'text zone width. If it spans two lines, split ONLY at a natural word boundary '
        f'(e.g. NEHRU / NAGAR); each line fills the same full width at maximum scale. '
        f'NEVER hyphenate mid-word. If the name fits on one line it still fills the full '
        f'zone width at a scale that dominates everything else below it.'
    ]
    if brief.city_display:
        lines.append(
            f'City, beneath the locality: "{brief.city_display}" — tracked small-caps, '
            f'roughly 40–50% of the locality cap-height. Must be clearly legible and '
            f'dignified — NOT a micro-label. Never shrink it below comfortable reading '
            f'size just to create contrast with the locality.'
        )
    if brief.config_display:
        lines.append(
            f'Configuration tag — THE SECOND-MOST-PROMINENT TEXT ON THE AD, directly below '
            f'the locality. ABSOLUTE SIZE FLOOR: must read effortlessly at arm\'s length — '
            f'a BOLD/BLACK-weight display serif or bold tracked sans, clearly HEAVIER '
            f'in stroke weight than the city name. Roughly co-equal with the price numeral '
            f'in visual weight and clearly BIGGER than the tagline. Strongly prefer '
            f'enclosing it in a thin gold-bordered pill/tag; if in a pill, the text inside '
            f'must be BOLD weight — never regular/light inside the pill. If any sizing '
            f'rule would make this thin or small, ignore it and make it bigger — err on '
            f'the side of TOO large. Never a caption: "{brief.config_display}"'
        )
    # ONE tagline only — never headline AND eyebrow competing as two messages.
    tagline = brief.headline or brief.eyebrow
    if tagline:
        lines.append(
            f'Tagline — italic serif, placed in the main text zone (NOT in the spec '
            f'strip, NOT in the footer band, NO icon above it). '
            f'RENDER EXACTLY THIS TEXT AND NO OTHER: "{tagline}". '
            f'No additions, no extensions, no second clause invented. '
            f'SIZE RULE: if the tagline fits comfortably on ONE LINE in the available '
            f'width, render it as a SINGLE LINE at a larger, bolder size — this is '
            f'always preferred over splitting into two smaller lines. Only split at a '
            f'natural break (period or dash) when the text is genuinely too long for '
            f'one line, or when a two-line split creates a clearly stronger visual '
            f'rhythm. When in doubt, keep it one line and make it bigger. '
            f'If split, style as: first clause cream/ivory roman, second clause gold '
            f'italic — but only split if the single-line version would be cramped. '
            f'It is a caption, not a spec item.'
        )
    if brief.price_display:
        lines.append(
            f'Price — CONVERSION ANCHOR, second scroll-stop element: "{brief.price_display}". '
            f'The numeral (e.g. "3") must be readable from across a room — the single '
            f'largest character in the lower half of the ad. Size the bordered container '
            f'around a LARGE numeral, never the reverse. The ₹ symbol and "Cr"/"onwards" '
            f'labels are 40–50% of the numeral cap-height. If the numeral looks small '
            f'inside its container, the container is too big — shrink the container or '
            f'enlarge the numeral until it dominates the conversion zone.'
        )
    if brief.cta_text:
        lines.append(f'CTA badge (solid filled, high contrast, grouped with the price): "{brief.cta_text}"')
    footer = brief.footer_items()
    if footer:
        def _icon_hint(label: str) -> str:
            l = label.lower()
            if any(k in l for k in ("sq ft", "sqft", "area", "size")):
                return "ruler/area icon"
            if any(k in l for k in ("bhk", "bed", "apartment", "villa", "flat")):
                return "floor-plan outline icon"
            if any(k in l for k in ("prelaunch", "launch", "entry", "pre-launch")):
                return "ribbon/key icon"
            if any(k in l for k in ("cheque", "payment", "bank")):
                return "bank/cheque icon"
            if any(k in l for k in ("club", "amenity", "amenities", "pool")):
                return "star/diamond icon"
            if any(k in l for k in ("possession", "ready", "handover")):
                return "key/calendar icon"
            return "thin geometric icon"
        annotated = [f'"{f.upper()}" (above it: a single thin-line {_icon_hint(f)})' for f in footer]
        n = len(footer)
        lines.append(
            f"Spec strip — EXACTLY {n} column{'s' if n != 1 else ''}, no more, no fewer. "
            f"Render ONLY these {n} item{'s' if n != 1 else ''} and absolutely nothing else — "
            f"do not invent, add, or repeat any other text in the spec strip. "
            "Each item: BOLD UPPERCASE geometric sans, WIDE LETTER-SPACING (generous "
            "tracking, never cramped — luxury print spacing so every character breathes), "
            "clearly readable at arm's length. "
            "CONTRAST: if a spec label sits over a light photo zone or light background, "
            "use DARK text (deep charcoal or navy) — never light text on a light surface. "
            "If over a dark zone, use light cream or gold text. "
            "Above each label draw ONE matching thin-line icon as described "
            "(no random icons, no text inside icons): " + ", ".join(annotated)
        )
    return "\n".join(lines)


def _integrated_letterform_word(headline: str) -> str:
    """Pick a short (≤7 char) concept word for the monumental letterform from the headline."""
    candidates = [w.strip(".,!?'\"").upper() for w in headline.split() if w.strip(".,!?'\"").isalpha()]
    # prefer short aspirational words, avoid stop words
    stop = {"A","AN","THE","AND","BUT","OR","NOT","IS","IN","OF","TO","IT","AT","NO","BE","BY","DO"}
    for w in candidates:
        if w not in stop and 4 <= len(w) <= 7:
            return w
    # fallback: longest word under 8 chars
    eligible = [w for w in candidates if w not in stop and len(w) <= 7]
    return max(eligible, key=len) if eligible else (candidates[0] if candidates else "RARE")


def _composition_block(spec: AdSpec, brief: BriefModel | None = None) -> str:
    """The ad design: LLM composition prose; skeleton design notes as fallback."""
    if spec.composition:
        return spec.composition.strip()
    design = (lib.get_skeleton(spec.skeleton).get("design") or "").strip()
    return design or "Design a clean, structured luxury property advertisement."


def build(spec: AdSpec, brief: BriefModel) -> str:
    """Assemble the single BAKED-mode Ideogram prompt.

    Only the LLM-authored fields (scene_prose, composition) are sanitized; the
    Python-authored instruction lines are assembled verbatim so that guard phrases
    (e.g. 'STRICT RULE — NO COMPANY NAME: … PIKORUA …') are never mangled.
    """
    palette = lib.get_palette(spec.palette_id)
    grammar = lib.design_grammar()
    brand = (grammar.get("brand_essence") or "").strip()
    sd = grammar.get("scene_direction") or {}
    scene_dir = " ".join(
        str(sd.get(k, "")).strip() for k in ("lighting", "styling", "mood")
    ).strip()
    craft = " ".join(lib.ad_craft())
    typography = " ".join(lib.typography_rules())
    logo_clause = _logo_zone_instruction(brief, spec.skeleton)
    text_strings = _text_strings(brief)
    brief_dict = brief.sanitizer_brief()

    # Sanitize only the LLM-generated prose fields — not the surrounding instructions.
    clean_scene = sanitizer.sanitize_llm_field(spec.scene_prose.strip(), brief_dict)
    clean_comp = sanitizer.sanitize_llm_field(_composition_block(spec, brief), brief_dict)

    prompt = (
        "A finished, professionally designed luxury real-estate advertisement (4:5 poster). "
        "It must instantly read as an ADVERTISEMENT — designed type, hierarchy and ad "
        "furniture — not a bare photograph with a caption.\n\n"
        f"BRAND FEELING: {brand}\n\n"
        "STRICT RULE — NO COMPANY NAME: Do NOT render the words 'PIKORUA', 'Pikorua', or "
        "any company / advisory name as visible text anywhere in the ad.\n\n"
        f"{logo_clause}"
        f"PHOTOGRAPH (luxury scene — {scene_dir}):\n{clean_scene}\n\n"
        f"AD COMPOSITION:\n{clean_comp}\n\n"
        f"TYPOGRAPHY: {typography}\n\n"
        f"DESIGN CRAFT: {craft}\n\n"
        "TEXT STRINGS — render ALL of the following EXACTLY ONCE, exact spelling, "
        "no substitutions, no invented alternatives. Each string appears ONCE total "
        "in the finished ad — never duplicate any element. The spec strip items below "
        "are the ONLY footer/strip labels; ignore any label text in the composition:\n"
        f"{text_strings}\n\n"
        f"{_palette_line(palette)}\n"
        "PER-ELEMENT TEXT CONTRAST (non-negotiable): each text element must contrast "
        "with whatever surface is directly behind it. Light text (cream, ivory, warm "
        "white) belongs on dark zones; dark text belongs on light or frosted surfaces. "
        "The palette colours above are targets, but if applying a palette colour would "
        "create a tonal match with its local background, shift it to high contrast — "
        "legibility always overrides palette fidelity. CTA badge: the fill and its text "
        "must always be maximum-contrast pairs (gold fill → dark text; dark fill → cream "
        "or gold text). Price numeral: always maximum contrast against its container "
        "background. Never place any text in a near-match with its immediate background "
        "at any size or weight.\n"
        "FINAL RENDERING PRIORITY — MOBILE SCROLL TEST: this ad is viewed for 2 seconds "
        "while scrolling. THREE elements must be so large and high-contrast that they are "
        "readable at a glance before anything else: (1) the locality name — the single "
        "largest text on the ad, no exception; (2) the price numeral — largest text in "
        "the lower half, clearly visible in its bordered container; (3) the CTA badge — "
        "solid filled, grouped directly with the price, impossible to miss. Every other "
        "element (tagline, city name, 'onwards' label, spec strip items) is secondary — "
        "sized for the viewer who pauses, not the one scrolling past. If the locality, "
        "price, or CTA cannot be read in under 2 seconds the image has failed. "
        "Photography is rich, text is confident and designed. Aspect ratio 4:5."
    )
    return prompt + sanitizer._ANTI_LOGO_GUARD
