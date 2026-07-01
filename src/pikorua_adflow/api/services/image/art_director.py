"""
Stage 3 — ArtDirector: one short LLM call per variant returning a strict JSON AdSpec.

This is the structural fix for "rules conflict / changes don't apply": the LLM never
writes layout prose. It writes the photographic scene and *selects* one ID from each
enumerated library (layout / palette / type pairing). The system prompt is ~80 words —
it replaces the old ~250-line compose_description() rule dump.

Set ART_DIRECTOR_MOCK=1 (or pass no CREATIVE_MODEL / no network) to get a deterministic
AdSpec without an LLM call — used by tests and as a safe fallback.
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import asdict, dataclass

from . import libraries as lib
from .brief_model import BriefModel

# Anchor variant_key -> the scene-library anchor that defines its creative brief.
_VARIANT_TO_ANCHOR = {
    "lifestyle_private_retreat": "private_retreat",
    "lifestyle_social_home": "social_family",
    "lifestyle_dynamic_a": "private_retreat",
    "lifestyle_dynamic_b": "social_family",
    "interior_signature_moment": "interior_signature",
    "exterior_establishing_shot": "exterior",
    # legacy keys still seen in older runs
    "lifestyle_city_connection": "social_family",
    "interior": "interior_signature",
}


@dataclass
class AdSpec:
    variant_key: str
    prompt_num: int
    scene_prose: str
    layout_id: str
    palette_id: str
    type_pairing_id: str
    text_anchor: str
    ornament_id: str = ""
    tone: str = "dark_luxury"
    # BAKED creative layer: the LLM-authored ad composition + the skeleton it builds on.
    # These drive baked_prompt.build(); layout_id/text_anchor remain for legacy RENDER mode.
    skeleton: str = ""
    composition: str = ""

    def to_entry(self) -> dict:
        """Serialise to the visual_prompts.json entry shape (§12)."""
        return asdict(self)

    @classmethod
    def from_entry(cls, entry: dict) -> "AdSpec":
        valid = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in entry.items() if k in valid})


def anchor_for(variant_key: str) -> dict:
    name = _VARIANT_TO_ANCHOR.get(variant_key, "interior_signature")
    return lib.anchors().get(name, {})


def _coerce_id(value: str, allowed: list[str], default: str) -> str:
    v = (value or "").strip()
    return v if v in allowed else default


def _mock_spec(variant_key: str, prompt_num: int, anchor: dict) -> dict:
    """Deterministic-ish AdSpec without an LLM (tests / offline fallback)."""
    rnd = random.Random(f"{variant_key}:{prompt_num}")
    families = anchor.get("scene_families") or ["interior_signature_moment"]
    family = rnd.choice(families)
    skeleton = rnd.choice(lib.skeleton_ids() or [""])
    skel = lib.get_skeleton(skeleton)
    return {
        "scene_prose": (
            f"Photographic scene: {family.replace('_', ' ')} for a premium residence. "
            "Shot on a full-frame camera, 35mm lens, natural directional light, shallow "
            "depth of field, one soft natural imperfection. Refined materials — stone, "
            "warm timber, brushed metal, layered textiles — render the space as calm, "
            "expensive and lived-in, leaving one uncluttered region for the ad text."
        ),
        "skeleton": skeleton,
        "composition": (
            f"Build a {skel.get('name', 'structured property ad')} composition. "
            f"{(skel.get('design') or '').strip()} "
            "Locality is the hero lockup; price in a bordered box; CTA as a defined pill; "
            "small line-icons and a thin rule add craft."
        ),
        "layout_id": rnd.choice(lib.layout_ids()),
        "palette_id": rnd.choice(lib.palette_ids()),
        "type_pairing_id": rnd.choice(lib.type_pairing_ids()),
        "tone": rnd.choice(["dark_luxury", "bright_aspirational"]),
    }


def _skeleton_menu() -> str:
    """Compact, LLM-readable digest of the skeleton grammar (ids + feel + discipline)."""
    lines = []
    for s in lib.skeletons():
        lines.append(
            f"- {s['id']} — {s.get('name','')}: {s.get('feel','')} "
            f"[discipline: {s.get('discipline','')}]"
        )
    return "\n".join(lines)


def _llm_spec(
    variant_key: str, anchor: str, brief: "BriefModel", force_skeleton: str = ""
) -> dict:
    """Scene + ad-composition LLM call. Raises on any failure (caller falls back)."""
    import litellm

    headline = brief.headline
    palette_ids = lib.palette_ids()
    type_ids = lib.type_pairing_ids()
    default_skel = lib.default_skeleton()
    craft = "\n".join(f"- {c}" for c in lib.ad_craft())
    bands = "\n".join(f"- {k}: {v}" for k, v in lib.info_band_styles().items())
    grammar = lib.design_grammar()
    brand = (grammar.get("brand_essence") or "").strip()
    sd = grammar.get("scene_direction") or {}
    scene_dir = " ".join(
        str(sd.get(k, "")).strip() for k in ("lighting", "styling", "figure", "mood")
    ).strip()
    typo = "\n".join(f"- {t}" for t in lib.typography_rules())

    is_lifestyle = "lifestyle" in variant_key

    prop_type = (brief.property_type or "").lower()
    if any(k in prop_type for k in ("apartment", "flat")):
        scene_type_rule = (
            "PROPERTY TYPE — APARTMENT: the scene must read as a premium apartment "
            "interior. Standard room heights (2.7m–3.2m max), no open-to-sky glass "
            "roofs, no vaulted double-height volumes, no bungalow-style courtyards or "
            "garden-level spaces. Apartment cues: city view through glazed windows, "
            "refined but normally-proportioned rooms, elevator-building corridor scale. "
            "The space must feel like a high-quality urban flat — not a villa, "
            "bungalow, or standalone residence. "
        )
    elif any(k in prop_type for k in ("villa", "bungalow", "house")):
        scene_type_rule = (
            "PROPERTY TYPE — VILLA/BUNGALOW: the scene must show a MODERN LUXURY private "
            "residence — a contemporary standalone home with clean architecture, private "
            "garden, lush planting, and ground-level living. "
            "SCENE SELECTION: unless the brief EXPLICITLY mentions a pool, water feature, "
            "or outdoor pool deck, do NOT default to a pool-side or poolside scene. "
            "PREFERRED SCENE TYPES (in priority order): (1) a living room or great room "
            "with FULL-HEIGHT glazed walls or sliding doors revealing the private garden "
            "and sky — the exterior is visible through glass, creating indoor-outdoor "
            "depth; (2) a covered private terrace with teak/stone outdoor furniture, "
            "pendant lanterns, lush planting framing the edges; (3) an open-plan kitchen "
            "or dining room with garden visible through large windows; (4) a transitional "
            "indoor-outdoor zone where the interior flows directly onto a private garden "
            "deck — with the home's architecture as a backdrop. Only use a pure pool-side "
            "exterior scene when the brief explicitly requests it. "
            "FORBIDDEN: heritage or colonial architecture (arched colonnades, stone "
            "balusters, Mughal motifs, aged plaster) — these read as a hotel or haveli, "
            "NOT a premium residence. FORBIDDEN: anyone standing at or leaning against a "
            "boundary wall, railing, or parapet — this reads as a hotel-guest pose, not "
            "an owner. People must be SEATED or reclining — relaxed, proprietary, "
            "unhurried. The space signals: this person OWNS this home. "
        )
    elif "penthouse" in prop_type:
        scene_type_rule = (
            "PROPERTY TYPE — PENTHOUSE: floor-to-ceiling glazing with unobstructed city "
            "panorama, elevated scale, rooftop terrace access possible. The space signals "
            "the top floor of a premium tower — dramatic views are the primary cue. "
        )
    else:
        scene_type_rule = ""

    system = (
        "You are a senior luxury real-estate ad art director. You design a finished "
        "advertisement, not a photo. Output ONLY valid JSON, no prose around it.\n\n"
        f"BRAND: {brand}\n\n"
        f"SCENE DIRECTION (make the photo read as genuinely luxury): {scene_dir}\n\n"
        "Produce THREE creative pieces:\n"
        f"PROPERTY TYPE RULE: {scene_type_rule}The scene must clearly match the property "
        "type above — wrong property-type cues break buyer trust immediately.\n\n"
        "1) scene_prose (100-130 words): the photograph only — follow the SCENE DIRECTION "
        "and PROPERTY TYPE RULE strictly. MUST include: (a) exact camera body + lens + "
        "tripod height (e.g. 'Sony A7R V, 50mm f/2.0, tripod at 110cm'); (b) time of "
        "day + lighting colour temperature (e.g. '7:45pm — warm amber pendant at 2700K, "
        "cool blue dusk at 5500K'); "
        + (
            "(c) MANDATORY for this lifestyle variant: 2 to 4 PEOPLE — HARD MAXIMUM 4, "
            "never 5 or more regardless of scene type. A dining or social scene should "
            "have 2-4 people around the table, never a full party. Mixed gender unless "
            "the brief specifically says otherwise — avoid all-female or all-male groups. "
            "State exact count, dress (tailored blazers / silk / linen resort-wear / "
            "elegant dresses), and candid gesture (mid-conversation, mid-laugh, gesturing, "
            "never posed or looking at camera). They read as owners at ease in their home; "
            if is_lifestyle else
            "(c) for interior/exterior variants: no people required; "
        ) +
        "(d) specific named materials (smoked walnut, Calacatta marble, fluted brass, "
        "Black Galaxy granite). The scene is fully furnished and styled — NEVER empty. "
        "Leave ONE calm low-detail region for text. "
        "NO layout language, NO text, NO ad furniture in this field.\n"
        + (
            f"2) skeleton: you MUST use '{force_skeleton}'. Set the skeleton field to it and "
            "design the whole composition to that archetype faithfully — do not drift.\n"
            if force_skeleton else
            "2) skeleton: pick ONE id from the skeleton menu below. Strongly prefer the "
            "new and varied archetypes: full_bleed_vignette, corner_anchor_pyramid, "
            "split_canvas, lower_text_panel, framing_device, editorial_rail. Use "
            "dark_triptych only when the scene truly suits horizontal bands. Vary "
            "across a batch — no two variants should share a skeleton. Build EXACTLY "
            "on the skeleton you pick — do not drift.\n"
        ) +
        "3) composition (70-120 words): design the ad on that skeleton in concrete visual "
        "terms. Name exact brief values for locality, city, config, price, CTA. "
        "SPEC STRIP ITEMS: NEVER write their text — refer generically.\n"
        "  ① SCROLL TEST — READ THIS FIRST, IT OVERRIDES EVERYTHING BELOW: "
        "this ad appears in a mobile feed for 2 seconds. THREE elements must be "
        "so physically large and high-contrast they are impossible to miss at a glance: "
        "(1) LOCALITY — fills most of the text zone width, the single largest element "
        "on the entire ad, set as large as the zone physically allows; "
        "(2) PRICE NUMERAL — the largest character in the lower half, large enough to "
        "read across a room; "
        "(3) CTA BADGE — solid-filled, grouped with price, impossible to miss. "
        "Everything else is secondary and exists for viewers who stop scrolling. "
        "Design these three BIG FIRST. Then fit the rest around them.\n"
        "  ② SIZE FLOOR (after scroll test): config/BHK pill must be bold and "
        "immediately readable — co-equal with price in visual weight. Tagline is "
        "Tier 3: legible but clearly smaller. Err on the side of ALL elements being "
        "TOO LARGE rather than refined and small.\n"
        "  ③ STACK ORDER — for all skeletons EXCEPT dark_triptych (top to bottom): "
        "(1) locality — very top; (2) city — immediately below locality, small tracked "
        "caps; (3) config/BHK — bold, immediately below city, NO ornament between city "
        "and config; (4) ornament separator; (5) price bordered container; (6) CTA badge "
        "— always grouped with price; (7) tagline italic; (8) spec strip — very bottom.\n"
        "  ③a DARK_TRIPTYCH ZONE RULE — use this INSTEAD of ③ for dark_triptych: "
        "HEADER BAND contains (1) locality, (2) city, (3) config/BHK pill, (4) ornament. "
        "PHOTO ZONE contains only the CONVERSION CLUSTER: (5) price box + (6) CTA badge "
        "+ (7) tagline — floating together in a clean area of the photo. FOOTER BAND "
        "contains (8) spec strip only. If the header feels crowded, expand the band — "
        "NEVER move BHK into the photo zone.\n"
        "  ④ VERTICAL DISTRIBUTION: spread groups across the full zone height using "
        "GENEROUS LEADING between groups — NOT by making text smaller to fit. "
        "Text sizes are fixed by ① and ②; leading is what fills remaining space. "
        "Forbidden: sparse top + cramped bottom. Every group visible and spaced.\n"
        "  — TAGLINE: if it fits on ONE LINE comfortably at a large size, keep it single "
        "line — larger and bolder is always better than splitting into two smaller lines. "
        "Only split at a period or dash when the tagline is genuinely too long for one "
        "line. Never split a short tagline just because a period exists. "
        "If split: first clause cream roman, second gold italic.\n"
        "  — ORNAMENTS: describe every ornament as a DRAWN VISUAL SHAPE — never typed "
        "character strings (no dashes, dots, equals signs, underscores as ornaments — "
        "Ideogram renders them literally as characters). Choose the ornament type that "
        "BEST MATCHES the skeleton's feel — do NOT default to serpentine waves every "
        "time; treat all options as equally valid and rotate them: "
        "(a) 'a small drawn diamond motif in gold' — suits formal/structured layouts "
        "(dark_triptych, editorial_rail, split_canvas); max one diamond per ad; "
        "(b) 'a pair of parallel 1px drawn gold hairline rules' — suits minimal/editorial; "
        "(c) 'a small drawn botanical leaf or branch sprig in gold hairline' — suits "
        "organic/photo-forward layouts (full_bleed_vignette, corner_anchor_pyramid); "
        "(d) 'a single thin drawn gold horizontal rule' — universal, clean; "
        "(e) 'a thin drawn serpentine gold wave flourish' — suits flowing/art-deco feels. "
        "Top and bottom dividers must be DIFFERENT drawn element types. "
        "For lower_text_panel: the ornament appears ONLY between the tagline and the "
        "spec strip (position 7 in the panel stack) — NEVER between locality/city/config "
        "or between config and the price row. Diamond is NOT a valid ornament for "
        "lower_text_panel — use botanical sprig, parallel hairlines, or serpentine wave.\n"
        "  — BACKGROUNDS: NO solid flat colour panels for large text zones. STRONGLY "
        "PREFER a GRADIENT fade (opaque at the outer edge → semi-transparent toward the "
        "photo, so the photo stays partly visible through the panel for depth) or "
        "GLASSMORPHISM (frosted ~55% opacity, subtle inner highlight, photo visible "
        "behind). Avoid a fully opaque flat panel — the transparency and photo-bleed are "
        "what make it feel crafted, not a developer placard. PHOTO VIGNETTE is also fine. "
        "Name which one you use.\n"
        "  — PHOTO-TO-PANEL TRANSITION (lower_text_panel and any layout with a text "
        "panel beneath the photo): the boundary between photo and panel MUST be a SOFT "
        "GRADIENT DISSOLVE — the bottom 15-20% of the photo zone fades organically into "
        "the panel tone with ZERO visible hard horizontal edge or cut line. The viewer's "
        "eye should not see where the photo ends and panel begins — it should feel like "
        "one unified field. If there is a hard line, the composition has failed. Always "
        "describe this explicitly in the composition: 'the photo dissolves into the panel "
        "via a soft gradient with no hard edge.'\n"
        "  — TEXT ZONE: choose a visually CLEAN zone (open wall, sky, blur plane) — "
        "never overlay text on window frames, railings, or complex architectural grids.\n"
        "  — CONVERSION ELEMENTS — NEVER MERGE INTO ONE CARD: the price box, CTA badge, "
        "and tagline are THREE SEPARATE ELEMENTS — never enclosed together in one shared "
        "rounded card or container. The price box holds ONLY the price numeral. The CTA "
        "badge is a separate solid-filled button sitting BESIDE the price box in the same "
        "horizontal row. The tagline is a freestanding italic line below the price+CTA "
        "row, breathing on the panel/background directly — not inside any box. Merging "
        "them into a single widget makes it look like a booking app, not a luxury ad. "
        "If you must use a card, it contains ONLY the price (bordered box). CTA and "
        "tagline always sit outside of it.\n"
        "  — PANEL COLOURS: any text panel must be DEEP and DARK — deep espresso "
        "(near-black brown), charcoal-walnut, dark mahogany, or midnight navy. Never a "
        "bright amber, orange, or warm mid-tone panel. The panel must be dark enough "
        "that cream and gold text reads at maximum contrast. If the scene is warm amber, "
        "the panel should be darker than the scene — not the same tone.\n"
        "  — PALETTE-SCENE PAIRING (warm-cool tension rule — most common oversight): "
        "After writing your scene, identify its dominant light temperature. "
        "COOL scene (blue-hour, overcast, twilight, cool dusk, cool-lit interior): you "
        "MUST pick a WARM-accented palette — charcoal_gold, warm_espresso, midnight_gold, "
        "warm_ivory, or emerald_bronze — so the warm editorial tones create visual tension "
        "against the cool scene. "
        "WARM scene (amber interior lights, golden hour, candlelit, sunset, warm daylight): "
        "any palette works — pairing a cool panel (deep_slate, navy_cream, burgundy_silver, "
        "steel_rose) creates contrast and is encouraged for variety, or a warm palette adds "
        "richness — both are valid. "
        "FORBIDDEN COMBINATION: a cool-toned scene + a cool-toned palette. This collapses "
        "all visual tension and the ad reads flat and corporate instead of luxury. Also vary "
        "the accent: bronze, silver, rose-copper are luxury accents alongside gold.\n"
        "  — FOOTER: spec items from brief only, CTA groups with price, no footer column "
        "for CTA.\n\n"
        f"SKELETON MENU:\n{_skeleton_menu()}\n\n"
        f"INFO-BAND STYLES (pick one for the conversion zone):\n{bands}\n\n"
        f"TYPOGRAPHY (design the type to this standard):\n{typo}\n\n"
        f"AD CRAFT (apply all):\n{craft}\n\n"
        "Then select exactly one id from each list:\n"
        f"palette_id ∈ {palette_ids}\n"
        f"type_pairing_id ∈ {type_ids}\n"
        "tone ∈ [dark_luxury, bright_aspirational]."
    )
    footer_items = " | ".join(brief.footer_items()) or "none"
    user = (
        f"PROPERTY FACTS (use these exact values everywhere — never invent alternatives):\n"
        f"  locality: {brief.locality_display}\n"
        f"  city: {brief.city_display}\n"
        f"  config: {brief.config_display}\n"
        f"  price: {brief.price_display}\n"
        f"  headline: {brief.headline!r}\n"
        f"  cta: {brief.cta_text}\n"
        f"  spec strip items (refer to these generically in composition — exact text "
        f"rendered from the anchor below): {footer_items}\n\n"
        f"Variant creative brief: {anchor}\n"
        'JSON keys: {"scene_prose","skeleton","composition","palette_id",'
        '"type_pairing_id","tone"}'
    )
    model = os.getenv("CREATIVE_MODEL", "openrouter/anthropic/claude-sonnet-4-6")
    resp = litellm.completion(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.9, max_tokens=1600,
    )
    raw = resp.choices[0].message.content.strip()
    print(f"[art_director] raw LLM response ({len(raw)} chars):\n{raw[:600]}")
    raw = re.sub(r"```(?:json)?", "", raw).strip("`").strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(f"art_director: no JSON in LLM response. raw={raw[:300]!r}")
    return json.loads(m.group(0))


def build_ad_spec(
    variant_key: str, prompt_num: int, brief: BriefModel, force_skeleton: str = ""
) -> AdSpec:
    """
    Produce a validated AdSpec for one slot. Every selected ID is coerced to a real
    library entry, so the compositor can always resolve it. Falls back to a deterministic
    mock spec if the LLM is unavailable or returns garbage.
    """
    anchor = anchor_for(variant_key)
    creative_brief = anchor.get("creative_brief", "premium residential lifestyle")
    force_skeleton = _coerce_id(force_skeleton, lib.skeleton_ids(), "") if force_skeleton else ""

    use_mock = os.getenv("ART_DIRECTOR_MOCK") == "1"
    parsed: dict
    if use_mock:
        parsed = _mock_spec(variant_key, prompt_num, anchor)
        if force_skeleton:
            parsed["skeleton"] = force_skeleton
    else:
        try:
            parsed = _llm_spec(variant_key, creative_brief, brief, force_skeleton)
        except Exception as exc:
            import traceback
            print(f"[art_director] LLM call failed — falling back to mock. Error: {exc}")
            traceback.print_exc()
            parsed = _mock_spec(variant_key, prompt_num, anchor)
            if force_skeleton:
                parsed["skeleton"] = force_skeleton

    layout_id = _coerce_id(parsed.get("layout_id"), lib.layout_ids(), lib.layout_ids()[0])
    palette_id = _coerce_id(parsed.get("palette_id"), lib.palette_ids(), lib.palette_ids()[0])
    type_id = _coerce_id(parsed.get("type_pairing_id"), lib.type_pairing_ids(), lib.type_pairing_ids()[0])
    skeleton = _coerce_id(parsed.get("skeleton"), lib.skeleton_ids(), lib.default_skeleton())
    # text_anchor must be a zone that exists in the chosen layout; default to layout id.
    layout = lib.get_layout(layout_id)
    zones = (layout.get("zones") or {})
    anchor_zone = (parsed.get("text_anchor") or "").strip()
    text_anchor = anchor_zone if anchor_zone in zones else layout_id
    tone = parsed.get("tone") if parsed.get("tone") in ("dark_luxury", "bright_aspirational") else "dark_luxury"

    return AdSpec(
        variant_key=variant_key,
        prompt_num=prompt_num,
        scene_prose=(parsed.get("scene_prose") or "").strip(),
        layout_id=layout_id,
        palette_id=palette_id,
        type_pairing_id=type_id,
        text_anchor=text_anchor,
        ornament_id=(parsed.get("ornament_id") or "").strip(),
        tone=tone,
        skeleton=skeleton,
        composition=(parsed.get("composition") or "").strip(),
    )
