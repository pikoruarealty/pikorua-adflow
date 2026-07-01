"""
Font resolver — turns a type_pairing role (display / body / accent) at a given pixel
size into a ready-to-draw PIL ImageFont, handling variable-font named instances and
falling back to a comparable bundled/system font when a file is missing.

This is the only place that touches font files, so type_pairings.yaml stays pure data.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import ImageFont

from . import libraries as lib

# Comparable fallbacks (Windows ships these; refined serif + geometric-ish sans) so
# the compositor never crashes if an OFL file failed to download.
_WIN_FONTS = Path(os.getenv("WINDIR", r"C:\Windows")) / "Fonts"
_FALLBACK_DISPLAY = ["georgiab.ttf", "cambriab.ttf", "timesbd.ttf", "ariblk.ttf"]
_FALLBACK_BODY = ["corbelb.ttf", "candarab.ttf", "arialbd.ttf", "calibrib.ttf"]


def _first_existing(names: list[str]) -> Optional[Path]:
    for n in names:
        p = _WIN_FONTS / n
        if p.exists():
            return p
    return None


def _resolve_path(spec: dict, role: str) -> Optional[Path]:
    file = (spec or {}).get("file")
    if file:
        p = lib.FONTS_DIR / file
        if p.exists():
            return p
    # fall back to a comparable system font for the role
    return _first_existing(_FALLBACK_DISPLAY if role == "display" else _FALLBACK_BODY)


@lru_cache(maxsize=512)
def _load_font(path_str: str, size: int, variation: Optional[str]) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path_str, size)
    if variation:
        try:
            font.set_variation_by_name(variation)
        except Exception:
            # static font, or the named instance is absent — use the default instance
            pass
    return font


def get_font(type_pairing_id: str, role: str, size: int) -> ImageFont.FreeTypeFont:
    """
    role: 'display' (locality/headline), 'body' (price/city/config/footer), or
    'accent' (eyebrow). Returns a PIL font at the requested pixel size.
    """
    pairing = lib.get_type_pairing(type_pairing_id)
    spec = pairing.get(role) or pairing.get("body") or {}
    path = _resolve_path(spec, "display" if role == "display" else "body")
    size = max(6, int(size))
    if path is None:
        return ImageFont.load_default()
    variation = spec.get("variation") if spec else None
    try:
        return _load_font(str(path), size, variation)
    except Exception:
        return ImageFont.load_default()
