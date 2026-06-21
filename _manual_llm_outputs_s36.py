"""
Fresh Anamika Heights prompts — Session 36.

Changes applied vs previous batch:
  - No golden/amber light unless genuinely scene-warranted (blue-hour, cool morning,
    crisp overcast, warm tungsten interior preferred over golden-hour as default)
  - the_architectural_dead_zone: text in shadow pools / open sky only — never on walls
  - BHK config is PRIMARY info, same bold weight as all other footer items; never a
    corner watermark or shrunken label
  - All 5 variants: distinct scenes, distinct palettes, distinct recipes
  - 5 fresh headlines not used in any prior batch

Run:  python _manual_llm_outputs_s36.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from pikorua_adflow.crews.content_crew.task_composer import dedupe_visual_batch, get_variant_meta
from pikorua_adflow.api.services.image_service import build_ad_prompt, sanitize_image_prompt

BRIEF = {
    "property_name":           "Anamika Heights",
    "property_type":           "Apartment",
    "locality":                "Sindhubhavan Road",
    "city":                    "Ahmedabad",
    "price_cr":                "3",
    "config":                  "4 & 5 BHK",
    "sample_ready":            True,
    "usps":                    ["Clubclass Amenities / 30+ Storey Tower"],
    "standout_feature": (
        "4 & 5 BHK residences from 3,300 to 6,100 sq ft on Sindhubhavan Road, "
        "Ahmedabad. 30+ storey tower. Clubclass amenities."
    ),
    "rera_verified":           False,
    "verified_awards":         False,
    "verified_certifications": False,
    "verified_landmarks":      False,
}

LLM_OUTPUTS = [

    # =========================================================================
    # VARIANT 1 — Lifestyle / Private Retreat
    # scene: bathroom_spa_ritual_morning_light
    # Light: clean neutral 5200K diffused morning — zero amber, zero golden hour
    # Recipe: the_open_room_anchor (floating pill upper-left; photo 85%+)
    # Palette: charcoal_gold
    # Headline: "Some mornings are worth being home for."
    # =========================================================================
    {
        "variant_key":  "lifestyle_private_retreat",
        "prompt_num":   1,
        "scene_prose": (
            "Sony A7R V, 35mm f/4, tripod at the doorway angled toward the freestanding bath "
            "below a large frosted floor-to-ceiling window, 2.4m wide by 2m tall. 7:20am — "
            "soft morning light through frosted glass; the shadow of a tree outside is softly "
            "visible through it, adding organic texture. The room is bright and open — the "
            "window fills the entire back wall above the tub. ISO 64, f/5.6. Faint chromatic "
            "aberration at the glass frame edge.\n\n"
            "Master bathroom, 18 feet wide. Teak-battened coffered ceiling with recessed warm "
            "ambient lighting — the ceiling warmth is from the room's own architectural "
            "lighting, complementing the cool morning window light. Freestanding oval tub in "
            "honed Calacatta Viola marble, 1.8m long, positioned below the large window. "
            "A woman reclines in the tub, eyes closed, one arm resting along the marble rim — "
            "private, completely at ease, not aware of the camera. The tub is centred; the "
            "dark hexagonal floor is clearly visible on both sides of it and in front of it "
            "between the tub base and the camera — this foreground floor zone must remain "
            "unobstructed. Full-height book-matched Calacatta Oro slabs on both side walls. "
            "Hexagonal honed Nero Marquina tile across the entire floor — the dark field "
            "fades naturally toward the camera as the window light doesn't reach the floor "
            "plane fully, creating a deep natural gradient from pale marble above to near-black "
            "tile below. Slim brushed-bronze deck-mount tap."
        ),
        "composition_notes": (
            "PEOPLE DO NOT DISPLACE TEXT: The woman in the tub is a scene element only. "
            "All text below is mandatory at full size — her presence does NOT reduce or "
            "remove any text element. Work the typography into the natural dark floor zone "
            "below and beside the tub.\n"
            "The editorial device: a compact floating pill anchored in the upper-left quarter. "
            "The pill has a delicate gold hairline border and the lightest possible charcoal "
            "frost backing — barely opaque; the marble wall behind it is still legible. "
            "Position: 6% from the left edge, 8% from the top. "
            "Inside the pill (headline ONLY): 'Some mornings are worth being home for.' "
            "in conversational italic display serif, warm white, mixed-case.\n"
            "SINDHUBHAVAN ROAD: HEAVY gold luxury display serif sitting in the natural "
            "gradient fade zone — the photo darkens organically toward the lower 25% of the "
            "canvas as the floor recedes from the window light. The text does not sit on a "
            "panel; it emerges from the darkness as the scene fades. Letterforms are "
            "dimensional — embossed quality, subtle depth, as if cast in gold against the "
            "dark hexagonal tile. Scale: spans the full canvas width edge to edge. "
            "Gold (#C9A84C). AHMEDABAD in small tracked geometric gold caps directly below.\n"
            "Price badge: 'FROM ₹3 CR ONWARDS' — bottom-right of the photo zone just above "
            "the spec strip. Charcoal pill (#2B2420), gold hairline border, HEAVY serif gold.\n"
            "Sample badge: 'SAMPLE FLAT READY — COME EXPERIENCE IT' — bottom-left of photo "
            "zone, above spec strip. Charcoal pill (#2B2420), gold hairline border.\n"
            "Spec strip: slim solid charcoal (#2B2420), full canvas width, 7% height. "
            "Bold tracked ALL CAPS geometric gold: "
            "'4 & 5 BHK RESIDENCES  ·  CLUBCLASS AMENITIES  ·  30+ STOREY TOWER'. "
            "Same bold weight throughout — '4 & 5 BHK RESIDENCES' is not shrunken.\n"
            "Bottom-right corner of full canvas: kept entirely clear — logo compositing zone."
        ),
        "headline":    "Some mornings are worth being home for.",
        "eyebrow":     "",
        "palette_tag": "charcoal_gold",
        "scene_tag":   "bathroom_spa_ritual_morning_light",
        "tone_tag":    "bright_aspirational",
        "recipe_tag":  "the_open_room_anchor",
        "logo_corner": "bottom-right",
        "badge_cta":   "SAMPLE FLAT READY",
    },

    # =========================================================================
    # VARIANT 2 — Lifestyle / The Social Home
    # scene: kitchen_casual_hosting_moment
    # Light: warm white tungsten at 3000K from recessed ceiling spotlights — interior
    #        kitchen lighting, NOT golden-hour amber; clean and directive
    # Recipe: the_horizon_anchor (SINDHUBHAVAN ROAD dominant in photo zone; footer = spec)
    # Palette: navy_gold
    # Headline: "Full floors. Not floor plans." — the PROPERTY/SPACE is the subject
    # =========================================================================
    {
        "variant_key":  "lifestyle_social_home",
        "prompt_num":   2,
        "scene_prose": (
            "Sony A7R V, 28mm f/2.0, handheld at counter height from behind the island, "
            "looking across toward the kitchen's back wall. 9pm — warm white tungsten at 3000K "
            "from three rows of flush ceiling spotlights; clean and directive light that renders "
            "the Statuario marble counter in sharp relief without any amber cast. ISO 400, f/2.8. "
            "Depth of field falls off at 4 metres; the open dining zone behind goes softly out "
            "of focus. No lens flare — the fixtures are fully recessed.\n\n"
            "The kitchen island is 3.6m long, polished Statuario marble worktop with a honed "
            "prep-zone inset. Two women stand at opposite ends of the island — one in a "
            "moss-green silk blazer, one in cream wide-leg linen — arranging food casually. "
            "A third figure, a man in a stone-grey overshirt, is half-visible over one shoulder "
            "in the background. The back wall: bespoke matte lacquered cabinetry in deep charcoal "
            "green, brushed-brass bar pulls. A half-sliced mango and a wine glass on the counter "
            "— nothing else staged beyond the meal. The room is large enough that none of the "
            "three figures appear crowded against each other."
        ),
        "composition_notes": (
            "LOCATION NAME IN PHOTO ZONE (required, non-negotiable):\n"
            "'SINDHUBHAVAN' sweeps across the upper photo zone as a single unbroken line of "
            "HEAVY or BLACK weight gold luxury serif — spanning roughly 70% of the canvas width. "
            "It sits approximately 10-15% from the top of the canvas, anchored against the "
            "dark ceiling/soffit area of the kitchen. This is the dominant visual element of "
            "the ad — the space and the address are the same thing.\n"
            "'ROAD' directly below on its own line, same HEAVY weight, same gold, roughly "
            "60% of the width of SINDHUBHAVAN. Below that: 'AHMEDABAD' in small tracked "
            "geometric caps, gold, quieter — city as confirmation, not a banner.\n"
            "Campaign headline 'Full floors. Not floor plans.' floats in the mid-photo zone "
            "in bold italic display serif, warm white, at roughly 40% canvas height — the "
            "creative provocation between the address above and the scene below.\n"
            "Price: a refined navy pill (#0D1B2A fill, gold hairline border) anchored to the "
            "right of the photo zone at the figures' eyeline — off-axis. Inside: 'RS 3 CR "
            "ONWARDS' in HEAVY display serif, gold. Unmissable.\n"
            "Sample badge: 'SAMPLE FLAT READY — SEE THE SPACE' — compact navy pill (#0D1B2A "
            "fill, gold hairline border), upper-left corner of the photo zone. Bold geometric "
            "sans, gold text.\n"
            "'4 & 5 BHK' rendered boldly in the photo zone at mid-right, just to the right of "
            "the second figure's shoulder — set against the dark charcoal cabinetry wall. "
            "HEAVY geometric gold sans, large enough to read at arm's length. This is the "
            "configuration callout in the photo zone, NOT the footer.\n"
            "Navy footer strip (#0D1B2A), full canvas width, 18-20% height. Icon-grid layout: "
            "three columns separated by thin gold hairlines. LEFT: stacked — small geometric gold "
            "icon (tower/building silhouette) above '30+ STOREYS' in tracked gold caps. "
            "CENTRE: stacked — icon (home/plan) above 'CLUBCLASS AMENITIES'. "
            "RIGHT: stacked — icon (check) above 'SINDHUBHAVAN ROAD, AHMEDABAD'. "
            "All columns equal height and visual weight.\n"
            "Top-left corner: clear for logo compositing."
        ),
        "headline":    "Full floors. Not floor plans.",
        "eyebrow":     "",
        "palette_tag": "navy_gold",
        "scene_tag":   "kitchen_casual_hosting_moment",
        "tone_tag":    "dark_luxury",
        "recipe_tag":  "the_horizon_anchor",
        "logo_corner": "top-left",
        "badge_cta":   "SAMPLE APARTMENT READY",
    },

    # =========================================================================
    # VARIANT 3 — Lifestyle / Dynamic A
    # scene: parent_and_child_reading_corner
    # Light: soft overcast Sunday afternoon, 4000K cool-neutral diffused through
    #        full-height living room glazing. No golden tones — clean flat magazine light.
    # Recipe: the_zenith_gaze (bright even overhead daylight, text in open sky/ceiling zone)
    # Palette: burgundy_gold
    # Headline: "Built for how you actually live."
    # =========================================================================
    {
        "variant_key":  "lifestyle_dynamic_a",
        "prompt_num":   3,
        "scene_prose": (
            "Sony A7R V, 35mm f/4, tripod at standing eye level slightly back from the seating "
            "zone, shooting across the living room toward full-height glazing. 3:30pm Sunday — "
            "soft overcast daylight at 4000K enters through a 4-metre floor-to-ceiling window; "
            "even, flat, no direct shadows. ISO 200, f/5.6. Very slight atmospheric haze from "
            "the glass pane creates a faint soft vignette at the far corners.\n\n"
            "The living room is 24 feet wide, herringbone European oak floor (200x1200mm, "
            "natural oil finish). A woman in a sand-coloured linen dress sits cross-legged on "
            "the floor beside a low walnut coffee table; a girl of about five sits in her lap, "
            "both absorbed in a large picture book spread across the table. Three more books "
            "fanned around them — nothing else on the floor. The sofa behind them: deep-set, "
            "slate-grey bouclé, low profile. Through the glazing: Ahmedabad skyline diffused "
            "by afternoon haze, sky pale silver-white. The room is entirely still."
        ),
        "composition_notes": (
            "PEOPLE DO NOT DISPLACE TEXT: The mother and child are scene elements. "
            "All text below is mandatory at full size — their presence does not justify "
            "removing or reducing any text element.\n"
            "The zenith_gaze device: the pale silver-white sky through the glazing occupies "
            "the upper 35-40% of the canvas — this is the primary typography zone. "
            "All dominant text lives in this bright sky/glass area, rendered with strong "
            "contrast backing so it reads against the pale ground.\n"
            "'SINDHUBHAVAN ROAD' — HEAVY or BLACK weight luxury display serif, spanning "
            "roughly 65% of canvas width across the sky zone. Navy-backed: each letter "
            "on a tight, minimal deep navy backing strip (#0D1B2A, just wide enough to give "
            "contrast — not a full-width panel). Gold (#C9A84C). Monumental in scale. "
            "This is the dominant visual element.\n"
            "Below: 'AHMEDABAD' — small tracked geometric gold caps, same navy backing.\n"
            "'Built for how you actually live.' — bold italic display serif, pure white, "
            "floats mid-frame at roughly 50% canvas height where the glazing "
            "frame meets the room interior. Reads cleanly against the oak floor zone.\n"
            "Price badge: 'FROM ₹3 CR ONWARDS' — compact navy pill (#0D1B2A fill, "
            "gold hairline border), anchored bottom-right of the photo zone above the spec strip. "
            "HEAVY serif, gold. Unmissable.\n"
            "Sample badge: 'SAMPLE FLAT READY — STEP INSIDE' — compact navy pill (#0D1B2A "
            "fill, gold hairline border), bottom-left of photo zone above the spec strip.\n"
            "Spec strip: slim navy backing (#0D1B2A), full canvas width, 7% height. "
            "Bold tracked geometric gold: '4 & 5 BHK RESIDENCES  ·  CLUBCLASS AMENITIES  "
            "·  30+ STOREY TOWER'. All at equal weight and size.\n"
            "Bottom-left corner: clear for logo compositing."
        ),
        "headline":    "Built for how you actually live.",
        "eyebrow":     "",
        "palette_tag": "slate_cream",
        "scene_tag":   "parent_and_child_reading_corner",
        "tone_tag":    "bright_aspirational",
        "recipe_tag":  "the_zenith_gaze",
        "logo_corner": "bottom-left",
        "badge_cta":   "SAMPLE FLAT READY",
    },

    # =========================================================================
    # VARIANT 4 — Lifestyle / Dynamic B
    # scene: home_office_afternoon_focus
    # Light: late afternoon west-facing window light, 3800K warm amber slant.
    #        This is the ONE case where warm directional light is justified —
    #        west afternoon in a focus-oriented room; it's earned, not defaulted.
    # Recipe: the_architectural_dead_zone (shadow zones for text, no wall projection)
    # Palette: forest_gold
    # Headline: "Some offices are worth commuting home to."
    # =========================================================================
    {
        "variant_key":  "lifestyle_dynamic_b",
        "prompt_num":   4,
        "scene_prose": (
            "Sony A7R V, 50mm f/3.5, tripod at seated eye level, slightly off-axis to the "
            "right of the desk looking across toward a narrow west-facing window. 4:45pm — "
            "warm directional sunlight at 3800K cuts a diagonal across the desk surface from "
            "the upper-right, rendering the walnut grain in fine relief. ISO 100, f/4. A faint "
            "lens flare at the window edge — the sun just catching the front element.\n\n"
            "The study: a custom floor-to-ceiling walnut bookcase on the left wall, shelves "
            "sparse (five books upright, one bronze cylindrical object, one folded document). "
            "A wide custom walnut desk, 2.2m long, honed dark-grey Dekton surface. A man in "
            "his early thirties, fitted charcoal linen overshirt, seated at the desk reading "
            "a printed document — back three-quarters to camera, not aware of it. A slim "
            "brushed-steel desk lamp (off) at the far end. The city is barely visible through "
            "the narrow window — a vertical strip of Ahmedabad skyline, warm with afternoon "
            "haze. Floor: 300x1200 honed dark basalt, wide-jointed. The room is designed for "
            "one thing: uninterrupted thought."
        ),
        "composition_notes": (
            "PEOPLE DO NOT DISPLACE TEXT: The man at the desk is a scene element. "
            "All text below is mandatory at full size. Work the typography into the dark "
            "zones the scene naturally provides around and above the figure.\n"
            "The architectural_dead_zone device: the deep shadow along the left wall "
            "(bookcase side, away from window) and the dark basalt floor zone below the "
            "desk both provide natural text surfaces. Text lives only here — never on any "
            "lit wall or surface catching the afternoon light.\n"
            "'SINDHUBHAVAN ROAD' — HEAVY gold luxury display serif, rendered in the deep "
            "shadow zone of the upper-left (bookcase wall area), spanning roughly 55% of "
            "canvas width. Gold (#C9A84C) against the natural dark — no backing needed if "
            "the shadow is deep enough; add a minimal vignette only if the contrast fails. "
            "AHMEDABAD in small tracked geometric caps directly below, same zone.\n"
            "'Some offices are worth commuting home to.' — bold italic display serif, "
            "warm cream, anchored just above the desk surface in the mid-frame area. "
            "The warm afternoon light grazes above it; the headline sits in the cooler "
            "shadow just under the light beam.\n"
            "Price badge: 'FROM ₹3 CR ONWARDS' — compact forest-green pill (#1C3325 fill, "
            "gold border), anchored bottom-right of the photo zone above the spec strip. "
            "HEAVY serif, gold.\n"
            "Sample badge: 'SAMPLE FLAT OPEN — EXPERIENCE IT' — compact forest-green pill "
            "(#1C3325 fill, gold hairline border), bottom-left of photo zone.\n"
            "Asymmetric spec band at very bottom, forest-green (#1C3325), 9-10% canvas height. "
            "LEFT 40%: '4 & 5 BHK' in LARGE gold display serif — dominant, the configuration "
            "as a typographic event in its own right. RIGHT 60%: two rows of tracked geometric "
            "gold caps — 'CLUBCLASS AMENITIES' above, '30+ STOREY TOWER' below. Thin gold "
            "vertical hairline divides left from right.\n"
            "Top-left corner: the deep shadow zone of the bookcase wall keeps this area "
            "compositionally clean — logo compositing zone."
        ),
        "headline":    "Some offices are worth commuting home to.",
        "eyebrow":     "",
        "palette_tag": "forest_gold",
        "scene_tag":   "home_office_afternoon_focus",
        "tone_tag":    "dark_luxury",
        "recipe_tag":  "the_architectural_dead_zone",
        "logo_corner": "top-left",
        "badge_cta":   "SAMPLE FLAT OPEN",
    },

    # =========================================================================
    # VARIANT 5 — Interior Signature Moment
    # scene: study_or_reading_nook_lamp_glow
    # Light: cool overcast ambient (6000K from clerestory) + ONE precise warm lamp
    #        circle (2700K). The contrast between cool ambient and warm lamp IS the drama.
    #        Not over-golden — restrained, composed, architectural.
    # Recipe: the_glass_morphism_shield (floating pill upper-left, bright daylight)
    # Palette: ivory_warmth
    # Headline: "Space that earns its silence."
    # =========================================================================
    {
        "variant_key":  "interior_signature_moment",
        "prompt_num":   5,
        "scene_prose": (
            "Sony A7R V, 50mm f/5.6, tripod at seated eye level angled toward the reading nook's "
            "corner where a slim floor lamp stands beside a low Italian chaise. 11am — overcast "
            "cool daylight at 6000K from a high clerestory slot window fills the room with even "
            "flat grey-white light. The lamp (on) adds a precise warm circle of 2700K on the "
            "stone floor beside the chaise. ISO 100, f/8. A faint chromatic halo at the lamp "
            "stem marks the cool-to-warm transition — the camera's honest record.\n\n"
            "The reading corner: a custom floor-to-ceiling built-in bookcase of oiled walnut on "
            "the right wall, shelves intentionally sparse — three books stacked horizontally, one "
            "bronze sculptural object. A low Italian chaise in deep stone-grey bouclé, profile to "
            "the camera. Floor: 900x900 honed Pietra grey limestone, tight-jointed. The floor "
            "lamp: slim powder-coated steel, ivory finish, pivoting half-cone shade casting the "
            "warm circle precisely downward. No person present. Nothing staged beyond what the "
            "room already holds. The warm circle against cool stone is the entire emotional event "
            "— two light temperatures, one room, one scale."
        ),
        "composition_notes": (
            "The glass_morphism_shield device: a small, clean floating pill in the upper-left "
            "corner of the frame. This is the ONLY text in the photo zone above the lower 15%.\n"
            "Pill shape: soft rounded rectangle, about 22% of canvas width. Backing: very light "
            "ivory frost — the bookcase shelves remain visible through it. Gold hairline border. "
            "Position: 6% from the left edge, 8% from the top.\n"
            "Inside the pill (headline ONLY): 'Space that earns its silence.' — italic display "
            "serif, deep charcoal (#2B2420), mixed-case. The room behind the pill remains legible.\n"
            "Photo fills 85%+ of the canvas. The lamp, chaise, bookcase, and floor are undisturbed "
            "above the lower 15%. Let the warm lamp circle against the cool stone be uninterrupted.\n"
            "SINDHUBHAVAN ROAD: HEAVY gold luxury display serif spanning 70%+ of canvas width, "
            "anchored in the lower photo zone where the cool Pietra grey stone floor recedes into "
            "shadow away from the lamp circle. The text sits IN the photo, in the floor's natural "
            "neutral tone — no backing panel. Letterforms are dimensional: embossed quality, "
            "gold with subtle shadow. AHMEDABAD in small tracked geometric gold caps directly below. "
            "These are the ONLY text elements this low in the photo — don't crowd them.\n"
            "Price badge: 'FROM ₹3 CR ONWARDS' — anchored bottom-right of the photo zone, just "
            "above the spec row. Ivory fill, gold hairline border (ivory_warmth palette — no dark "
            "panels). HEAVY display serif in deep charcoal.\n"
            "Sample badge: 'STEP INSIDE — SAMPLE FLAT READY' — compact ivory pill with gold border, "
            "bottom-left of the photo zone above the spec row. Bold geometric sans, charcoal.\n"
            "Spec row at very bottom: slim warm charcoal backing (#2B2420), 7% canvas height. "
            "One compact row: bold tracked ALL CAPS geometric gold — "
            "'4 & 5 BHK RESIDENCES  ·  CLUBCLASS AMENITIES  ·  30+ STOREY TOWER'. Equal weight.\n"
            "Bottom-right corner of full canvas: kept entirely clear — logo compositing zone."
        ),
        "headline":    "Space that earns its silence.",
        "eyebrow":     "",
        "palette_tag": "ivory_warmth",
        "scene_tag":   "study_or_reading_nook_lamp_glow",
        "tone_tag":    "bright_aspirational",
        "recipe_tag":  "the_glass_morphism_shield",
        "logo_corner": "bottom-right",
        "badge_cta":   "SAMPLE FLAT OPEN",
    },
]

# ---------------------------------------------------------------------------
# STEP 1 — dedupe_visual_batch (identical to output_saver.py)
# ---------------------------------------------------------------------------
import json

entries = dedupe_visual_batch(LLM_OUTPUTS)

# STEP 2 — save visual_prompts.json (identical format to output_saver.py)
# This is exactly what output_saver.save_for_review() writes to disk.
vp_path = Path(__file__).parent / "anamika_heights_visual_prompts_s36.json"
vp_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

# STEP 3 — process each entry exactly as visuals.py /generate-images does:
#   - extract entry_brief the same way _brief_for_sanitizer() does
#   - look up variant meta the same way _get_variant_meta() does
#   - call build_ad_prompt() + sanitize_image_prompt() with assembled=True
def _brief_for_sanitizer(brief: dict) -> dict:
    """Mirror of visuals._brief_for_sanitizer — exact same field extraction."""
    return {
        "locality":               brief.get("locality", ""),
        "city":                   brief.get("city", ""),
        "property_type":          brief.get("property_type", ""),
        "price_cr":               str(brief.get("price_cr", "")).strip(),
        "sample_ready":           bool(brief.get("sample_ready", False)),
        "rera_verified":          bool(brief.get("rera_verified", False)),
        "verified_awards":        bool(brief.get("verified_awards", False)),
        "verified_certifications":bool(brief.get("verified_certifications", False)),
        "verified_landmarks":     bool(brief.get("verified_landmarks", False)),
        "config":                 brief.get("config", ""),
        "usps":                   brief.get("usps", []),
        "property_name":          brief.get("property_name", ""),
    }

sanitizer_brief = _brief_for_sanitizer(BRIEF)

output_lines = []
output_lines.append("=" * 70)
output_lines.append("ANAMIKA HEIGHTS — IDEOGRAM PROMPTS (SESSION 36)")
output_lines.append("Pipeline: visual_prompts.json → build_ad_prompt → sanitize")
output_lines.append("=" * 70)

for entry in entries:
    vk = entry["variant_key"]
    n  = entry["prompt_num"]

    # Mirror of visuals.py lines 181-190: build entry_brief with variant CTA
    entry_brief = dict(sanitizer_brief)
    try:
        vm = get_variant_meta(vk)
        cta = vm.get("sample_ready_cta")
        if cta:
            entry_brief["sample_ready_cta"] = cta
    except Exception:
        pass

    # Mirror of visuals.py line 205-206: scene_prose present → composition-driven path
    gen_entry = dict(entry)
    final = build_ad_prompt(gen_entry, entry_brief, vk)
    final = sanitize_image_prompt(final, entry_brief, assembled=True)

    output_lines.append("")
    output_lines.append("-" * 70)
    output_lines.append(f"PROMPT {n} — {vk.upper().replace('_', ' ')}")
    output_lines.append(f"  palette={entry['palette_tag']}  recipe={entry['recipe_tag']}")
    output_lines.append(f"  scene={entry['scene_tag']}  tone={entry['tone_tag']}")
    output_lines.append(f"  headline='{entry['headline']}'")
    output_lines.append("-" * 70)
    output_lines.append(final)

output_lines.append("")
output_lines.append("=" * 70)

out_path = Path(__file__).parent / "anamika_heights_prompts_s36.txt"
out_path.write_text("\n".join(output_lines), encoding="utf-8")

print(f"\nDone. {len(entries)} prompts written to: {out_path}")
print(f"      visual_prompts.json written to:  {vp_path}")
for e in entries:
    print(f"  [{e['prompt_num']}] {e['variant_key']:35s}  "
          f"palette={e['palette_tag']:15s}  recipe={e['recipe_tag']}")
