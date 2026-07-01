"""
Reference-creative handling (§8), rebuilt for the RENDER pipeline.

Text-replacement failures disappear because Ideogram no longer renders ad text — the
compositor does. Vision extracts the reference's photographic mood and its rough layout
(which is mapped to one of our enumerated layout_ids); our brief text is then composited
deterministically, so the wording is exact every time.

Mode REPLACE  ("our ad in their style")  — remix the reference photo for a close visual
              match, then code-composite OUR brief text into the matched layout.
Mode RELAYOUT ("keep their layout, new scene") — generate a fresh AI scene, then
              composite OUR brief text into the reference-matched layout.

The cached vision calls themselves live in image_service.py (reused, not duplicated).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..image_service import (
    analyze_reference_image as _analyze,
    extract_reference_ad_layout as _extract_layout,
    ref_description_path,  # re-exported for routes that list cached descriptions
)

__all__ = [
    "analyze_reference_image",
    "extract_layout_prose",
    "match_layout_id",
    "ref_description_path",
]


def analyze_reference_image(img_path: Path) -> str:
    """Photographic mood/atmosphere of the reference (cached to .desc.txt)."""
    return _analyze(img_path)


def extract_layout_prose(img_path: Path) -> str:
    """Where text elements sit in the reference ad, as prose (cached to .layout.txt)."""
    return _extract_layout(img_path)


# Keyword -> enumerated layout_id. The vision prose describes element positions; we map
# it to the closest layout in libraries/layouts.yaml so the compositor can render it.
def match_layout_id(layout_prose: str) -> str:
    text = (layout_prose or "").lower()
    # order matters: more specific cues first
    if re.search(r"\bright(?:-| )?(?:side|rail|column|edge)\b", text) or "vertical rail" in text:
        return "side_rail"
    if "border" in text or "frame" in text or "inset" in text:
        return "framed_border"
    if "top" in text and "third" in text:
        return "top_band"
    if "editorial" in text or ("solid" in text and "block" in text):
        return "editorial_split"
    if "lower" in text or "bottom" in text or "below" in text:
        return "lower_panel"
    return "lower_panel"
