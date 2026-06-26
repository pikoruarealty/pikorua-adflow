"""
task_composer.py — Builds per-variant task descriptions for the visual_prompter agent.

Architecture (new):
  The LLM writes ONLY a 60-80 word scene photography description + picks a headline
  from the copy output + picks a palette tag.  Python assembles the full structured
  ad brief from those creative choices via build_gpt_image_prompt() in image_service.py.

  This replaces the old pattern where the LLM wrote a 200-400 word prose prompt with
  abstract zone language ("dark rises from lower 50%") — a format gpt-image-1 ignores.

Usage (called from ContentCrew @task methods):

    from .task_composer import compose_description, VisualPromptOutput, list_variants

    desc = compose_description(
        "lifestyle_private_retreat",
        prior_scene_tags=state.get("lifestyle_private_retreat", {}).get("scene", []),
        prior_tone_tags=state.get("lifestyle_private_retreat", {}).get("tone", []),
    )
    Task(
        description=desc,
        expected_output='Valid JSON: {"scene_prose": "...", ...}',
        agent=visual_prompter_agent,
        output_pydantic=VisualPromptOutput,
        context=[write_meta_ads_task, rewrite_flagged_task],
    )

Template variables — {city}, {locality}, {price_cr}, {sample_ready}, {property_type},
{reference_images} — are left as literal strings; CrewAI substitutes them from the
campaign inputs dict at kickoff time.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

_IMAGE_VARIANTS_PATH = Path(__file__).parent / "config" / "image_variants.yaml"


class VisualPromptOutput(BaseModel):
    """Structured output every visual_prompter task must return."""
    scene_prose: str            # 120-140 word photography description only
    headline: str               # one headline selected from the copy output in context
    eyebrow: str = ""           # optional short aspirational line above the headline
    palette_tag: str            # one of the allowed palette names for this variant
    scene_tag: str              # exact scene from scene_pool
    tone_tag: str               # dark_luxury or bright_aspirational
    recipe_tag: str = ""        # chosen design recipe (learned coherent design bundle)
    logo_corner: str            # corner kept clean; composite_logo() uses this post-generation
    composition_notes: str = "" # scene-specific layout direction (150-200 words); triggers
                                # the composition-driven path in build_ad_prompt()
    ideogram_prompt: str = ""   # legacy compat — present in old visual_prompts.json entries


def _load_config() -> dict:
    with open(_IMAGE_VARIANTS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_CONFIG = _load_config()
VARIANT_KEYS: list[str] = list(_CONFIG["variants"].keys())

# Learned design grammar — recipes the LLM picks from per variant. Defensive load:
# if the file is missing, allowed_recipes resolve to empty and the prompt omits the
# recipe-selection block (pipeline still runs on the legacy palette/structure path).
_DESIGN_PRINCIPLES_PATH = Path(__file__).parent / "config" / "design_principles.yaml"


def _load_design_principles() -> dict:
    try:
        with open(_DESIGN_PRINCIPLES_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, Exception):
        return {}


_RECIPES_BY_NAME: dict[str, dict] = {
    r["name"]: r
    for r in _load_design_principles().get("recipes", [])
    if isinstance(r, dict) and r.get("name") and not r.get("disabled")
}


def _recipe_summary(name: str) -> str:
    """One-line summary so the LLM can pick the right recipe for the scene."""
    r = _RECIPES_BY_NAME.get(name)
    if not r:
        return f"  - {name}"
    palette = r.get("palette_family") or []
    if isinstance(palette, list):
        palette = ", ".join(str(p) for p in palette)
    return (
        f"  - {name} [tier: {r.get('text_tier', '?')}] — "
        f"lighting: {r.get('lighting', '')}; "
        f"type: {r.get('type_move', '')}; colour world: {palette}"
    )


def list_variants() -> list[str]:
    """Return variant keys in canonical order (5 entries)."""
    return VARIANT_KEYS


def get_variant_label(variant_key: str) -> str:
    return _CONFIG["variants"][variant_key]["label"]


def get_variant_meta(variant_key: str) -> dict:
    """Raw variant config — scene_pool, tone bias, allowed_palettes, etc."""
    return _CONFIG["variants"][variant_key]


def get_hard_bans() -> dict:
    """Convenience accessor — same data image_service.py loads for sanitisation."""
    return _CONFIG.get("hard_bans", {})


def dedupe_visual_batch(entries: list) -> list:
    """
    Guarantee the five ads in one batch look distinct WITHOUT pinning any design to a
    topic. The variant fixes only the subject (scene + creative brief); recipe and
    palette are the dynamic design language. The LLM picks them per scene, but can
    drift to the same favourites — so here we enforce, in batch order, that no two ads
    share a palette_tag or recipe_tag.

    A collision is reassigned to the first option in that variant's own allowed pool
    that is not yet used in this batch. The LLM's first-come choice is always kept;
    only later duplicates yield. If a pool is exhausted, the original choice stands.

    Mutates and returns the same list of entry dicts (each: variant_key, palette_tag,
    recipe_tag). info_band_style follows recipe_tag downstream, so deduping recipes
    also diversifies the bottom-band layouts automatically.
    """
    used_palettes: set = set()
    used_recipes: set = set()

    for entry in entries:
        meta = _CONFIG["variants"].get(entry.get("variant_key"), {})

        allowed_palettes = meta.get("allowed_palettes", [])
        palette = entry.get("palette_tag")
        if (not palette or palette in used_palettes) and allowed_palettes:
            replacement = next(
                (p for p in allowed_palettes if p not in used_palettes), palette
            )
            if replacement:
                entry["palette_tag"] = replacement
                palette = replacement
        if palette:
            used_palettes.add(palette)

        allowed_recipes = [
            r for r in meta.get("allowed_recipes", []) if r in _RECIPES_BY_NAME
        ]
        recipe = entry.get("recipe_tag")
        if recipe and recipe in used_recipes and allowed_recipes:
            replacement = next(
                (r for r in allowed_recipes if r not in used_recipes), recipe
            )
            entry["recipe_tag"] = replacement
            recipe = replacement
        if recipe:
            used_recipes.add(recipe)

    return entries


def compose_description(
    variant_key: str,
    prior_scene_tags: Optional[list] = None,
    prior_tone_tags: Optional[list] = None,
    prior_palette_tags: Optional[list] = None,
    prior_recipe_tags: Optional[list] = None,
) -> str:
    """
    Build the task description for one variant.

    The LLM's job is narrow: write the scene photography prose, select a headline
    from the copy context, optionally write an eyebrow, pick a palette tag.
    Python assembles the full structured ad brief (layout, typography, palette
    colour values) in build_gpt_image_prompt() inside image_service.py.

    prior_scene_tags / prior_tone_tags must be scoped to (property_name, variant_key)
    by the caller — not a global history across all five variants.

    Returns a string with {city}, {locality}, {price_cr}, {sample_ready},
    {property_type}, {reference_images} as literal placeholders for CrewAI.
    """
    if variant_key not in _CONFIG["variants"]:
        raise ValueError(
            f"Unknown variant '{variant_key}'. Valid keys: {VARIANT_KEYS}"
        )

    variant = _CONFIG["variants"][variant_key]
    allowed_palettes = variant.get("allowed_palettes", ["navy_gold", "charcoal_gold"])
    allowed_recipes = [r for r in variant.get("allowed_recipes", []) if r in _RECIPES_BY_NAME]
    scene_photography_brief = _CONFIG.get("scene_photography_brief", "")
    creative_brief = variant.get("creative_brief", "")

    context_block = (
        "Campaign context:\n"
        "  Property type: {property_type}  |  City: {city}  |  Locality: {locality}\n"
        "  Price: ₹{price_cr} Cr  |  Sample apartment ready: {sample_ready}\n"
        "  100% cheque payment only: {cheque_only}\n"
        f"  Scene pool for this variant: {variant['scene_pool']}\n"
        f"  Prior scene tags (this property, this variant): {prior_scene_tags or []}\n"
        f"  Prior tone tags (this property, this variant): {prior_tone_tags or []}\n"
        f"  Default tone bias: {variant['default_tone_bias']}\n"
        f"  Allowed palette tags — pick exactly one: {allowed_palettes}\n"
        f"  Palette tags already used in this batch: {prior_palette_tags or []}\n"
        "  → Choose a palette_tag NOT in the 'already used' list above if one is available.\n"
    )

    recipe_block = ""
    if allowed_recipes:
        # Shuffle so the LLM encounters a different ordering each run — prevents
        # habitual selection of the same first-listed recipe across every campaign.
        shuffled_recipes = list(allowed_recipes)
        random.shuffle(shuffled_recipes)
        summaries = "\n".join(_recipe_summary(r) for r in shuffled_recipes)
        recipe_block = (
            "DESIGN RECIPE — pick exactly one coherent design bundle for this variant. "
            "The recipe drives the ad's composition, lighting, negative space, and how much "
            "text is rendered. Choose the recipe whose lighting and mood best fit the scene "
            "you are describing.\n"
            f"  Allowed recipes:\n{summaries}\n"
            f"  Recipes already used in this batch: {prior_recipe_tags or []}\n"
            "  → Prefer a recipe_tag NOT in the 'already used' list if one fits the scene.\n"
            "  → Pick a palette_tag from allowed_palettes that best matches the chosen "
            "recipe's colour world.\n\n"
        )

    instruction_tail = (
        "SCENE SELECTION: Pick a scene from scene_pool that does NOT appear in "
        "prior_scene_tags. If all have been used, pick the one used longest ago.\n\n"
        "TONE: Pick a tone_tag that differs from the most recent prior_tone_tag "
        "unless the default_tone_bias strongly overrides it.\n\n"
        "REFERENCE IMAGES:\n"
        "{reference_images}\n"
        "Study these for photographic quality, lighting, atmosphere, and the feel "
        "of premium Indian luxury real estate advertising. Let them guide the scene "
        "prose — not text layout.\n\n"
        "HEADLINE: The Meta ad copy produced earlier in this crew run is in your "
        "context. Select one headline from it that best fits the scene you are "
        "describing. Use the exact words — do not paraphrase.\n"
        "HEADLINE SUBJECT RULE: the property, the address, or the space must be the "
        "subject of the headline — never a secondary object (a piece of furniture, "
        "a view, a meal, a lifestyle element). If the best available headline makes "
        "something other than the property its subject, pick the next best one.\n\n"
        "COMPOSITION NOTES — write 150-200 words of scene-specific layout direction. "
        "This drives every placement decision in the final image. Cover these five things "
        "with concrete, imperative sentences (percentages, positions, backing treatments):\n"
        "  1. LOCATION NAME (non-negotiable): Where 'SINDHUBHAVAN ROAD' (or the locality) "
        "sits in the MAIN PHOTO ZONE — it must be large and dominant here, not relegated "
        "to the footer. This is the primary text event. State its weight (HEAVY/BLACK serif), "
        "scale, position, and what natural surface of the scene gives it room.\n"
        "  2. HEADLINE: Where the campaign headline floats — relative to the location name "
        "and the scene's own geometry (shadow pool, sky zone, open wall, floor plane).\n"
        "  3. PRICE: Where the price module sits — pill/badge position, backing colour, size.\n"
        "  4. FOOTER/SPEC ROW: What the bottom spec treatment looks like — backing strip "
        "or compact row, height, what text it holds (amenities + size range). The layout of "
        "the bottom section must match the chosen recipe's info_band_style:\n"
        "     compact_spec_row → one narrow spec row at very bottom edge only\n"
        "     strip_three_col → three equal columns with gold hairlines\n"
        "     icon_grid_strip → three columns with icon stacked above label\n"
        "     asymmetric_band → config LARGE on left, stacked USPs on right\n"
        "     price_hero_strip → price dominant centre, specs flanking\n"
        "Use the info_band_style that matches your recipe — do not default to the same "
        "three-column spec strip for every variant.\n"
        "FOOTER CONTENT RULE: The footer holds secondary information that a buyer glances "
        "at after the dominant text has hooked them — amenity tier (e.g. CLUBCLASS AMENITIES), "
        "apartment size range (e.g. 3,300–6,100 SQ FT), or possession timeline. "
        "NEVER use storey count or tower height as a footer item — 'XX+ STOREY TOWER' is a "
        "technical building statistic, not a buyer selling point. Leave it out entirely. "
        "The apartment configuration (BHK) must be in the photo zone, not the footer.\n"
        "SCENE FABRICATION RULE: The scene may only show what the property brief supports. "
        "Lifestyle scenes inside the apartment (living room, bedroom, kitchen, dining room, "
        "private balcony) are always legitimate. Specific amenity scenes — rooftop pool, gym, "
        "sky lounge, clubhouse interior, children's play area — may ONLY be used if that "
        "amenity is explicitly named in the brief's USPs or features. A generic USP such as "
        "'Clubclass Amenities' does NOT authorise showing a specific amenity — it authorises "
        "using 'CLUBCLASS AMENITIES' as a text label. If the brief does not name a specific "
        "amenity, use an apartment interior or private balcony scene instead.\n"
        "  5. LEGIBILITY AIDS: Read the scene surface before choosing a contrast method. "
        "A bright surface (pale sky, white wall, frosted glass) is an opportunity for "
        "dark-toned text placed directly on it — often more premium than any backing. "
        "When contrast aid is genuinely needed: soft shadow, thin vignette, or hairline "
        "border. NEVER a solid rectangular backing strip or per-letter dark panel over a "
        "bright surface — this creates a cheap sign-board effect. Note the palette colour.\n"
        "RULES for composition_notes:\n"
        "  - The location name MUST appear large in the PHOTO ZONE of every ad. "
        "The footer strip is for secondary spec info only.\n"
        "  - NO LOCATION NAME IN FOOTER: If the location name is already dominant in the "
        "photo zone (which it must be), do NOT also place it in the footer strip — not as its "
        "own column, not as icon-grid label, not as a subtle confirmation line. The footer "
        "holds amenity tier and size range. Repeating the location in both zones wastes "
        "a footer column on information the buyer already read at the top of the ad.\n"
        "  - PEOPLE DO NOT DISPLACE TEXT: If a person is in the scene, ALL text elements "
        "(location name, headline, price, spec row) remain mandatory at full size and weight. "
        "Work the typography around the figure using natural negative space. A person in the "
        "frame is never a reason to remove or shrink a text element.\n"
        "  - PERSON MUST NOT BLOCK THE DARK ZONE: If the location name anchors in a natural "
        "dark floor zone, a person in the scene must not occupy or crowd that zone. The dark "
        "floor surface must remain visibly clear on at least one full side of the figure so "
        "the location name can span full width. If the scene has no such clearance, remove "
        "the person or choose a different scene.\n"
        "  - GRADIENT FADE, NOT HARD PANEL: When the location name sits in a naturally dark "
        "floor or shadow zone, describe it as a gradient — the photo fades organically into "
        "darkness toward the bottom. Never describe a solid rectangular backing panel with a "
        "hard top edge. The darkness must feel like it belongs to the scene, not like a band "
        "placed over it. The spec strip at the very bottom can be a solid band; the text zone "
        "above it must be a natural gradient from the photo.\n"
        "  - Never project text onto a wall surface at perspective angle — text must be "
        "flat overlay in shadow pools, open sky, or frame edges.\n"
        "  - Never place '4 & 5 BHK' (or any config) as a corner watermark or tiny label.\n"
        "  - APARTMENT CONFIG IN PHOTO ZONE (mandatory): The apartment configuration "
        "(e.g. '4 & 5 BHK') is a primary buying decision — it must appear at a prominent, "
        "clearly readable size INSIDE the photo zone, not relegated to the footer strip. "
        "In scenes with a sky or glazing cluster, stack it as the third item under LOCATION "
        "and CITY. In dark-zone scenes, treat it as a typographic accent near the headline. "
        "In asymmetric_band layouts, BHK goes LARGE on the left side of the spec band. "
        "The footer strip is for secondary details (amenity tier, size range) — never for BHK.\n"
        "  - BHK FONT IN PHOTO ZONE: When '4 & 5 BHK' (or any apartment config) appears "
        "in the photo zone as a featured typographic element, specify it as HEAVY display serif "
        "— the same typeface family as the headline and location name. Do NOT specify geometric "
        "sans for a photo-zone BHK callout — that renders as an Arial/system font label that "
        "looks like it was pasted from a Word document. Geometric sans is for footer spec "
        "labels only.\n"
        "  - BADGE TEXT LENGTH (length is not size): The sample badge is always PROMINENT and "
        "instantly readable regardless of word count. Use the 3-word form 'SAMPLE FLAT READY' "
        "only to fit a tight zone — it is still rendered large, never shrunk because it is "
        "short. DO NOT append a CTA phrase to a tiny corner pill — 6-word text wraps to 3 "
        "lines and becomes illegible. Use a longer phrase ('SAMPLE FLAT READY — STEP INSIDE') "
        "only where the scene gives at least 30% canvas width of clear space. Match badge "
        "WORD COUNT to available width — never match badge SIZE down to save space.\n"
        "  - PRICE & CTA PROMINENCE (non-negotiable): Price and the sample/CTA badge are the "
        "two primary conversion elements — they must read INSTANTLY, at a single glance. "
        "'Prominent' means BOTH large AND high-contrast: the price is among the two or three "
        "largest text events in the ad (second only to the location name); the CTA badge text "
        "is at least as large as the footer spec labels, never a small lozenge. Contrast is "
        "GUARANTEED: if the element's colour will not clearly pop against the surface behind "
        "it (gold on a mid-brown wall, pale text on pale stone, a dark pill on a bright "
        "ceiling), give it a solid palette pill with a gold hairline or a strong dark halo — "
        "never bare text blending into a similar tone. Bare text with no backing is allowed "
        "ONLY on a genuinely high-contrast surface (gold on near-black floor, dark on white). "
        "When in doubt, back it. Both sit within the central 70-80% focus area — never an "
        "extreme corner, never against the frame edge.\n"
        "  - SCENE NEGATIVE SPACE (compose room for the text): Do NOT let the furniture and "
        "architecture fill every surface so the text gets crammed into the one narrow strip "
        "that is left. Frame the scene so it deliberately holds calm, uncluttered zones, each "
        "sized to carry the BHK, the price and the CTA at full scale, and SPREAD these across "
        "the frame rather than stacking two or three down one edge. If a surface cannot host "
        "an element at full size, reframe wider or open a calm darker zone. Negative space is "
        "a designed asset, created on purpose — not the leftover gaps between objects.\n"
        "  - ELEMENT SPACING (intentional negative space): Every text element keeps a clear "
        "margin (at least 3-4% of canvas) from every other element and from the frame edge. "
        "No two elements touch or collide by accident. If two would clash, reposition or "
        "resize one — the spacing must read as designed, not crowded.\n"
        "  - MINIMUM SCALE: Specify actual sizes, not vague adjectives, and never shrink a "
        "secondary element with a tiny relative fraction. Location name spans at least 75% of "
        "canvas width — state this explicitly (e.g. 'spanning 78% of canvas width'). Campaign "
        "headline at least 3% canvas height. City name (AHMEDABAD) in clearly readable tracked "
        "caps — never a faint micro-line. BHK config in the photo zone large enough to read at "
        "arm's length — at least ~50% of the location name's cap height. Price badge and "
        "sample/CTA badge each PROMINENT and instantly readable at across-a-table distance — "
        "give them a confident size, never a small fraction that renders tiny. Footer text "
        "fills its column — Bold or ExtraBold, never condensed. DISTRIBUTED FLOATING SPEC "
        "LINES: when specs float as individual lines (not in a strip or grid), each line must "
        "be rendered at the same optical weight and cap height as the CTA badge text — never "
        "a micro-caption, never 'slim' or 'engraved' (both mean illegible at spec size). "
        "Bold or ExtraBold geometric sans, generously tracked. Write sizes that guarantee "
        "legibility, not sizes that look neat on a layout sketch.\n"
        "  - SUPPORTING SPECS ARE DYNAMIC (no forced bottom strip): Decide per scene how the "
        "supporting specs sit — a grounded footer strip, a single refined floating line, a "
        "distributed pair of engraved-style plaques in a shadow zone, or a slim baseline under "
        "the location name. Do NOT stamp an identical bottom strip on every ad. FILL RULE: when "
        "you DO render a footer strip or icon grid, it must carry exactly THREE balanced items — "
        "if the brief only yields two (amenities + size), add one always-true descriptor "
        "(GATED COMMUNITY / SIGNATURE LIVING / ELEVATED SPACES) to complete it; never leave a "
        "strip with one lonely or two stranded items. When you instead distribute the specs as "
        "floating lines or plaques (no strip), two is perfectly fine — do NOT pad those. The "
        "strip itself is never a default; the fill only applies once a strip exists.\n"
        "  - TYPOGRAPHIC INTEGRATION: Text must feel like it belongs to the scene — not a "
        "digital overlay or a 3D render dropped onto a photograph. The text colour follows "
        "the palette chosen for this variant; it is not always gold. NEVER describe or imply: "
        "hard bevel edges, reflective metallic sheen, plastic gloss, or over-rendered 3D depth "
        "that makes letterforms look like a video game title screen. Presence and contrast come "
        "from the scene's natural surfaces beneath the text, not from surface rendering effects.\n"
        "  - PER-ELEMENT COLOUR CHOICE: Text colour is a per-element decision based on the "
        "specific surface behind each element — not a global palette rule applied uniformly. "
        "Use gold where it reads clearly against the surface. Use cream, warm white, or "
        "off-white where the surface is mid-dark and gold would blend in. Use deep charcoal "
        "or navy directly on a bright or pale surface — this is often more premium than "
        "forcing gold or adding a pill. The goal is maximum contrast and luxury feel at each "
        "station individually. No element is obligated to match any other element's colour. "
        "SPEC VISIBILITY WARNING: footer/spec text is already at a small size — if the "
        "surface behind the specs is mid-dark or same-tone as gold (dark green, dark charcoal, "
        "dark brown), DO NOT use gold for spec text. Use cream or white instead. Gold spec "
        "text on a dark surface at small size reads as a decorative blur, not information. "
        "Contrast for small text must be absolute — never approximate.\n"
        "  - NEVER USE THE PROJECT NAME: The property name (e.g. 'Anamika Heights', or whatever "
        "the project is called) must NEVER appear in any text element — not in the footer, not "
        "in the photo zone, not anywhere. The ad shows the locality (SINDHUBHAVAN ROAD), the "
        "city (AHMEDABAD), and specs. The project name is internal only.\n"
        "  - COMPOSITIONAL DISTRIBUTION: Not all supporting elements need to cluster at one "
        "edge. The sample badge does NOT have a fixed corner — consider placing it mid-frame "
        "where the scene creates natural room: above a figure's shoulder, beside a furniture "
        "piece, against a clear wall or shadow zone. The price and headline positions are "
        "equally flexible — let the scene geometry determine where each reads best, not a "
        "fixed vertical slot. Distribution means SPREADING elements across the frame at full "
        "prominent size — it never means shrinking an element to slot it in. Each of the five "
        "variants must have a genuinely DIFFERENT element arrangement (different location "
        "anchor, different price/CTA positions); do not converge on one samey skeleton.\n"
        "  - LIGHT DIVERSITY: Do NOT default every interior to warm golden 2700K light for a "
        "'luxury' feel — over-warm scenes read as cheap and samey. Vary colour temperature "
        "across the batch: cool morning daylight, neutral afternoon daylight, blue-hour dusk, "
        "crisp night. Reserve warm tungsten / golden light for genuinely social or evening "
        "scenes only (a hosted dinner, an evening lounge) — at most one or two of five.\n"
        "  - LOCATION ANCHOR VARIETY: The location name need NOT sit at the top of every ad. "
        "Anchor it in whichever natural dark or clear zone the chosen scene offers — a "
        "shadowed foreground floor, a dark feature wall, an arch reveal, a sky or glazing "
        "band, a side column. Avoid the top-band default on every variant; vary the anchor "
        "scene to scene so the batch does not look templated.\n"
        "  - TEXT FIDELITY: The location name is a proper place name — specify it as a single "
        "continuous typographic word with no internal slash, hyphen, full-stop, space, or "
        "decorative separator inserted within the name. Common model failure modes: mid-word "
        "slash (SINDHU/BHAVAN), doubled letter (SINDHUBHAVAAN), dropped letter (SINDHBHAVAN), "
        "or the name rendered as two separate words. Address these explicitly in composition_notes "
        "by stating the word is unbroken and noting its exact character count if it is long.\n"
        "  - FOOTER GRID GEOMETRY (for icon_grid_strip layouts): The footer follows a strict "
        "column grid. A vertical gold hairline marks the exact centre. Each column has its own "
        "axis — icon centred on that axis, label text centred directly beneath. Both columns "
        "identical width, equal outer margins, equal distance from hairline to icon centre. "
        "Nothing floats independently. Specify this geometry explicitly in composition_notes "
        "when using an icon-grid footer so the model does not improvise the alignment.\n"
        "  - Reference the scene's actual geometry, not abstract zones.\n\n"
        "OUTPUT — respond with ONLY valid JSON, no markdown fences, no preamble:\n"
        "{\n"
        '  "scene_prose": "<TWO PARAGRAPHS (120-140 words total). '
        'Para 1 (50-60 words): camera body + lens + shooting angle + focal distance + '
        'light quality (colour temperature, direction, character of light) + time of day + '
        'one natural photographic imperfection. '
        'Para 2 (50-70 words): the subject\'s architectural or material character — '
        'for exteriors: facade glazing type, spandrel material, balcony profile, stone/metal/timber '
        'details, landscaping; for interiors: floor material+finish, ceiling height+feature, '
        'dominant furniture profile, surface finish hierarchy, signature architectural detail. '
        'SCENE PHOTOGRAPHY ONLY — no ad layout, no text placement, no typography.>",\n'
        '  "headline": "<one headline from the copy output, exact words>",\n'
        '  "eyebrow": "<optional short aspirational line, or empty string if none>",\n'
        '  "palette_tag": "<one of the allowed_palettes listed above>",\n'
        '  "scene_tag": "<exact sub-scene name from scene_pool>",\n'
        '  "tone_tag": "<dark_luxury or bright_aspirational>",\n'
        f'  "recipe_tag": "<one of the allowed recipe names listed above{"" if allowed_recipes else " — leave empty string if none listed"}>",\n'
        '  "logo_corner": "<bottom-left | bottom-right | top-right | top-left — '
        'choose the corner naturally kept cleanest by the scene composition>",\n'
        '  "composition_notes": "<150-200 words of concrete scene-specific layout direction '
        'covering: location name placement in photo zone, headline position, price module, '
        'footer/spec row, legibility aids. Imperative sentences only. Reference actual scene '
        'geometry. Location name in photo zone is REQUIRED and non-negotiable.>"\n'
        "}"
    )

    return (
        f"{scene_photography_brief}\n\n"
        f"{creative_brief}\n\n"
        f"{context_block}\n"
        f"{recipe_block}"
        f"{instruction_tail}"
    )
