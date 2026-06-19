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
        "architectural_perspective",
        prior_scene_tags=state.get("architectural_perspective", {}).get("scene", []),
        prior_tone_tags=state.get("architectural_perspective", {}).get("tone", []),
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

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

_IMAGE_VARIANTS_PATH = Path(__file__).parent / "config" / "image_variants.yaml"


class VisualPromptOutput(BaseModel):
    """Structured output every visual_prompter task must return."""
    scene_prose: str       # 60-80 word photography description only
    headline: str          # one headline selected from the copy output in context
    eyebrow: str = ""      # optional short aspirational line above the headline
    palette_tag: str       # one of the allowed palette names for this variant
    scene_tag: str         # exact scene from scene_pool
    tone_tag: str          # dark_luxury or bright_aspirational
    logo_corner: str       # corner kept clean; composite_logo() uses this post-generation
    ideogram_prompt: str = ""  # legacy compat — present in old visual_prompts.json entries


def _load_config() -> dict:
    with open(_IMAGE_VARIANTS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_CONFIG = _load_config()
VARIANT_KEYS: list[str] = list(_CONFIG["variants"].keys())


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


def compose_description(
    variant_key: str,
    prior_scene_tags: Optional[list] = None,
    prior_tone_tags: Optional[list] = None,
    prior_palette_tags: Optional[list] = None,
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
    scene_photography_brief = _CONFIG.get("scene_photography_brief", "")
    creative_brief = variant.get("creative_brief", "")

    context_block = (
        "Campaign context:\n"
        "  Property type: {property_type}  |  City: {city}  |  Locality: {locality}\n"
        "  Price: ₹{price_cr} Cr  |  Sample apartment ready: {sample_ready}\n"
        f"  Scene pool for this variant: {variant['scene_pool']}\n"
        f"  Prior scene tags (this property, this variant): {prior_scene_tags or []}\n"
        f"  Prior tone tags (this property, this variant): {prior_tone_tags or []}\n"
        f"  Default tone bias: {variant['default_tone_bias']}\n"
        f"  Allowed palette tags — pick exactly one: {allowed_palettes}\n"
        f"  Palette tags already used in this batch: {prior_palette_tags or []}\n"
        "  → Choose a palette_tag NOT in the 'already used' list above if one is available.\n"
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
        "describing. Use the exact words — do not paraphrase.\n\n"
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
        '  "logo_corner": "<bottom-left | bottom-right | top-right | top-left — '
        'choose the corner naturally kept cleanest by the scene composition>"\n'
        "}"
    )

    return (
        f"{scene_photography_brief}\n\n"
        f"{creative_brief}\n\n"
        f"{context_block}\n"
        f"{instruction_tail}"
    )
