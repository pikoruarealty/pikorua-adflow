"""
Redesigned image-generation pipeline for Pikorua AdFlow (see NEW_IMAGE_PIPELINE.md).

Governing principle — ONE home per concern:
  scene/photography  -> art_director scene_prose
  layout/placement   -> libraries/layouts.yaml (data, % zones)
  colour             -> libraries/palettes.yaml (data, hex)
  legibility/sizing  -> compositor.py (code)
  claims safety      -> sanitizer.py (one ban list)
  exact text strings -> brief_model.BriefModel

Two modes:
  RENDER (default) — Ideogram paints the scene only; compositor.py renders all text
                     deterministically in PIL (legibility is a code guarantee).
  BAKED  (fallback) — one clean Ideogram prompt with text baked in.

Pipeline: Brief -> BriefModel -> VariantPlanner -> ArtDirector(AdSpec)
          -> BatchDedup -> SceneRenderer(Ideogram) -> Compositor|BakedPrompt -> PNG
"""

from __future__ import annotations

import os


def image_mode(variant_override: str | None = None) -> str:
    """Resolve the active render mode. Per-variant override beats the env default."""
    if variant_override in ("render", "baked"):
        return variant_override
    # BAKED (Ideogram designs the ad from the art-director's composition) is the default
    # and the only shipped path; the legacy PIL compositor ("render") is opt-in only.
    mode = (os.getenv("IMAGE_MODE", "baked") or "baked").strip().lower()
    return mode if mode in ("render", "baked") else "baked"
