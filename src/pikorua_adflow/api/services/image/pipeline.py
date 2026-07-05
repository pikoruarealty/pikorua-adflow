"""
Pipeline orchestration — the single entry the route layer calls per image slot.

`ensure_spec` turns a visual_prompts.json entry into a validated AdSpec (running the
art_director on demand if the entry is just a lazy placeholder). `generate_one` runs
the mode-appropriate final stage and returns finished PNG bytes (text included, logo
left for the route to composite).
"""

from __future__ import annotations

from . import baked_prompt, compositor, scene_renderer, image_mode
from .art_director import AdSpec, build_ad_spec
from .brief_model import BriefModel


def ensure_spec(entry: dict, brief: BriefModel) -> AdSpec:
    """
    Return a ready AdSpec for this slot. If the entry already carries the new fields
    (layout_id + scene_prose), use them; otherwise generate a fresh AdSpec via the
    art_director (handles lazy placeholders and legacy entries transparently).
    """
    variant_key = entry.get("variant_key", "")
    prompt_num = entry.get("prompt_num", 0)
    # A usable entry has a scene plus an ad design (composition for BAKED, or a layout for
    # legacy RENDER). Otherwise it's a lazy placeholder — run the art_director on demand.
    if entry.get("scene_prose") and (entry.get("composition") or entry.get("layout_id")):
        spec = AdSpec.from_entry({**entry, "variant_key": variant_key, "prompt_num": prompt_num})
        return spec
    return build_ad_spec(variant_key, prompt_num, brief)


def generate_one(
    spec: AdSpec, brief: BriefModel, key: str,
    speed: str = "QUALITY", aspect: str = "4x5", mode: str | None = None,
) -> bytes:
    """Run RENDER (scene + compositor) or BAKED (single prompt) and return PNG bytes."""
    mode = image_mode(mode)
    if mode == "baked":
        prompt = baked_prompt.build(spec, brief)
        from .ideogram_client import call as _call
        return _call(prompt, key, speed=speed, aspect=aspect)
    # RENDER (default)
    scene_bytes = scene_renderer.render(spec, brief, key, speed=speed, aspect=aspect)
    return compositor.composite(scene_bytes, spec, brief)
