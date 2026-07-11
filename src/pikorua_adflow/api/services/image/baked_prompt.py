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


def _is_locality_echo(text: str, brief: BriefModel) -> bool:
    """True if `text` adds no words beyond the locality/city already baked in large
    text elsewhere on the ad (e.g. headline "Science Park, Ahmedabad." when locality
    is "Science Park" and city is "Ahmedabad") — rendering it again as a tagline
    would duplicate the same words in one image."""
    import re as _re
    words = {w for w in _re.findall(r"[a-z]+", text.lower()) if w}
    if not words:
        return False
    place_words = {w for w in _re.findall(r"[a-z]+", f"{brief.locality} {brief.city}".lower()) if w}
    return words.issubset(place_words)


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
    # Each string carries only its identity + one compact role line. All sizing and
    # hierarchy detail lives ONCE in _design_rules(); all placement/creative detail
    # lives in the AD COMPOSITION — never duplicated here, so the composition keeps
    # its prompt budget and no two sections can contradict each other.
    lines = [
        f'Locality name — THE DOMINANT ELEMENT, the single largest text on the ad, '
        f'filling the text zone width: "{brief.locality_display}". If it needs two '
        f'lines, split only at a natural word boundary (e.g. NEHRU / NAGAR), each line '
        f'at the same full width; never hyphenate mid-word.'
    ]
    if brief.city_display:
        lines.append(
            f'City, beneath the locality: "{brief.city_display}" — tracked small-caps '
            f'at 40–50% of the locality cap-height; clearly legible, never a micro-label.'
        )
    if brief.config_display:
        lines.append(
            f'Configuration tag — the second-most-prominent text, directly below the '
            f'city: "{brief.config_display}". BOLD/BLACK weight, co-equal with the '
            f'price in visual weight and clearly bigger than the tagline; prefer a thin '
            f'gold-bordered pill with BOLD text inside. Err on the side of too large.'
        )
    # ONE tagline only — never headline AND eyebrow competing as two messages.
    tagline = brief.headline or brief.eyebrow
    if tagline and _is_locality_echo(tagline, brief):
        # The headline is just the locality/city restated (e.g. "Science Park,
        # Ahmedabad.") — that same text is already the single largest element on
        # the ad via the locality/city block above. Rendering it again as the
        # tagline prints the same words twice in one image. Fall back to the
        # eyebrow if it says something different; otherwise drop the tagline.
        tagline = brief.eyebrow if brief.eyebrow and not _is_locality_echo(brief.eyebrow, brief) else ""
    if tagline:
        lines.append(
            f'Tagline — RENDER EXACTLY THIS TEXT AND NO OTHER: "{tagline}". No '
            f'additions, no invented clauses. It sits in the main text zone (never in '
            f'the spec strip or footer band, no icon above it). The AD COMPOSITION '
            f'section defines its creative type treatment (scale-cut, weight/colour '
            f'contrast, or line-break drama) — follow that treatment exactly; if the '
            f'composition gives none, DEFAULT to weight/colour contrast: the first '
            f'clause in cream roman, the strongest word or closing clause in heavier '
            f'gold-accent weight — never a single flat weight/colour italic line, '
            f'which is a typographic failure. Whatever backing it sits on (frosted '
            f'pill, scrim, dark panel), its text colour is chosen to contrast THAT '
            f'backing\'s actual tone, not assumed cream or white — a light frosted '
            f'pill needs dark espresso text, a dark scrim or panel needs cream/gold '
            f'text.'
        )
    if brief.price_display:
        # Pick a price qualifier — varies across properties for natural variety.
        # "onwards" reads naturally trailing the number, so it sits BELOW/AFTER the
        # numeral; "starting from"/"starting at" read as a lead-in phrase, so they sit
        # ABOVE/BEFORE it instead — never squeezed after the numeral like "onwards".
        _QUALIFIERS = ["onwards", "starting from", "starting at"]
        qualifier = _QUALIFIERS[hash(brief.locality) % len(_QUALIFIERS)]
        qualifier_line = (
            f'the qualifier "{qualifier}" beneath the numeral at ~30% cap-height in '
            f'small tracked caps'
            if qualifier == "onwards" else
            f'the qualifier "{qualifier}" ABOVE the numeral (a small lead-in line, '
            f'~30% cap-height, tracked caps) — never after or below it'
        )
        lines.append(
            f'Price — CONVERSION ANCHOR: "{brief.price_display}". The numeral is the '
            f'single largest character in the lower half of the ad, readable from '
            f'across a room; "₹" and "Cr" at 40–50% of its cap-height; {qualifier_line}. '
            f'Size the bordered container around a LARGE numeral, never the reverse.'
        )
    if brief.cta_text:
        lines.append(
            f'CTA badge — SOLID FILLED (gold-filled on dark, dark-filled on light), '
            f'visually grouped with the price, never scattered far from it: '
            f'"{brief.cta_text}"'
        )
    else:
        lines.append(
            "NO CTA BADGE — do not render any call-to-action badge, button, pill, or "
            "action label anywhere in the ad."
        )
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
            f"Spec strip — EXACTLY {n} column{'s' if n != 1 else ''}: render ONLY "
            f"{'these items' if n != 1 else 'this item'}, nothing invented or repeated. "
            "BOLD UPPERCASE geometric sans, wide luxury letter-spacing, readable at "
            "arm's length; dark text over light zones, cream/gold over dark. Above each "
            "label ONE matching thin-line icon (no text inside icons): "
            + ", ".join(annotated)
        )
    # Cheque guard — always outside the footer block.
    # When cheque_only=True the spec strip already carries it; an "exactly once" guard
    # prevents the composition prose from triggering a second render of the same string.
    if not brief.cheque_only:
        lines.append(
            "NO CHEQUE PAYMENT text — do not render '100% Cheque Payment', "
            "'Cheque Only', or any payment-method wording anywhere in the ad."
        )
    else:
        lines.append(
            "EXACTLY-ONCE GUARD — '100% Cheque Payment' renders ONLY in the spec strip "
            "above, exactly once; ignore any other mention of it in this prompt."
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


def _strip_footer_dupes(composition: str, footer_items: list[str]) -> str:
    """Remove explicit footer item text values from composition prose.

    Root cause: when the art-director LLM quotes a footer item (e.g. '100% Cheque Payment')
    verbatim inside the composition description, that same string also appears in the
    _text_strings() spec strip block — giving Ideogram two separate rendering instructions
    for the same text, which causes a visible duplicate in the image.

    This function is the defence-in-depth layer: strip any quoted occurrence of a footer
    item from the composition prose so only the text-strings block defines it.
    This is generic — it applies to any footer item, not just cheque payment.
    """
    import re as _re
    result = composition
    for item in footer_items:
        # Match the item text inside any quote type, optionally preceded by a dash/colon
        pattern = _re.compile(
            r"""(?:—\s*|:\s*)?['""“‘]?""" + _re.escape(item) + r"""['""”’]?\s*—?""",
            _re.IGNORECASE,
        )
        result = pattern.sub("", result)
        # Also strip possessive references: "the single spec item from the brief — '100% …'"
        ref_pattern = _re.compile(
            r"""(?:spec item[s]? from the brief\s*—\s*)['""“‘]?""" + _re.escape(item) + r"""['""”’]?""",
            _re.IGNORECASE,
        )
        result = ref_pattern.sub("spec item from the brief", result)
    return result


# Prompt budget: Ideogram's hard prompt limit is 10k characters — anything past it is
# silently truncated, so the WHOLE prompt must land under 10k with margin. Within that,
# the AD COMPOSITION is the creative payload (art-directed placement, scale, tagline
# treatment, spec-strip position) — a too-tight composition allowance was itself a
# failure mode: it got clipped to a stub and renders came out templated and samey,
# ignoring every structure rule. So the Python boilerplate is kept lean, the
# composition gets a high protected floor (_COMP_FLOOR), and overshoot is reclaimed
# first from the composition down to that floor, then from the scene prose.
_MAX_PROMPT_CHARS = 9800
_SCENE_CLIP = 1200
_COMP_CLIP = 3400
_COMP_FLOOR = 2400
_SCENE_FLOOR = 700


def _clip(text: str, limit: int) -> str:
    """Trim prose to `limit` chars at the last sentence boundary (fallback: word)."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = cut.rfind(". ")
    if dot > limit // 2:
        return cut[: dot + 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip() + "."


def _design_rules(has_cta: bool) -> str:
    """Distilled, Python-authored design rules — the non-negotiables from the design
    grammar compressed to fit the prompt budget. The FULL ad_craft/typography lists
    still feed the art-director LLM, whose composition prose carries the detail."""
    rules = (
        "DESIGN RULES:\n"
        "- Typography: luxury register only — high-contrast display serif (Didot/"
        "Cormorant style) for locality and price, tracked small-caps or geometric "
        "luxury sans for labels; generous letter-spacing; mixed scale between tiers; "
        "NO system/UI fonts, no bevels, 3D or glossy text effects.\n"
        "- Hierarchy (buyer scan order): locality largest (~1.3-1.5x the config line); "
        "config/BHK second — bold, co-equal with the price in visual weight, never a "
        "thin caption; price numeral largest character in the lower half, its labels "
        "at ~a third of its size; tagline tier 3 — styled, clearly smaller; spec strip "
        "bold uppercase tracked sans, readable at arm's length.\n"
        "- Every text element fills at least 75% of its zone width — scale up, never "
        "shrink to fit; prefer 2-3 lines at large scale over one small cramped line.\n"
        "- Render every string exactly once, horizontal and upright; never repeat or "
        "echo any word as decoration or filler.\n"
        "- Surfaces: no flat solid panels except a slim bottom strip — use gradients, "
        "frosted glassmorphism, or a photo vignette behind any large text zone; any "
        "photo-to-panel boundary dissolves as a soft gradient, never a hard edge.\n"
        "- Ornaments are DRAWN shapes only (a thin gold hairline rule, a botanical "
        "sprig, a single wave, at most one small diamond) — never typed characters.\n"
        "- Conversion elements stay SEPARATE: the price's bordered box contains ONLY "
        "the price lockup; CTA badge, tagline, and spec items are freestanding — "
        "never merged with the price into one shared card.\n"
        "- The spec strip is its own horizontal strip at the very bottom edge of the "
        "ad — never attached to, beside, or inside the price container.\n"
        "- BUSY-BACKGROUND BACKING: any text element sitting over glass, window "
        "mullions, reflections, foliage, or a textured surface must get its own "
        "backing (a small frosted pill, soft scrim, or tinted shape sized to it) — "
        "never bare text floating on a busy surface, even if another element nearby "
        "already has a panel. The text colour is then chosen to contrast THAT "
        "backing's own tone, not a default cream/white — a light frosted pill takes "
        "dark text, a dark scrim or panel takes cream/gold text. A backing that "
        "doesn't flip the text colour to match is not solving the legibility "
        "problem, only decorating it.\n"
        "- SCALE BALANCE: a large locality lockup must never leave the price/CTA/"
        "tagline looking small or under-supported by comparison — give that lower "
        "conversion cluster its own strong backing so it reads as the second "
        "unmissable moment, not an afterthought.\n"
    )
    if has_cta:
        rules += (
            "- The CTA is ONE solid-filled, maximum-contrast badge grouped directly "
            "with the price — never a pale outline pill.\n"
        )
    else:
        rules += (
            "- This ad has NO call-to-action: render no badge, pill, button, banner, "
            "or imperative phrase anywhere.\n"
        )
    return rules


def build(spec: AdSpec, brief: BriefModel) -> str:
    """Assemble the single BAKED-mode Ideogram prompt.

    Structure is deliberate: TEXT STRINGS come FIRST (they are the payload — if the
    model reads nothing else, it must read these), followed by the scene/composition
    creative direction, then a compact rules block. Total length is hard-budgeted to
    _MAX_PROMPT_CHARS. Only the LLM-authored fields (scene_prose, composition) are
    sanitized; Python-authored instruction lines are assembled verbatim.
    """
    palette = lib.get_palette(spec.palette_id)
    logo_clause = _logo_zone_instruction(brief, spec.skeleton)
    text_strings = _text_strings(brief)
    brief_dict = brief.sanitizer_brief()

    # Sanitize + budget the LLM-generated prose fields (the only unbounded parts).
    clean_scene = _clip(
        sanitizer.sanitize_llm_field(spec.scene_prose.strip(), brief_dict), _SCENE_CLIP
    )
    footer = brief.footer_items()
    raw_comp = _composition_block(spec, brief)
    # Strip any footer item text values from the composition prose before it reaches Ideogram.
    # The text-strings block already defines them — a second mention in composition prose
    # causes Ideogram to render the same text twice (root cause of duplicate cheque text).
    if footer:
        raw_comp = _strip_footer_dupes(raw_comp, footer)
    clean_comp = _clip(
        sanitizer.sanitize_llm_field(raw_comp, brief_dict), _COMP_CLIP
    )

    def _assemble(comp: str) -> str:
        return (
            "A finished, professionally designed luxury real-estate advertisement "
            "(4:5 poster) — designed type, hierarchy and ad furniture, not a bare "
            "photograph with a caption. Register: quiet confidence — understated, "
            "refined, expensive; Sotheby's, not a billboard.\n\n"
            "TEXT STRINGS — render ALL of the following EXACTLY ONCE, exact spelling, "
            "no substitutions, no invented alternatives. Each string appears ONCE total "
            "in the finished ad — never duplicate any element:\n"
            f"{text_strings}\n\n"
            "TEXT SOURCE LOCK (critical): the TEXT STRINGS above are the COMPLETE and "
            "ONLY words permitted to appear as visible text anywhere in the image. "
            "Every other section of this prompt is creative direction — mood, "
            "materials, lighting, layout — NOT copy to typeset. Never promote a room "
            "name, amenity, or descriptive term from the photograph or composition "
            "into visible ad text. If a string is not listed above verbatim, it must "
            "not appear anywhere on the ad. NUMERALS, NEVER WORDS: every number in the "
            "TEXT STRINGS — the BHK/configuration count, the price, any figure in the "
            "spec strip — is set as a digit exactly as given ('4 & 5 BHK', not 'Four "
            "& Five BHK' or 'Four and Five'; '3 Cr', not 'Three Cr'). Never spell out "
            "a number as a word anywhere on the ad.\n\n"
            "STRICT RULE — NO COMPANY NAME: Do NOT render the words 'PIKORUA', "
            "'Pikorua', or any company / advisory name as visible text anywhere.\n\n"
            f"{logo_clause}"
            f"PHOTOGRAPH (photorealistic luxury real estate scene):\n{clean_scene}\n\n"
            f"AD COMPOSITION:\n{comp}\n\n"
            f"{_palette_line(palette)}\n"
            "PER-ELEMENT CONTRAST (non-negotiable): every text element must strongly "
            "contrast the surface directly behind it — legibility overrides palette "
            "fidelity. Never place text over a busy photo area without a gradient "
            "scrim or frosted panel behind it.\n\n"
            f"{_design_rules(bool(brief.cta_text))}\n"
            + (
                "MOBILE SCROLL TEST: at a 2-second glance the locality, the price "
                "numeral, and the solid CTA badge must read instantly; everything "
                "else is secondary. "
                if brief.cta_text else
                "MOBILE SCROLL TEST: at a 2-second glance the locality and the price "
                "numeral must read instantly; everything else is secondary. "
            )
            + "Photography is rich, text is confident and designed. Aspect ratio 4:5."
        )

    budget = _MAX_PROMPT_CHARS - len(sanitizer._ANTI_LOGO_GUARD)
    prompt = _assemble(clean_comp)
    if len(prompt) > budget:
        # Reclaim the excess from the composition, but never below its floor — the
        # composition is the creative payload, not the first casualty.
        overshoot = len(prompt) - budget
        clean_comp = _clip(clean_comp, max(_COMP_FLOOR, len(clean_comp) - overshoot))
        prompt = _assemble(clean_comp)
    if len(prompt) > budget:
        # Still over: reclaim the remainder from the scene prose down to its floor.
        overshoot = len(prompt) - budget
        clean_scene = _clip(clean_scene, max(_SCENE_FLOOR, len(clean_scene) - overshoot))
        prompt = _assemble(clean_comp)
    return prompt + sanitizer._ANTI_LOGO_GUARD
