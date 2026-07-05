"""
Stage 5 — SceneRenderer: builds a SCENE-ONLY Ideogram prompt (no text strings, no
layout doctrine) from AdSpec.scene_prose + photographic-quality cues + one instruction
to keep the text_anchor zone visually calm. Then calls Ideogram v4.

In RENDER mode the model paints only photography; all ad text is composited later in
code, so legibility is a guarantee rather than a model hope.
"""

from __future__ import annotations

from . import sanitizer
from . import libraries as lib
from .art_director import AdSpec
from .brief_model import BriefModel
from .ideogram_client import call as _ideogram_call

# Where each layout wants the calm zone, phrased for the image model.
_ANCHOR_HINT = {
    "top_band": "keep the upper third of the frame calm and uncluttered (open sky, soft wall, or shadow)",
    "lower_panel": "keep the lower third of the frame calm and uncluttered (floor, shadow pool, or quiet surface)",
    "full_bleed_gradient": "keep the lower-left quadrant calm and softly shadowed",
    "side_rail": "keep the right edge of the frame calm and uncluttered",
    "framed_border": "keep the top and bottom edges of the frame calm and uncluttered",
    "editorial_split": "keep the lower 40% of the frame calm with a darker, quieter tone",
}

_QUALITY_CUES = (
    "Editorial luxury real-estate photography, premium Indian developer quality. "
    "Full-frame camera, prime lens, controlled natural light, true-to-life colour, fine "
    "grain, realistic materials and reflections. The photograph fills the entire frame "
    "(no borders, no text, no graphics). Human subjects wear refined Western attire — "
    "tailored suits, silk resort-wear, elegant dresses; never traditional Indian clothing. "
    "Aspect ratio 4:5."
)


def build_scene_prompt(spec: AdSpec, brief: BriefModel) -> str:
    """Assemble the scene-only Ideogram prompt for an AdSpec (RENDER mode)."""
    anchor_key = spec.text_anchor if spec.text_anchor in _ANCHOR_HINT else spec.layout_id
    calm = _ANCHOR_HINT.get(anchor_key, "leave one calm, uncluttered zone for later text overlay")
    raw = f"{spec.scene_prose.strip()}\n\n{_QUALITY_CUES}\nComposition: {calm}."
    # Only the scene prose is LLM-authored, so sanitize it (strip claims/tech noise).
    return sanitizer.sanitize(raw, brief.sanitizer_brief(), strip_tech_noise=True)


def render(spec: AdSpec, brief: BriefModel, key: str, speed: str = "QUALITY",
           aspect: str = "4x5") -> bytes:
    """Build the scene-only prompt and call Ideogram v4. Returns raw scene image bytes."""
    prompt = build_scene_prompt(spec, brief)
    return _ideogram_call(prompt, key, speed=speed, aspect=aspect)
