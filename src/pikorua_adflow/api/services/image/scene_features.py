"""
Amenity → variant distribution.

The generic-scene problem: the art director only ever saw a static anchor
`creative_brief`, so every campaign produced the same couple-on-a-sofa / marble-lobby
photos regardless of what the property actually offers. This module takes the concrete,
depictable amenities from the brief and hands EACH image variant one distinct feature to
build its scene around — so a pool, a clubhouse, a landscaped garden between towers, a
grand 4/5 BHK living room and the tower facade each get their own ad instead of five
interchangeable interiors.

The result is a `{variant_key: scene_note}` map. Each note is concrete photographic
direction (what the scene must feature), which the route passes as `extra_scene_note`
into `art_director.build_ad_spec`. A cheap LLM (GPT-4o-mini) does the mapping so it
handles arbitrary amenities gracefully; a deterministic keyword fallback covers any LLM
failure so generation never blocks.
"""

from __future__ import annotations

import json
import os
import re

# What each variant slot is naturally suited to depict — steers the LLM (and the
# fallback) toward sensible amenity→scene pairings.
_VARIANT_INTENT = {
    "lifestyle_private_retreat":
        "an intimate interior lifestyle moment (a couple/small group at ease); best for "
        "a private, calm feature — a spacious living room, private terrace, study, or "
        "a garden/skyline seen through the home's glazing.",
    "lifestyle_social_home":
        "a warm social scene with 2-4 people; best for shared/community amenities — "
        "a clubhouse, lounge, party lawn, dining, rooftop gathering space.",
    "lifestyle_dynamic_a":
        "an active or leisure lifestyle scene; best for a leisure amenity — swimming "
        "pool, spa, landscaped garden, deck, sports/gold court.",
    "lifestyle_dynamic_b":
        "a second distinct lifestyle scene; best for a DIFFERENT leisure/wellness "
        "amenity than dynamic_a — gym, jogging track, kids' play, water feature, "
        "another garden/court.",
    "interior_signature_moment":
        "an empty architectural interior where light + material + scale are the hero "
        "(no people); best for showing the grandeur of the apartments themselves — a "
        "double-height living room, a spacious 4/5 BHK great room, a signature staircase "
        "or lobby.",
    "exterior_establishing_shot":
        "the building exterior in context; best for the towers, facade, storey count, "
        "podium landscaping seen from outside.",
}

# Fallback keyword → the variant that best depicts that kind of amenity.
_KEYWORD_ROUTE = [
    (("tower", "storey", "storeyed", "facade", "elevation", "skyline of the building", "podium"),
     "exterior_establishing_shot"),
    (("club", "clubhouse", "lounge", "party", "banquet", "community", "dining", "cafe", "co-working"),
     "lifestyle_social_home"),
    (("pool", "swimming", "spa", "deck", "garden", "landscap", "court", "sport", "tennis", "golf",
      "gym", "fitness", "jogging", "track", "play", "water feature", "waterfall"),
     "lifestyle_dynamic_a"),
    (("double-height", "double height", "great room", "living", "lobby", "foyer", "staircase",
      "bhk", "penthouse", "duplex", "high ceiling", "spacious"),
     "interior_signature_moment"),
    (("terrace", "balcony", "study", "library", "wardrobe", "master", "bedroom", "kitchen", "view"),
     "lifestyle_private_retreat"),
]


def _clean(items) -> list[str]:
    if isinstance(items, str):
        items = [items]
    return [str(a).strip() for a in (items or []) if str(a).strip()]


def _fallback(amenities: list[str], variant_keys: list[str]) -> dict:
    """Deterministic keyword routing — each amenity goes to its best-fit variant; a
    variant that collects several amenities keeps the first, then extras spill to the
    next still-empty variant so scenes stay distinct."""
    order = list(variant_keys)
    assigned: dict[str, list[str]] = {}
    leftovers: list[str] = []
    for amen in amenities:
        low = amen.lower()
        target = next(
            (vk for kws, vk in _KEYWORD_ROUTE if vk in order and any(k in low for k in kws)),
            None,
        )
        if target and target not in assigned:
            assigned[target] = [amen]
        else:
            leftovers.append(amen)
    # Spill leftovers into still-unassigned variants, then round-robin the rest.
    empties = [vk for vk in order if vk not in assigned]
    for amen in leftovers:
        if empties:
            assigned[empties.pop(0)] = [amen]
        else:
            # attach to the variant with the fewest features so far
            target = min(order, key=lambda vk: len(assigned.get(vk, [])))
            assigned.setdefault(target, []).append(amen)
    return {
        vk: f"Build this scene around the property's {', '.join(feats)}. Make it the clear "
            f"hero of the photograph — a real, specific, on-brief feature, not a generic room."
        for vk, feats in assigned.items()
    }


def _llm(amenities: list[str], variant_keys: list[str], brief: dict) -> dict:
    """Ask GPT-4o-mini to give each variant one distinct amenity to feature. Raises on
    any failure so the caller falls back."""
    import litellm

    prop_type = (brief.get("property_type") or "").strip() or "residence"
    intent_lines = "\n".join(
        f"- {vk}: {_VARIANT_INTENT.get(vk, 'a distinct lifestyle scene')}"
        for vk in variant_keys
    )
    system = (
        "You are an art-direction planner for a luxury real-estate ad campaign. A property "
        "has several concrete, depictable amenities. You must spread them across the ad "
        "variants so EACH variant's photograph features a DIFFERENT real amenity — never "
        "the same feature twice, never a generic scene when a real amenity fits. "
        "Return ONLY valid JSON: an object mapping each variant_key to one short scene "
        "directive (1-2 sentences) naming the specific amenity that variant must feature "
        "and how to shoot it. Match each amenity to the variant best suited to show it "
        "(see the intents). If there are more variants than amenities, give the extra "
        "variants a distinct on-brief scene derived from the property type — never repeat "
        "an amenity. If an amenity clearly suits no listed variant, still place it on its "
        "closest match. Never invent amenities that are not in the list."
    )
    user = (
        f"Property type: {prop_type}\n"
        f"Amenities (depict these — do not invent others):\n"
        + "\n".join(f"- {a}" for a in amenities)
        + f"\n\nVariants and what each is suited to depict:\n{intent_lines}\n\n"
        'Return JSON like {"variant_key": "scene directive", ...} covering every '
        "variant_key listed above."
    )
    model = os.getenv("MODEL", "openrouter/openai/gpt-4o-mini")
    resp = litellm.completion(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.4, max_tokens=700,
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    data = json.loads(m.group(0) if m else raw)
    # Keep only known variant keys with non-empty string notes.
    return {
        vk: str(note).strip()
        for vk, note in data.items()
        if vk in variant_keys and str(note).strip()
    }


def distribute(amenities, variant_keys: list[str], brief: dict | None = None) -> dict:
    """Return {variant_key: scene_note} spreading amenities across the variants.

    Empty dict when there are no amenities (callers then use the plain anchor brief).
    Never raises — falls back to deterministic keyword routing on any LLM error.
    """
    amenities = _clean(amenities)
    variant_keys = [vk for vk in variant_keys if vk]
    if not amenities or not variant_keys:
        return {}
    if os.getenv("SCENE_FEATURES_MOCK") == "1":
        return _fallback(amenities, variant_keys)
    try:
        result = _llm(amenities, variant_keys, brief or {})
        if result:
            return result
    except Exception as exc:  # pragma: no cover - network/parse guard
        print(f"[scene_features] LLM distribution failed, using fallback: {exc}")
    return _fallback(amenities, variant_keys)
