"""
Library loader — the single read path for layouts / palettes / type_pairings /
scene_library YAML data. Every other module reads enumerated IDs through here so
"one home per concern" holds: no module re-parses the YAML or hardcodes a colour,
zone box, or font name.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_LIB_DIR = Path(__file__).parent / "libraries"
FONTS_DIR = _LIB_DIR / "fonts"
ORNAMENTS_DIR = _LIB_DIR / "ornaments"


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    path = _LIB_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── Layouts ──────────────────────────────────────────────────────────────────

def layouts() -> list[dict]:
    return _load("layouts.yaml").get("layouts", [])


def layout_ids() -> list[str]:
    return [l["id"] for l in layouts() if l.get("id")]


def get_layout(layout_id: str) -> dict:
    """Return the layout dict, falling back to the first layout if id is unknown."""
    for l in layouts():
        if l.get("id") == layout_id:
            return l
    libs = layouts()
    return libs[0] if libs else {}


# ── Palettes ─────────────────────────────────────────────────────────────────

def palettes() -> list[dict]:
    return _load("palettes.yaml").get("palettes", [])


def palette_ids() -> list[str]:
    return [p["id"] for p in palettes() if p.get("id")]


def get_palette(palette_id: str) -> dict:
    for p in palettes():
        if p.get("id") == palette_id:
            return p
    libs = palettes()
    return libs[0] if libs else {}


# ── Type pairings ────────────────────────────────────────────────────────────

def type_pairings() -> list[dict]:
    return _load("type_pairings.yaml").get("type_pairings", [])


def type_pairing_ids() -> list[str]:
    return [t["id"] for t in type_pairings() if t.get("id")]


def get_type_pairing(type_pairing_id: str) -> dict:
    for t in type_pairings():
        if t.get("id") == type_pairing_id:
            return t
    libs = type_pairings()
    return libs[0] if libs else {}


# ── Design grammar (BAKED creative layer) ──────────────────────────────────────

def design_grammar() -> dict:
    return _load("design_grammar.yaml")


def skeletons() -> list[dict]:
    return design_grammar().get("skeletons", [])


def skeleton_ids() -> list[str]:
    return [s["id"] for s in skeletons() if s.get("id") and not s.get("disabled")]


def get_skeleton(skeleton_id: str) -> dict:
    for s in skeletons():
        if s.get("id") == skeleton_id:
            return s
    libs = skeletons()
    return libs[0] if libs else {}


def default_skeleton() -> str:
    return design_grammar().get("default_skeleton") or (skeleton_ids()[0] if skeleton_ids() else "")


def ad_craft() -> list[str]:
    return design_grammar().get("ad_craft", [])


def typography_rules() -> list[str]:
    return design_grammar().get("typography", [])


def info_band_styles() -> dict:
    return design_grammar().get("info_band_styles", {})


def grammar_elements() -> dict:
    return design_grammar().get("elements", {})


# ── Scene library ────────────────────────────────────────────────────────────

def scene_library() -> dict[str, Any]:
    return _load("scene_library.yaml")


def anchors() -> dict[str, dict]:
    return scene_library().get("anchors", {})


def rotating_pool() -> list[str]:
    return list(scene_library().get("rotating_pool", []))
