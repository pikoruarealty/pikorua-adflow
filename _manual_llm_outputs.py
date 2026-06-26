"""
Simulates ContentCrew + Visual Prompter pipeline for Anamika Heights.
Correct variant lineup: lifestyle_private_retreat / lifestyle_social_home /
lifestyle_dynamic_a / lifestyle_dynamic_b / interior_signature_moment.
(lifestyle_city_connection and exterior_establishing_shot removed — city-view
and exterior shots are no longer part of the default batch.)

PIPELINE CHANGES:
  - ads_layout_analysis.json patterns inform recipe selection
  - the_sky_text_canvas: sky-text-only recipe (upper zone holds all text)
  - Disabled recipes (the_depth_integration, the_physical_3d_intrusion,
    the_sky_chandelier, the_dark_water_canvas, the_zenith_gaze,
    the_backlit_silhouette) filtered at _RECIPES_BY_NAME load time
  - task_composer.py: recipe list shuffled per run for variety
  - dedupe_visual_batch: guarantees 5 distinct palettes + recipes per batch

RECIPE VARIETY ACROSS RUNS:
  Each variant has a RECIPE_POOL with 2 configs. The script randomly picks one
  per variant each run, then dedupe_visual_batch() resolves any conflicts.
  Re-run to get a different combination.

Run:  python _manual_llm_outputs.py
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from pikorua_adflow.crews.content_crew.task_composer import dedupe_visual_batch, get_variant_meta
from pikorua_adflow.api.services.image_service import build_ad_prompt, sanitize_image_prompt

# ---------------------------------------------------------------------------
BRIEF = {
    "property_name":           "Anamika Heights",
    "property_type":           "Apartment",
    "locality":                "Sindhubhavan Road",
    "city":                    "Ahmedabad",
    "price_cr":                "3",
    "config":                  "4 & 5 BHK",
    "sample_ready":            True,
    "usps":                    ["Clubclass Amenities / 3,300–6,100 sq ft"],
    "standout_feature": (
        "4 & 5 BHK residences from 3,300 to 6,100 sq ft on Sindhubhavan Road, "
        "Ahmedabad. 30+ storey tower. Clubclass amenities. 100% cheque only."
    ),
    "rera_verified":           False,
    "verified_awards":         False,
    "verified_certifications": False,
    "verified_landmarks":      False,
}

# ---------------------------------------------------------------------------
# RECIPE POOLS — 2 complete configs per variant.
# Pool-A = primary configuration; Pool-B = alternate recipe energy + palette.
# ---------------------------------------------------------------------------

VARIANT_POOLS = {

    # =========================================================================
    # VARIANT 1 — Lifestyle / Private Retreat
    # scene: master bedroom OR dressing room, solo figure or empty, cool morning
    #
    # POOL-A: PHOTO-FIRST layout — full-bleed photo (78%), location text on the
    #         natural dark walnut wall (flat overlay), dark navy footer strip (22%)
    #         holds BHK + price + sqft. No top band. Different from V2.
    #
    # POOL-B: EDITORIAL TRIPTYCH — slate top band holds location + headline ONLY
    #         (no BHK in top band). BHK lives exclusively in the footer.
    # =========================================================================
    "lifestyle_private_retreat": [
        {   # POOL-A — master bedroom, photo-first + navy footer strip
            "variant_key":  "lifestyle_private_retreat",
            "prompt_num":   1,
            "scene_prose": (
                "Sony A7R V, 35mm f/2.0, tripod at chest height positioned at the foot "
                "of the master bed, framing the 180-degree corner glazing bay ahead. "
                "7:15am — cool morning daylight at 5600K streams through east and south "
                "faces of the glazing, crisp and shadowless, rendering marble veining with "
                "near clinical precision. ISO 100, f/5.6. Faint lens haze from the "
                "north-facing glazing pane.\n\n"
                "The master bedroom spans 26 feet across the corner. A long low upholstered "
                "window bench in stone-grey linen sits across the full glazing bay — a woman "
                "in a cream cashmere wrap sits at the far right end, facing outward, bare "
                "feet tucked under her, coffee cup resting on the marble sill. "
                "Floor: 1200x2400 Calacatta Viola marble — pale lilac veining on warm "
                "white ground. The left-side wall to the window bay is dark oiled walnut "
                "cladding, floor to 3.2m ceiling — no hardware, no shelving, a single "
                "deep-toned expanse. The room is quiet and unhurried."
            ),
            "composition_notes": (
                "PHOTO-FIRST layout. The photo fills the upper 78% of the canvas completely "
                "— no top band, no overlay panel. All address text sits on the dark walnut "
                "wall that the scene was composed to provide.\n"
                "SINDHUBHAVAN: overlaid as flat HEAVY gold luxury display serif floating in "
                "front of the dark walnut wall — NOT embedded in or carved into it. "
                "One word filling the panel width, upper portion of the wall. "
                "Each letter individually legible at arm's length.\n"
                "ROAD: immediately below at the same weight and scale. Two-line address stack.\n"
                "AHMEDABAD: below ROAD in small tracked geometric caps, cream. "
                "Quiet confirmation.\n"
                "Headline 'A morning worth waking for.': italic display serif, warm white, "
                "below AHMEDABAD — at 35% of SINDHUBHAVAN's cap height. Clear, confident.\n"
                "Badge = DECORATIVE PLAQUE: positioned mid-frame on the right side of the "
                "photo zone where the bright marble and glazing create natural negative space. "
                "Solid ivory backing (#F5EDD8), outer gold hairline, inner gold hairline. "
                "Badge text: 'SAMPLE FLAT READY' only. Wide, prominent — never a small stamp.\n"
                "FOOTER STRIP (lower 22%): solid dark navy (#0D1B2A), full canvas width. "
                "Three equal columns separated by thin gold vertical hairlines:\n"
                "LEFT: '4 & 5 BHK' in BOLD cream geometric caps with fine gold icon above — "
                "PRIMARY BUYING INFORMATION. Optically equal weight to the price.\n"
                "CENTRE: 'STARTING AT' (small tracked cream) / '₹3 Cr' (dominant, 2.5× the "
                "label cap height, gold) / 'ONWARDS' (small tracked cream) stacked.\n"
                "RIGHT: '3,300–6,100 SQ FT' in cream tracked caps with fine gold icon above.\n"
                "All footer text clearly readable against the dark navy — never faint.\n"
                "Top-right corner of the photo zone: clear for logo compositing."
            ),
            "headline":    "A morning worth waking for.",
            "eyebrow":     "",
            "palette_tag": "navy_gold",
            "scene_tag":   "master_bedroom_cool_morning_window",
            "tone_tag":    "bright_aspirational",
            "recipe_tag":  "the_open_room_anchor",
            "logo_corner": "top-right",
            "badge_cta":   "SAMPLE FLAT READY",
        },
        {   # POOL-B — dressing room diagonal, editorial triptych, BHK only in footer
            "variant_key":  "lifestyle_private_retreat",
            "prompt_num":   1,
            "scene_prose": (
                "Sony A7R V, 24mm f/4.0, tripod at 90cm height positioned in the threshold "
                "between dressing room and master bedroom. 8:30am — crisp neutral daylight "
                "at 5200K, slightly diffused by high cloud, fills the west-facing window at "
                "the far end. ISO 200, f/8. Fine grain from a subtle push in the shadows.\n\n"
                "The shot frames the entire 40-foot master suite in one continuous diagonal "
                "recession — dressing room floor in the near foreground giving way to bedroom "
                "floor, then window beyond. Floor: 1200x2400 Bianco Sivec marble, book-matched. "
                "The dressing room has a floor-to-ceiling dark walnut joinery wall on the left "
                "third of the frame — brass handles, no labels, absolute stillness. "
                "No people. One slim orchid on the windowsill at the far end — the single "
                "human gesture in the entire composition. The room communicates scale before "
                "anything is read."
            ),
            "composition_notes": (
                "EDITORIAL TRIPTYCH structure. Three distinct horizontal zones.\n"
                "TOP BAND (upper 30% of canvas): solid dark slate backing (#1E2430). "
                "Photography does NOT enter this zone.\n"
                "Tier 1: a small symmetrical gold botanical ornament centred near the top — "
                "fine line-art, bilateral symmetry, NOT text.\n"
                "Tier 2: 'SINDHUBHAVAN' in HEAVY display serif filling 82% of canvas width — "
                "natural letterform proportions, never condensed. Gold (#C9A84C). "
                "Each letter individually legible. This is the primary typographic event.\n"
                "Tier 3: a thin gold horizontal hairline rule spanning 60% of canvas width, centred.\n"
                "Tier 4: 'ROAD' in HEAVY display serif at 55% of SINDHUBHAVAN's cap height, centred.\n"
                "Tier 5: 'AHMEDABAD' in tracked geometric all-caps with thin gold vertical "
                "hairlines flanking both sides (| AHMEDABAD | style), centred.\n"
                "Tier 6: 'A morning worth waking for.' in italic display serif, warm cream, "
                "centred — at 35% of SINDHUBHAVAN's cap height. Campaign tagline, its own tier.\n"
                "ZONE BOUNDARY: soft gradient 2-3% where the slate top band fades into the photo.\n"
                "PHOTO ZONE (middle 46%): the dressing room diagonal scene, full-bleed, "
                "edge-to-edge. NO text here EXCEPT the sample badge.\n"
                "Badge = DECORATIVE PLAQUE: solid slate backing (#1E2430), outer gold hairline, "
                "inner gold hairline (double-frame). Badge text: 'SAMPLE FLAT READY' only. "
                "Centred, wide, prominent — never a small stamp.\n"
                "FOOTER STRIP (lower 24%): solid warm cream (#F5EDD8). Three equal columns "
                "separated by thin gold vertical hairlines:\n"
                "LEFT: '4 & 5 BHK' with fine gold icon above — PRIMARY BUYING INFORMATION. "
                "Bold, same optical weight as the price. NOT a small label.\n"
                "CENTRE: 'STARTING AT' (small tracked) / '₹3 Cr' (dominant, 2.5× label cap "
                "height, gold) / 'ONWARDS' (small tracked) stacked. Price is the hero.\n"
                "RIGHT: '3,300–6,100 SQ FT' with fine gold icon above.\n"
                "All footer text in deep charcoal (#2B2420) — never light text on cream.\n"
                "Top-right corner inside photo zone: clear for logo compositing."
            ),
            "headline":    "A morning worth waking for.",
            "eyebrow":     "",
            "palette_tag": "slate_cream",
            "scene_tag":   "dressing_room_morning_long_diagonal",
            "tone_tag":    "dark_luxury",
            "recipe_tag":  "the_editorial_triptych",
            "logo_corner": "top-right",
            "badge_cta":   "SAMPLE FLAT READY",
        },
    ],

    # =========================================================================
    # VARIANT 2 — Lifestyle / Social Home
    # scene: dining room amber dinner party — 4 figures, city view
    #
    # POOL-A: the_editorial_triptych — dark top band, BHK LARGE (not eyebrow),
    #         botanical ornament, headline as Tier 7 before zone boundary,
    #         badge is pure CTA only (no headline inside), cream footer.
    # POOL-B: the_golden_archway — gold frame overlay, mixed-scale type.
    # =========================================================================
    "lifestyle_social_home": [
        {   # POOL-A  (editorial triptych — fixed BHK + headline placement)
            "variant_key":  "lifestyle_social_home",
            "prompt_num":   2,
            "scene_prose": (
                "Sony A7R V, 50mm f/2.0, tripod at 120cm height positioned at the far "
                "end of the dining room, framing the length of the table. 7:45pm — warm "
                "amber pendant light at 2700K from four matte brass cylindrical pendants "
                "(320mm diameter) hangs above the table; a deep cool blue dusk light at "
                "5500K fills the city-view glazing behind the diners, creating a "
                "warm-cool tension across the frame. ISO 400, f/2.8. Soft lens glow "
                "from the brightest pendant.\n\n"
                "The 5 BHK dining room has a 2.8m wide, 10-seat solid smoked walnut dining "
                "table with turned fluted legs in burnished brass — four people mid-dinner: "
                "two couples, dressed in contemporary luxury (silk blazers, drape "
                "tops, slim-cut trousers). Gestures are mid-sentence, never posed. The "
                "city of Ahmedabad occupies the entire glazing wall behind them — city "
                "lights beginning to appear in the blue dusk. Ceiling: dark charcoal "
                "plaster tray, 3.6m high, with recessed amber cove strip — the ceiling "
                "reads as near-black from this angle. Flooring: 1200x600 Black Galaxy "
                "granite in natural oiled finish."
            ),
            "composition_notes": (
                "EDITORIAL TRIPTYCH structure. Three distinct horizontal zones.\n"
                "TOP BAND (upper 30% of canvas): solid warm charcoal backing (#2B2420). "
                "Photography does NOT enter this zone. Ample breathing room between tiers.\n"
                "Tier 1: a small symmetrical gold botanical ornament centred near the top — "
                "fine line-art, bilateral symmetry, NOT text — a luxury divider motif.\n"
                "Tier 2: 'SINDHUBHAVAN' in HEAVY display serif filling 82% of canvas width — "
                "natural letterform proportions, never condensed. Gold (#C9A84C). Each letter "
                "individually legible. This is the primary typographic event.\n"
                "Tier 3: a thin gold horizontal hairline rule spanning 60% of canvas width, centred.\n"
                "Tier 4: 'ROAD' in HEAVY display serif at 55% of SINDHUBHAVAN's cap height, "
                "centred. Together the two lines read as one address name.\n"
                "Tier 5: 'AHMEDABAD' in tracked geometric all-caps with thin gold vertical "
                "hairlines flanking both sides (| AHMEDABAD | style), centred.\n"
                "Tier 6: 'Full floors. Not floor plans.' in italic display serif, warm cream, "
                "centred — at 35% of SINDHUBHAVAN's cap height. Campaign tagline, its own tier.\n"
                "ZONE BOUNDARY: soft gradient 2-3% height where the charcoal top band fades "
                "into the photo — never a hard cut.\n"
                "PHOTO ZONE (middle 46%): full-bleed dining scene, edge-to-edge. "
                "NO text here EXCEPT the sample badge.\n"
                "Badge = DECORATIVE PLAQUE: solid charcoal backing (#2B2420), outer gold hairline, "
                "inner gold hairline (double-frame), fine gold rules flanking the CTA word. "
                "Badge text: 'SAMPLE FLAT OPEN' only — NO sub-label, NO headline inside. "
                "Centred, wide, prominent.\n"
                "FOOTER STRIP (lower 24%): solid warm cream (#F5EDD8). Three equal columns "
                "separated by thin gold vertical hairlines:\n"
                "LEFT: '4 & 5 BHK' with fine gold icon above — PRIMARY BUYING INFORMATION. "
                "Bold, same optical weight as the price. NOT a small label.\n"
                "CENTRE: 'STARTING AT' (small tracked) / '₹3 Cr' (dominant, 2.5× label cap "
                "height, gold) / 'ONWARDS' (small tracked) stacked. Price is the hero.\n"
                "RIGHT: '3,300–6,100 SQ FT' with fine gold icon above.\n"
                "All footer text in deep charcoal (#2B2420) — never light text on cream.\n"
                "Top-right corner inside photo zone: clear for logo compositing."
            ),
            "headline":    "Full floors. Not floor plans.",
            "eyebrow":     "",
            "palette_tag": "charcoal_gold",
            "scene_tag":   "dining_room_amber_dinner_party",
            "tone_tag":    "dark_luxury",
            "recipe_tag":  "the_editorial_triptych",
            "logo_corner": "top-right",
            "badge_cta":   "SAMPLE FLAT OPEN",
        },
        {   # POOL-B
            "variant_key":  "lifestyle_social_home",
            "prompt_num":   2,
            "scene_prose": (
                "Sony A7R V, 35mm f/2.8, tripod at 100cm height positioned near the "
                "kitchen island looking across into the great room. 6:30pm — bright "
                "even interior daylight at 4500K from west-facing floor-to-ceiling "
                "glazing fills the room laterally; warm pool light from kitchen pendant "
                "clusters adds depth. ISO 320, f/2.8. Subtle softness from the "
                "bright window edge catching the lens.\n\n"
                "The 5 BHK great room + kitchen is 45 feet wide — an open-plan island "
                "kitchen in pale Corian and fluted oak on the right; a modular sofa in "
                "dove-grey bouclé central; three women mid-conversation between the "
                "kitchen and sofa — laughing, candid, unhurried. Wide-plank European "
                "oak flooring in natural oiled finish. The room feels designed for this "
                "kind of gathering — the scale apparent when the figures register as "
                "small against its width."
            ),
            "composition_notes": (
                "The creative device: a THICK GOLD GRAPHIC OVERLAY FRAME on all four canvas "
                "edges — a heavy gold hairline border running edge to edge. Interior corners: "
                "ornate line-art corner rosettes (fine lines, NOT filled blocks). The frame "
                "IS the structural container for all text.\n"
                "Upper frame zone: 'SINDHUBHAVAN ROAD' in BOLD display sans at maximum canvas "
                "width — edge to edge across the upper frame bar. Gold (#C9A84C). This is "
                "the dominant typographic element.\n"
                "Below: '4 & 5 BHK RESIDENCES' in italic mixed-scale display serif at 60% "
                "the location name's cap height, warm white — crossing from the frame zone "
                "into the photo, the scale contrast creates the visual drama.\n"
                "Below: campaign headline 'Full floors. Not floor plans.' in italic serif at "
                "45% the BHK line's cap height, gold — a third typographic layer.\n"
                "Left border: 'AHMEDABAD' in tracked geometric caps, vertical orientation, "
                "gold, running along the left border.\n"
                "Bottom frame zone — three columns, gold vertical hairlines:\n"
                "LEFT: '4 & 5 BHK RESIDENCES', warm white tracked caps — PRIMARY BUYING INFO.\n"
                "CENTRE: '₹3 Cr ONWARDS' — gold, HEAVY serif, dominant (2× column label size).\n"
                "RIGHT: '3,300–6,100 SQ FT', warm white tracked caps.\n"
                "Badge 'SAMPLE FLAT READY': compact gold-bordered pill, upper-right inside "
                "the frame, below the location name text. Dark backing, bold geometric sans. "
                "3 words, prominent.\n"
                "The photo inside the frame: completely uncluttered — the frame holds all text.\n"
                "Top-left corner inside gold frame: clear for logo compositing."
            ),
            "headline":    "Full floors. Not floor plans.",
            "eyebrow":     "",
            "palette_tag": "burgundy_gold",
            "scene_tag":   "great_room_kitchen_bright_afternoon",
            "tone_tag":    "bright_aspirational",
            "recipe_tag":  "the_golden_archway",
            "logo_corner": "top-left",
            "badge_cta":   "SAMPLE FLAT READY",
        },
    ],

    # =========================================================================
    # VARIANT 3 — Lifestyle / Dynamic Scene A
    # scene: family living room sunday afternoon
    # brief: couple + child, open-plan living room, cool Sunday daylight,
    #        dark basalt feature wall = natural text anchor.
    #
    # POOL-A: the_open_room_anchor — location name on basalt wall, floating
    #         pill headline, compact spec row. ivory_warmth palette.
    # POOL-B: the_golden_archway — gold frame overlay device, entire photo
    #         uncluttered inside. burgundy_gold palette.
    # =========================================================================
    "lifestyle_dynamic_a": [
        {   # POOL-A
            "variant_key":  "lifestyle_dynamic_a",
            "prompt_num":   3,
            "scene_prose": (
                "Sony A7R V, 35mm f/2.8, tripod at 105cm height positioned in the far "
                "right corner of the 5 BHK living room, framing the open-plan width. "
                "11:00am — cool even daylight at 4900K from north-facing floor-to-ceiling "
                "glazing, no direct sun, fills the room with a soft even wash that renders "
                "marble veining with near clinical clarity. ISO 200, f/5.6. Faint chromatic "
                "fringe at the far glazing corner pane.\n\n"
                "The 5 BHK living room stretches 40 feet across. A modular sectional sofa "
                "in dove-grey bouclé holds a couple and one child (approximately 7) in an "
                "unhurried Sunday moment — the child on the floor beside the sofa, adults "
                "with books and coffee. Floor: 1200×2400 Arabescato Corchia marble, highly "
                "polished. Ceiling void at 4.0m with a thin brass linear pendant track above "
                "the seating group. Left wall: flat deep charcoal basalt tile, floor to "
                "ceiling, no hardware — a single tonally dark expanse in the even daylight."
            ),
            "composition_notes": (
                "The deep charcoal basalt left wall, floor-to-ceiling, is the primary text "
                "zone — the scene's highest-contrast natural surface. All location text sits "
                "here as a FLAT OVERLAY — not embedded in or carved into the wall surface.\n"
                "SINDHUBHAVAN: overlaid as flat HEAVY or BLACK weight gold luxury display serif "
                "floating in front of the basalt panel. One word filling the full panel width — "
                "scale so each letter is individually legible at arm's length.\n"
                "ROAD: immediately below, same weight, same gold — second line at the same "
                "scale. The two-line stack IS the primary typographic event.\n"
                "AHMEDABAD: below ROAD in small tracked geometric caps, gold. Quiet confirmation.\n"
                "'4 & 5 BHK': below AHMEDABAD in HEAVY display serif italic, gold — same "
                "typeface family as the location name, NOT geometric sans. At least 55% of "
                "SINDHUBHAVAN's cap height. Large enough to read across a room.\n"
                "Headline pill: upper-right corner of canvas — compact semi-transparent pill "
                "(#2B2420 fill, 65% opacity, gold hairline border). Inside: 'The home that "
                "fits your whole life.' in italic mixed-case serif, warm white. Pill sized to "
                "the text only, never a sidebar.\n"
                "Price: compact warm charcoal pill (#2B2420, gold hairline), bottom-centre of "
                "the photo zone. '₹3 CR ONWARDS' in HEAVY display serif, gold. Unmissable.\n"
                "Badge 'SAMPLE FLAT READY': bottom-left of photo zone. Compact rectangular "
                "stamp, charcoal fill, gold hairline border. Bold geometric sans. 3 words.\n"
                "Spec strip: single slim strip at very bottom (7% canvas height), dark charcoal "
                "backing, full width. 'CLUBCLASS AMENITIES  ·  3,300–6,100 SQ FT' "
                "in tracked geometric caps, warm white. Two items, centred, centre-dot separator.\n"
                "The marble floor, sofa group, and glazing wall: completely clear of all text.\n"
                "Bottom-right corner: clear for logo compositing."
            ),
            "headline":    "The home that fits your whole life.",
            "eyebrow":     "",
            "palette_tag": "ivory_warmth",
            "scene_tag":   "family_living_room_sunday_afternoon",
            "tone_tag":    "bright_aspirational",
            "recipe_tag":  "the_open_room_anchor",
            "logo_corner": "bottom-right",
            "badge_cta":   "SAMPLE FLAT READY",
        },
        {   # POOL-B
            "variant_key":  "lifestyle_dynamic_a",
            "prompt_num":   3,
            "scene_prose": (
                "Sony A7R V, 24mm f/4.0, tripod at 95cm height at the open kitchen-to-"
                "living-room threshold, facing the 5 BHK living room width. 12:30pm — "
                "bright even overhead daylight at 5000K through north-west facing glazing "
                "fills the room with near-uniform clean light. ISO 160, f/5.6. Subtle "
                "softening at the glazing edge where the lens catches the bright sky beyond.\n\n"
                "The scene frames the entire 40-foot living room in one wide shot — the sofa "
                "group mid-left, a couple and two children (~5 and ~9) in an after-lunch "
                "moment: one child drawing on the marble floor, the other settled on a "
                "parent's lap. Floor: 1200×2400 Calacatta Gold marble, book-matched, the "
                "gold veining rendered clearly in the neutral midday light. The glazing wall "
                "at the far end reveals a rooftop garden beyond. Ceiling: pale plaster at "
                "4.0m, two recessed pendant clusters in warm brass. The room feels designed "
                "for exactly this kind of gathering."
            ),
            "composition_notes": (
                "The creative device: a THICK GOLD GRAPHIC OVERLAY FRAME on all four canvas "
                "edges — a heavy gold hairline border running edge to edge. Interior corners: "
                "ornate line-art corner rosettes (fine lines, NOT filled blocks). The frame "
                "IS the structural container for all text. The photo inside is uncluttered.\n"
                "Upper frame zone: 'SINDHUBHAVAN ROAD' in BOLD display sans at maximum canvas "
                "width — spanning the full upper frame bar. Gold (#C9A84C). This is the "
                "dominant typographic element.\n"
                "Below: '4 & 5 BHK RESIDENCES' in italic mixed-scale display serif at 60% "
                "the location name's cap height, warm white — crossing from the frame zone "
                "into the photo, the scale contrast creates the visual drama.\n"
                "Below: campaign headline in italic serif at 45% the BHK line, gold.\n"
                "Left border: 'AHMEDABAD' in tracked geometric caps, vertical orientation, "
                "gold, running along the left border.\n"
                "Bottom frame zone — three equal columns, gold hairlines:\n"
                "LEFT: '4 & 5 BHK RESIDENCES', warm white tracked caps — PRIMARY BUYING INFO.\n"
                "CENTRE: '₹3 Cr ONWARDS' — gold, HEAVY serif, dominant.\n"
                "RIGHT: '3,300–6,100 SQ FT', warm white tracked caps.\n"
                "Badge 'SAMPLE FLAT READY': compact gold-bordered pill inside the frame, "
                "upper-left below the location name. Dark backing, bold geometric sans. Prominent.\n"
                "Top-right corner inside frame: clear for logo compositing."
            ),
            "headline":    "The home that fits your whole life.",
            "eyebrow":     "",
            "palette_tag": "burgundy_gold",
            "scene_tag":   "family_living_room_bright_midday",
            "tone_tag":    "bright_aspirational",
            "recipe_tag":  "the_golden_archway",
            "logo_corner": "top-right",
            "badge_cta":   "SAMPLE FLAT READY",
        },
    ],

    # =========================================================================
    # VARIANT 4 — Lifestyle / Dynamic Scene B
    # scene: luxury kitchen — evening (POOL-A) / morning (POOL-B)
    # brief: domestic interior moment, warm kitchen, aspirational but lived-in.
    # No balcony, no city view, no exterior.
    #
    # POOL-A: the_zoned_triptych — dark top band (kitchen ceiling), photo zone,
    #         cream footer. Headline as Tier 7 in top band. charcoal_gold.
    # POOL-B: the_glass_morphism_shield — floating pill, location name on dark
    #         cabinet shadow zone, compact spec row. slate_cream.
    # =========================================================================
    "lifestyle_dynamic_b": [
        {   # POOL-A — kitchen evening, the_zoned_triptych
            "variant_key":  "lifestyle_dynamic_b",
            "prompt_num":   4,
            "scene_prose": (
                "Sony A7R V, 50mm f/2.0, tripod at 100cm height positioned at the far end "
                "of the kitchen island, framing the cooking range wall. 7:30pm — warm amber "
                "pendant light at 2700K from three matte black cylindrical pendants (280mm "
                "diameter) above the island creates a pool of warm light; deep shadows fall "
                "beyond the pendant cluster. ISO 640, f/2.0. Soft lens glow from the nearest "
                "pendant directly above.\n\n"
                "The 5 BHK kitchen has an island in Calacatta Paonazzo marble — a woman in a "
                "cream silk top stands at the six-burner range along the far wall, gesturing "
                "mid-task. Cabinet faces: flat painted deep charcoal matte, push-to-open "
                "hardware, no visible handles. Above the range: a deep dark stone hood. "
                "Floor: 600×600 honed black basalt tile. Open shelving in smoked oak on the "
                "right wall holds ceramics and glassware. The pendant cluster overhead reads "
                "as near-dark from this angle — a natural dark zone for the top band."
            ),
            "composition_notes": (
                "ZONED TRIPTYCH structure. The near-black pendant hood and dark charcoal ceiling "
                "above the island serve as the natural top band backing — no artificial panel.\n"
                "TOP BAND (upper 26–30% of canvas): 'SINDHUBHAVAN' in HEAVY gold display serif "
                "filling 82% of canvas width — not condensed, natural letterform proportions. "
                "'ROAD' on the second line at 55% of SINDHUBHAVAN's cap height, centred. "
                "'AHMEDABAD' below in tracked geometric caps with thin gold vertical hairlines "
                "flanking (| AHMEDABAD | style). "
                "'4 & 5 BHK' in a double-bordered frame box: outer thin gold hairline, 3px gap, "
                "inner gold hairline — text in BOLD cream caps centred inside. Box spans 50% "
                "canvas width, centred. PROMINENT — a primary buying decision, not a footnote.\n"
                "Campaign headline 'Where the kitchen earns its square footage.' as a final tier "
                "below the BHK box, in italic display serif, warm cream, centred — at 30% of "
                "SINDHUBHAVAN's cap height. Just above the gradient zone boundary.\n"
                "ZONE BOUNDARY: soft gradient 2-3% where the dark ceiling fades to the photo.\n"
                "PHOTO ZONE (middle 46%): the kitchen cooking scene, full-bleed, edge-to-edge. "
                "NO text except the badge. Badge = DECORATIVE PLAQUE: charcoal backing (#2B2420), "
                "outer gold hairline, inner gold hairline (double-frame), fine gold rules "
                "flanking the CTA word. 'SAMPLE FLAT OPEN' only — NO sub-label, NO headline "
                "inside the badge. Centred horizontally, anchored mid-frame.\n"
                "FOOTER STRIP (lower 24%): solid warm cream (#F5EDD8). Three equal columns, "
                "thin gold vertical hairlines:\n"
                "LEFT: '4 & 5 BHK' with fine gold icon above — PRIMARY BUYING INFO.\n"
                "CENTRE: 'STARTING AT' (small tracked) / '₹3 Cr' (dominant gold, 2.5× label "
                "cap height) / 'ONWARDS' (small tracked) stacked. Price is the hero.\n"
                "RIGHT: '3,300–6,100 SQ FT' with fine gold icon above.\n"
                "All footer text in deep charcoal (#2B2420) — never light text on cream.\n"
                "Top-right corner: clear for logo compositing."
            ),
            "headline":    "Where the kitchen earns its square footage.",
            "eyebrow":     "",
            "palette_tag": "charcoal_gold",
            "scene_tag":   "kitchen_solo_cooking_evening",
            "tone_tag":    "dark_luxury",
            "recipe_tag":  "the_zoned_triptych",
            "logo_corner": "top-right",
            "badge_cta":   "SAMPLE FLAT OPEN",
        },
        {   # POOL-B — kitchen morning, the_glass_morphism_shield
            "variant_key":  "lifestyle_dynamic_b",
            "prompt_num":   4,
            "scene_prose": (
                "Sony A7R V, 35mm f/2.8, tripod at 95cm height positioned at the kitchen "
                "entry, framing the island and glazing beyond. 9:00am — cool morning daylight "
                "at 5400K streams through east-facing floor-to-ceiling glazing beside the "
                "kitchen, rendering the marble island surface in near-clinical precision. "
                "ISO 100, f/5.6. Fine chromatic fringe at the glazing leading edge.\n\n"
                "The 5 BHK kitchen shows the island full length in the foreground — a man in "
                "a grey linen shirt seated at the island end, reading, untouched coffee at his "
                "elbow. Cabinet faces: flat warm linen matte, frameless. Counter: Calacatta "
                "Viola marble, book-matched. The glazing to the right fills the frame with soft "
                "morning sky — implied trees beyond. Floor: 1200×600 light ivory Bianco Sivec "
                "marble. The shadow side of the left cabinet column falls in deep cool shadow — "
                "a natural high-contrast zone within the otherwise bright kitchen."
            ),
            "composition_notes": (
                "The shadow side of the left cabinet column, where the morning light does not "
                "reach, is the primary text zone — a deep cool-toned dark surface within the "
                "bright kitchen. This is the natural contrast zone the scene creates.\n"
                "Headline glass morphism pill: upper-left corner — compact narrow semi-"
                "transparent pill (#1E2430 fill, 50% opacity, gold hairline border). Inside: "
                "'Where the kitchen earns its square footage.' in bold italic display serif, "
                "warm white. Pill sized to the text only — not a full-column sidebar.\n"
                "SINDHUBHAVAN ROAD: overlaid as flat HEAVY gold display serif floating in front "
                "of the shadow-side cabinet column — NOT embedded in or engraved into the "
                "surface. Two lines: SINDHUBHAVAN spanning the shadow zone width, "
                "ROAD below at the same scale. This is the primary typographic event.\n"
                "AHMEDABAD: below ROAD in small tracked geometric caps, gold.\n"
                "'4 & 5 BHK': below AHMEDABAD in HEAVY display serif italic, gold — same "
                "serif family as the location name. At least 55% of SINDHUBHAVAN's cap height. "
                "NOT geometric sans.\n"
                "Price: compact charcoal pill (#2B2420 backing, gold hairline), anchored "
                "bottom-right of the photo zone. '₹3 CR ONWARDS' in HEAVY serif, gold.\n"
                "Badge 'STEP INSIDE — SAMPLE READY': compact stamp, bottom-centre. Charcoal "
                "fill, gold hairline. Bold geometric sans. 4 words.\n"
                "Spec strip: single slim strip at very bottom (7% height), charcoal backing, "
                "full width. 'CLUBCLASS AMENITIES  ·  3,300–6,100 SQ FT' "
                "in tracked geometric caps, warm white.\n"
                "The morning marble counter, the glazing, and the man reading: completely "
                "clear of text.\n"
                "Bottom-left corner: clear for logo compositing."
            ),
            "headline":    "Where the kitchen earns its square footage.",
            "eyebrow":     "",
            "palette_tag": "slate_cream",
            "scene_tag":   "kitchen_morning_solo_coffee",
            "tone_tag":    "bright_aspirational",
            "recipe_tag":  "the_glass_morphism_shield",
            "logo_corner": "bottom-left",
            "badge_cta":   "STEP INSIDE — SAMPLE READY",
        },
    ],

    # =========================================================================
    # VARIANT 5 — Interior Signature Moment
    # scene: empty living room — dramatic light, material showcase.
    # No people. Architecture is the subject.
    #
    # POOL-A: the_glass_morphism_shield — floating pill headline upper-left,
    #         location name on dark basalt wall, compact spec row. navy_gold.
    # POOL-B: the_editorial_triptych — dark top band from natural ceiling,
    #         photo zone (empty room + badge), cream footer. charcoal_gold.
    # =========================================================================
    "interior_signature_moment": [
        {   # POOL-A
            "variant_key":  "interior_signature_moment",
            "prompt_num":   5,
            "scene_prose": (
                "Sony A7R V, 24mm f/8.0, tripod at 100cm height, positioned at the "
                "entry threshold of the 5 BHK living room. 10:30am — overcast cool "
                "daylight at 5500K, perfectly diffused through north-west facing floor-"
                "to-ceiling glazing, rendering a single wide diagonal light shaft across "
                "the room from upper-left to lower-right. ISO 100, f/11. Near-perfect "
                "technical exposure with a faint residual bloom at the brightest glazing "
                "pane corner.\n\n"
                "The 5 BHK living room stretches 42 feet from the entry threshold to "
                "the glazing wall at the far end. Floor: 1200x2400 Calacatta Gold marble, "
                "book-matched — the light shaft rakes across the veined surface, creating "
                "a dramatic bright diagonal stripe across the dark ground. No furniture "
                "anywhere — completely empty. The left wall: smooth dark basalt tile, "
                "floor to 3.8m ceiling, no hardware. The right wall: plaster, in full "
                "shadow. The diagonal light shaft on the marble is the entire emotional "
                "event. Through the glazing at the far end: overcast Ahmedabad sky."
            ),
            "composition_notes": (
                "The left dark basalt wall and the shadow pool on the right side of the "
                "room are the text zones — both naturally dark, high-contrast surfaces.\n"
                "Headline glass morphism pill: upper-left corner of canvas. A compact "
                "semi-transparent rounded pill with a delicate gold hairline border and "
                "a very slightly frosted dark backing (45% opacity, #1E2430). Inside: "
                "'100% Cheque. 0% Compromise.' in bold italic display serif, warm white. "
                "Pill sized exactly to the text — never a sidebar or column.\n"
                "'SINDHUBHAVAN ROAD': overlaid as flat HEAVY gold luxury display serif floating "
                "in front of the dark basalt left wall — NOT carved into or embedded in the "
                "wall surface. SINDHUBHAVAN spans the full basalt zone width; ROAD on the "
                "line below at the same scale. Clean flat typographic layer, independent of "
                "the wall geometry — the primary typographic event.\n"
                "'AHMEDABAD': below ROAD, small tracked geometric caps, gold.\n"
                "'4 & 5 BHK': italic tracked HEAVY serif, gold, below AHMEDABAD — same "
                "typographic stack floating in front of the dark basalt wall. NOT geometric sans.\n"
                "Price: compact navy pill (#0D1B2A fill, gold hairline) anchored in the "
                "bottom-right of the shadow zone on the right side of the room. "
                "'₹3 CR ONWARDS' in HEAVY serif, gold. Unmissable.\n"
                "Badge 'STEP INSIDE — SAMPLE READY': compact wide pill, bottom centre "
                "of the photo zone. Slate-grey fill, gold border. Bold geometric sans. "
                "4 words only.\n"
                "Spec strip: single slim strip at very bottom of canvas (8% height), "
                "dark navy backing (#0D1B2A), full width. Text: "
                "'CLUBCLASS AMENITIES  ·  3,300–6,100 SQ FT' — tracked caps, gold.\n"
                "The marble floor diagonal and the glazing wall beyond: completely clear.\n"
                "Top-right corner: clear for logo compositing."
            ),
            "headline":    "100% Cheque. 0% Compromise.",
            "eyebrow":     "",
            "palette_tag": "navy_gold",
            "scene_tag":   "living_room_diagonal_daylight",
            "tone_tag":    "bright_aspirational",
            "recipe_tag":  "the_glass_morphism_shield",
            "logo_corner": "top-right",
            "badge_cta":   "STEP INSIDE — SAMPLE READY",
        },
        {   # POOL-B
            "variant_key":  "interior_signature_moment",
            "prompt_num":   5,
            "scene_prose": (
                "Sony A7R V, 24mm f/8.0, tripod at 90cm height at the midpoint of the "
                "5 BHK living room, framing the far glazing wall and its city view. "
                "5:45pm — warm dusk amber at 3200K enters from below the overcast, "
                "a 15-minute window of directional warm light raking across the marble "
                "at a low angle. ISO 200, f/8. Slight warm veil from the hazy dusk light.\n\n"
                "The room is entirely empty — marble, walls, glazing, and dusk light. "
                "Floor: 1200x2400 Calacatta Oro marble — the dusk light renders the "
                "gold veining near luminous. The glazing at the far end is full-height, "
                "frameless, and shows the dusk Ahmedabad skyline, amber haze at the "
                "horizon. The near ceiling at 3.8m has a dark coffered plaster grid — "
                "near-black from this angle, a naturally dark upper zone."
            ),
            "composition_notes": (
                "EDITORIAL TRIPTYCH structure. The natural dark coffered ceiling is the "
                "backing for the top band — near-black, clearly delineated from the photo zone.\n"
                "TOP BAND (upper 26% of canvas): 'SINDHUBHAVAN' fills this band in HEAVY "
                "gold display serif at 82% canvas width — not condensed, natural letterform "
                "proportions. 'ROAD' on a second line at 55% of SINDHUBHAVAN's cap height, "
                "centred. 'AHMEDABAD' below in tracked geometric caps with gold vertical "
                "hairlines flanking both sides (| AHMEDABAD | style). "
                "'4 & 5 BHK' in a double-bordered frame box: outer thin gold hairline, "
                "3px gap, inner gold hairline, text in BOLD cream caps centred inside. "
                "Box spans 50% of canvas width, centred. PROMINENT — primary buying info.\n"
                "Campaign headline 'Not just bigger. Properly big.' as final tier, italic "
                "display serif, warm cream, at 30% of SINDHUBHAVAN's cap height.\n"
                "Gradient zone boundary: soft fade into the photo — never a hard cut.\n"
                "PHOTO ZONE (middle 50%): the empty dusk room, edge-to-edge. "
                "NO text here EXCEPT sample badge. Badge = DECORATIVE PLAQUE: dark "
                "charcoal backing, outer gold hairline, inner gold hairline, fine gold "
                "rules above and below the CTA word. 'SAMPLE FLAT OPEN' only — no "
                "sub-label inside the badge. Centred, prominent.\n"
                "FOOTER STRIP (lower 24%): solid warm cream (#F5EDD8). Three equal columns "
                "with thin gold vertical hairlines:\n"
                "LEFT: '4 & 5 BHK' with a fine gold icon above — PRIMARY BUYING INFO.\n"
                "CENTRE: 'STARTING AT' (small tracked) / '₹3 Cr' (dominant gold) / 'ONWARDS'.\n"
                "RIGHT: '3,300–6,100 SQ FT' with a fine gold icon above.\n"
                "All footer text in deep charcoal (#2B2420).\n"
                "Top-right corner: clear for logo compositing."
            ),
            "headline":    "Not just bigger. Properly big.",
            "eyebrow":     "",
            "palette_tag": "charcoal_gold",
            "scene_tag":   "living_room_dusk_diagonal_light",
            "tone_tag":    "dark_luxury",
            "recipe_tag":  "the_editorial_triptych",
            "logo_corner": "top-right",
            "badge_cta":   "SAMPLE FLAT — VISIT TODAY",
        },
    ],
}

# ---------------------------------------------------------------------------
# SELECT ONE CONFIG PER VARIANT — randomly picks POOL-A or POOL-B for each,
# then dedupe_visual_batch() resolves any residual palette/recipe collisions.
# Re-run the script to get a different combination.
# ---------------------------------------------------------------------------
LLM_OUTPUTS = []
for vk, pool in VARIANT_POOLS.items():
    pick = random.choice(pool)
    LLM_OUTPUTS.append(pick)

# Assign sequential prompt numbers after random selection
for i, entry in enumerate(LLM_OUTPUTS, 1):
    entry["prompt_num"] = i

# Deduplicate palettes and recipes across the batch
entries = dedupe_visual_batch(LLM_OUTPUTS)

# ---------------------------------------------------------------------------
output_lines = []
output_lines.append("=" * 70)
output_lines.append("ANAMIKA HEIGHTS - IDEOGRAM PROMPTS")
output_lines.append("Variants: private_retreat / social_home / dynamic_a / dynamic_b / interior")
output_lines.append("=" * 70)

for entry in entries:
    vk = entry["variant_key"]
    n  = entry["prompt_num"]

    meta = get_variant_meta(vk)
    cta_brief = dict(BRIEF)
    cta_brief["sample_ready_cta"] = entry.get("badge_cta") or meta.get("sample_ready_cta", "")

    final = build_ad_prompt(entry, cta_brief, vk)
    final = sanitize_image_prompt(final, cta_brief, assembled=True)

    output_lines.append("")
    output_lines.append("-" * 70)
    output_lines.append(f"PROMPT {n} - {vk.upper().replace('_', ' ')}")
    output_lines.append(f"  palette={entry['palette_tag']}  recipe={entry['recipe_tag']}")
    output_lines.append(f"  scene={entry['scene_tag']}  tone={entry['tone_tag']}")
    output_lines.append(f"  headline='{entry['headline']}'")
    output_lines.append("-" * 70)
    output_lines.append(final)

output_lines.append("")
output_lines.append("=" * 70)

out_path = Path(__file__).parent / "anamika_heights_prompts.txt"
out_path.write_text("\n".join(output_lines), encoding="utf-8")

print(f"Done. {len(entries)} prompts written to: {out_path}")
for e in entries:
    print(f"  [{e['prompt_num']}] {e['variant_key']:35s}  "
          f"palette={e['palette_tag']:15s}  recipe={e['recipe_tag']}")
