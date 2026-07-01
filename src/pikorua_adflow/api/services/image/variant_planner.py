"""
Stage 2 — VariantPlanner: builds the batch of variant slots for a campaign, and
Stage 4 — BatchDedup: guarantees the batch looks distinct.

Fixed anchors (always planned unless skipped): private_retreat, social_family,
interior_signature. Opt-in anchor: exterior (only when the user provides a building
description). A rotating pool grows the batch toward 20-30 creatives across
regenerations without repeating used scenes.
"""

from __future__ import annotations

from . import libraries as lib

# Canonical slot order. The first three are the fixed lifestyle/interior anchors; the
# two "dynamic" slots draw from the rotating pool; exterior is opt-in and last.
ANCHOR_VARIANTS = [
    "lifestyle_private_retreat",
    "lifestyle_social_home",
    "interior_signature_moment",
]
DYNAMIC_VARIANTS = ["lifestyle_dynamic_a", "lifestyle_dynamic_b"]
OPT_IN_VARIANTS = ["exterior_establishing_shot"]

DEFAULT_BATCH = ANCHOR_VARIANTS[:1] + ["lifestyle_social_home"] + DYNAMIC_VARIANTS[:1] \
    + ["interior_signature_moment"]


def plan_batch(include_exterior: bool = False) -> list[str]:
    """
    Return the ordered list of variant_keys to generate for a fresh campaign.
    Five lifestyle/interior creatives by default (exterior is opt-in, §2).
    """
    batch = [
        "lifestyle_private_retreat",
        "lifestyle_social_home",
        "lifestyle_dynamic_a",
        "lifestyle_dynamic_b",
        "interior_signature_moment",
    ]
    if include_exterior:
        batch.append("exterior_establishing_shot")
    return batch


def next_pool_scenes(used: list[str], count: int) -> list[str]:
    """Draw `count` rotating-pool scene families not in `used` (for regeneration growth)."""
    pool = [s for s in lib.rotating_pool() if s not in set(used or [])]
    return pool[:count]


def batch_dedup(entries: list[dict]) -> list[dict]:
    """
    Guarantee across the batch (§4):
      - skeletons are distinct where the pool allows (structural variety per batch)
      - no two ads share (layout_id, palette_id)
      - minimise back-to-back reuse of type_pairing_id and text_anchor

    Mutates and returns the same list of AdSpec-shaped dicts. The LLM's first choice is
    kept; later collisions yield to the first unused option from the library.
    """
    layout_ids = lib.layout_ids()
    palette_ids = lib.palette_ids()
    type_ids = lib.type_pairing_ids()
    skel_ids = lib.skeleton_ids()

    used_pairs: set[tuple] = set()
    used_palettes: set[str] = set()
    used_skeletons: set[str] = set()
    prev_type: str | None = None
    prev_anchor: str | None = None

    for e in entries:
        # skeleton: keep distinct across the batch while the pool allows
        skel = e.get("skeleton")
        if skel and skel in used_skeletons and len(skel_ids) > len(used_skeletons):
            skel = next((s for s in skel_ids if s not in used_skeletons), skel)
            e["skeleton"] = skel
        if skel:
            used_skeletons.add(skel)

        # palette: ensure unique within the batch where pool allows
        pal = e.get("palette_id")
        if pal in used_palettes:
            pal = next((p for p in palette_ids if p not in used_palettes), pal)
            e["palette_id"] = pal
        # layout: ensure (layout, palette) pair is unique
        lay = e.get("layout_id")
        if (lay, pal) in used_pairs:
            lay = next(
                (l for l in layout_ids if (l, pal) not in used_pairs), lay
            )
            e["layout_id"] = lay
            # text_anchor may reference the old layout's zone — re-point to the new layout
            zones = (lib.get_layout(lay).get("zones") or {})
            if e.get("text_anchor") not in zones:
                e["text_anchor"] = lay
        used_pairs.add((lay, pal))
        used_palettes.add(pal)

        # type pairing: avoid immediate repeat
        tp = e.get("type_pairing_id")
        if tp == prev_type and len(type_ids) > 1:
            tp = next((t for t in type_ids if t != prev_type), tp)
            e["type_pairing_id"] = tp
        prev_type = tp

        # text_anchor: discourage back-to-back identical anchors
        anchor = e.get("text_anchor")
        if anchor == prev_anchor:
            zones = list((lib.get_layout(e.get("layout_id")).get("zones") or {}).keys())
            alt = next((z for z in ("lower_panel", "top_band", "side_rail") if z in zones and z != prev_anchor), None)
            if alt:
                e["text_anchor"] = alt
                anchor = alt
        prev_anchor = anchor

    return entries
