"""
Fresh Anamika Heights prompts — Session 39 (distinct-skeleton + dynamic-footer rewrite).

Why this batch was redesigned (carried over from Session 38's render feedback):
  - The previous batch converged on ONE samey skeleton (location top -> BHK side
    block -> headline -> tiny price bottom-right -> footer strip) across all five.
  - Price & CTA rendered tiny because the composition_notes literally said
    "compact pill" — concrete prose beats every abstract size guard downstream.
  - The footer never filled because the prose said "two items"; code-level
    padding never reaches the image model. The prose is the law.
  - Too much default golden light made the batch read cheap and uniform.

What this batch fixes (each authored into the composition_notes prose itself):
  - DISTINCT SKELETON per variant — each pins a genuinely different element
    arrangement: location anchored in a different zone every time (foreground
    floor / arch band / top band / left column / sky band), price & CTA in
    different positions per scene.
  - PRICE & CTA always PROMINENT — never "compact" as a size cue. Backing AND
    placement decided by the surface (solid pill on a dark/busy area; bold text
    directly on a light/clear surface with no box). Both always inside the
    central 70-80% focus area, never jammed in a corner.
  - DYNAMIC FOOTER — none / text-strip / icon-grid / distributed-pair /
    distributed-line across the five. When a STRIP or GRID is used it carries
    THREE balanced items (fill with GATED COMMUNITY); a floating/distributed
    line keeps two and is never padded.
  - BHK is a STANDALONE LARGE photo-zone element, never a footer label.
  - LIGHT DIVERSITY — only V2 is warm (evening hosting). V1 cool morning,
    V3 cool overhead daylight, V4 cool bright morning, V5 blue-hour night.
  - INTENTIONAL SPACING — every element keeps a clear margin; nothing touches.
  - Brand-new scenes vs the prior batch (no living-room/kitchen/foyer/study/bath
    repeats): open-plan dining lounge, evening tasting table, overhead living
    lounge, panelled reading nook silhouette, corner blue-hour lounge.

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
    "usps":                    ["Clubclass Amenities", "3,300–6,100 sq ft"],
    "standout_feature": (
        "4 & 5 BHK residences from 3,300 to 6,100 sq ft on Sindhubhavan Road, "
        "Ahmedabad. Clubclass amenities."
    ),
    "rera_verified":           False,
    "verified_awards":         False,
    "verified_certifications": False,
    "verified_landmarks":      False,
}

LLM_OUTPUTS = [

    # =========================================================================
    # VARIANT 1 — Lifestyle / Private Retreat
    # NEW scene: open-plan dining-and-lounge, bright COOL morning
    # Skeleton: location LOW in dark foreground floor; headline pill upper-left;
    #           price as bold text on light surface; CTA pill on bright glazing;
    #           footer = single floating gold line (NO strip).
    # Palette: charcoal_gold   Recipe: the_open_room_anchor
    # =========================================================================
    {
        "variant_key":  "lifestyle_private_retreat",
        "prompt_num":   1,
        "scene_prose": (
            "Sony A7R V, 24mm f/4, tripod at 115cm at the threshold of an open-plan "
            "dining-and-lounge running the full depth of the apartment toward a wall of glazing. "
            "8:20am — bright, cool morning daylight pours straight across the room; clean even "
            "illumination, soft long shadows, absolutely no amber. ISO 200, f/4. The space reads "
            "airy and full of light, scale and calm established before comfort.\n\n"
            "A 3.4m travertine dining table runs down the centre with eight sculptural oak chairs, "
            "a low linen runner and a tall branch arrangement. To the left an open lounge: a deep "
            "oatmeal sectional, a bouclé armchair, stacked art books on a stone plinth. A woman in "
            "soft grey cashmere stands at a marble sideboard pouring coffee, unhurried, in profile. "
            "Floor: wide fumed-oak herringbone, warm mid-brown, falling into deep shadow across the "
            "foreground where the light drops off. Full-height glazing frames a green podium garden "
            "beyond. Every surface carries material — the frame is layered, calm, completely full."
        ),
        "composition_notes": (
            "PEOPLE DO NOT DISPLACE TEXT: the woman at the sideboard is a scene element only — all "
            "text below is mandatory at full prominent size, worked around her using the room's "
            "negative space.\n"
            "'SINDHUBHAVAN' is one unbroken 12-character word (S-I-N-D-H-U-B-H-A-V-A-N) — no "
            "internal slash, hyphen, space, or split.\n"
            "DISTINCT SKELETON — this variant anchors the location LOW in the foreground floor. "
            "The open_room_anchor headline pill: a floating rounded rectangle anchored UPPER-LEFT, "
            "6% from the left, 7% from the top, with a clear 4% margin from every edge. Backing: "
            "lightest charcoal frost, barely opaque, the room visible behind it; thin gold "
            "hairline. Inside (headline ONLY): 'Mornings were made for rooms like this.' in bold "
            "italic display serif, deep charcoal (#2B2420), mixed-case.\n"
            "SINDHUBHAVAN ROAD: HEAVY warm-gold luxury display serif spanning 80% of canvas width, "
            "anchored in the DARK FOREGROUND-FLOOR ZONE across the lower third — the fumed-oak "
            "herringbone falls dark enough here to carry the letterforms with no backing. "
            "MONUMENTAL — each individual letter legible at arm's length. Flanked by thin gold "
            "editorial hairlines. 'AHMEDABAD' in clearly readable tracked geometric gold caps "
            "directly below in the same floor zone — a confident line, not a faint micro-caption.\n"
            "'4 & 5 BHK' — a STANDALONE LARGE element, NOT part of the location cluster and NOT in "
            "any footer. Place it as a bold typographic event at roughly 46% canvas height on the "
            "RIGHT, against the shadowed wall return beside the glazing. HEAVY gold display serif, "
            "same family as the location name, at roughly 50% of its cap height. A primary selling "
            "point.\n"
            "SCENE NEGATIVE SPACE: frame this open room so the right-hand wall return and the "
            "dark foreground floor stay calm and uncluttered — these zones carry the BHK, price "
            "and CTA. Do NOT stack all three down the right edge; spread them and keep generous "
            "space between each.\n"
            "PRICE & CTA — both LARGE, both high-contrast, both central, clearly separated (not "
            "stacked under the BHK). Price 'FROM ₹3 CR ONWARDS' anchors in the LOWER-RIGHT where "
            "the brown wall meets the dark foreground floor at roughly 62% canvas height — a "
            "naturally near-black zone — rendered as LARGE bold gold display text, among the "
            "biggest elements in the ad after the location name; the dark floor gives full "
            "contrast so no box is needed; keep clear space below it before the SINDHUBHAVAN ROAD "
            "line. Sample badge 'SAMPLE FLAT READY' sits upper-mid at roughly 28% height as a "
            "GENEROUSLY SIZED solid charcoal pill with a gold hairline — large enough that the "
            "three words read instantly across a room, never a small grey lozenge lost against the "
            "bright ceiling. Neither element is tucked into a corner.\n"
            "Supporting specs — NO bottom strip; this bright open room must breathe. Float "
            "'CLUBCLASS AMENITIES  ·  3,300–6,100 SQ FT' as a single slim tracked gold line on a "
            "thin gold hairline directly beneath SINDHUBHAVAN ROAD in the floor zone, part of the "
            "same cluster, with clear spacing above and below. Two items only — do not pad a "
            "floating line. No sign-board, no boxed strip.\n"
            "TYPOGRAPHIC INTEGRATION: all text is flat, scene-integrated typography — no bevel, "
            "metallic sheen, gloss, or 3D depth.\n"
            "Top-right corner of full canvas: clear — logo compositing zone."
        ),
        "headline":    "Mornings were made for rooms like this.",
        "eyebrow":     "",
        "palette_tag": "charcoal_gold",
        "scene_tag":   "open_plan_dining_lounge_bright_cool_morning",
        "tone_tag":    "bright_aspirational",
        "recipe_tag":  "the_open_room_anchor",
        "logo_corner": "top-right",
        "badge_cta":   "SAMPLE FLAT READY",
    },

    # =========================================================================
    # VARIANT 2 — Lifestyle / The Social Home  (THE ONE warm scene)
    # NEW scene: evening tasting / dinner table seen through a plaster archway
    # Skeleton: location HIGH in arch-reveal band; headline mid-left in reveal;
    #           price + CTA prominent burgundy pills (dark busy surface);
    #           footer = TEXT STRIP, three balanced items (fill-to-3).
    # Palette: burgundy_gold   Recipe: the_golden_archway
    # =========================================================================
    {
        "variant_key":  "lifestyle_social_home",
        "prompt_num":   2,
        "scene_prose": (
            "Sony A7R V, 35mm f/2.0, handheld at 150cm just outside a tall plaster archway, "
            "looking through it into a warm dining-and-tasting space. 9:10pm — warm 2700K light "
            "from a low linear pendant over a long table plus a backlit display niche; this is the "
            "one deliberately warm, social, evening scene of the set. ISO 1000, f/2.0, gentle "
            "falloff into the archway's shadowed reveal, a whisper of lens warmth at the edge.\n\n"
            "The archway is a full-height plastered opening with a softly rounded head, its deep "
            "reveal in warm shadow framing the room beyond. Inside: a long book-matched walnut "
            "table set for a relaxed dinner — four guests mid-conversation, a decanter and glasses "
            "poured, a low arrangement of figs and candles. A woman in burgundy silk, a man in "
            "charcoal, two more in cream and slate. Behind them a backlit fluted-oak display wall "
            "and full-height glazing onto the Ahmedabad night. Brushed-brass details, deep "
            "upholstered chairs, layered table linen. The frame is warm, full, and lived-in — no "
            "empty space anywhere."
        ),
        "composition_notes": (
            "PEOPLE DO NOT DISPLACE TEXT: the four guests are scene elements only — all text below "
            "is mandatory at full prominent size.\n"
            "'SINDHUBHAVAN' is one unbroken 12-character word (S-I-N-D-H-U-B-H-A-V-A-N) — no "
            "internal slash, hyphen, space, or split inside the word.\n"
            "DISTINCT SKELETON — this variant anchors the location HIGH in the arch reveal. The "
            "golden_archway device: the deep warm-shadow reveal of the plaster arch frames the "
            "upper and left edges — this shadowed band is the typography zone, no overlay needed.\n"
            "SINDHUBHAVAN ROAD: HEAVY gold luxury display serif spanning 78% of canvas width, "
            "anchored across the dark arch-reveal in the upper 26% of the frame, clear of the top "
            "edge. MONUMENTAL — each letter legible at arm's length. Flanked by thin gold editorial "
            "hairlines. 'AHMEDABAD' in clearly readable tracked geometric gold caps below, same "
            "zone — a confident line, not a faint caption.\n"
            "'4 & 5 BHK' — a STANDALONE LARGE element at roughly 50% canvas height on the RIGHT, "
            "against the warm-shadow zone beside the display wall. HEAVY gold display serif, same "
            "family as the location name, at roughly 50% of its cap height. A primary selling "
            "point, not a label.\n"
            "Campaign headline 'The evening has found its address.' — bold italic display serif, "
            "warm cream, at roughly 40% canvas height in the soft shadow of the archway reveal, "
            "left of the figures, with clear spacing from the location name above.\n"
            "SCENE NEGATIVE SPACE: keep the arch-reveal band, the left reveal shadow and the "
            "foreground table edge as calm text zones; do NOT stack the BHK and the price "
            "together on the right edge — give them separate stations with a clear vertical "
            "gap.\n"
            "PRICE & CTA — both LARGE, both high-contrast, both central, clearly separated. "
            "Price 'FROM ₹3 CR ONWARDS' sits at the BOTTOM-RIGHT of the photo zone, just above "
            "the footer strip and well clear of the BHK far above it, as a solid deep-burgundy "
            "pill (#3A1721) with a gold hairline — among the largest elements in the ad after "
            "the location name, reading instantly against the warm scene. Sample badge "
            "'SAMPLE FLAT READY' mid-left at roughly 46% height in the arch-reveal shadow, a "
            "GENEROUSLY SIZED solid burgundy pill with a gold hairline; three words rendered "
            "large, never shrunk to fit.\n"
            "Supporting specs — a TEXT STRIP suits this grounded, hosted evening scene. A slim "
            "deep-burgundy footer strip (#3A1721) along the base, full width, ~9% height, sitting "
            "clear of the price above it. THREE balanced items in evenly weighted tracked Bold "
            "geometric gold caps, separated by small brushed-brass diamond dividers: "
            "'CLUBCLASS AMENITIES'  ·  '3,300–6,100 SQ FT'  ·  'GATED COMMUNITY'. Intentional even "
            "spacing, not stretched to fill width.\n"
            "TYPOGRAPHIC INTEGRATION: flat scene-integrated typography — no bevel, sheen, gloss, "
            "or 3D depth.\n"
            "Bottom-right corner of full canvas: clear — logo compositing zone."
        ),
        "headline":    "The evening has found its address.",
        "eyebrow":     "",
        "palette_tag": "burgundy_gold",
        "scene_tag":   "evening_tasting_table_through_archway",
        "tone_tag":    "dark_luxury",
        "recipe_tag":  "the_golden_archway",
        "logo_corner": "bottom-right",
        "badge_cta":   "SAMPLE FLAT READY",
    },

    # =========================================================================
    # VARIANT 3 — Lifestyle / Dynamic A
    # NEW scene: overhead bird's-eye of an open living lounge, COOL daylight
    # Skeleton: location in TOP marble band; headline centred on the rug;
    #           price + CTA as bold text on light marble (no box);
    #           footer = ICON-GRID, three columns (fill-to-3).
    # Palette: slate_cream   Recipe: the_zenith_gaze
    # =========================================================================
    {
        "variant_key":  "lifestyle_dynamic_a",
        "prompt_num":   3,
        "scene_prose": (
            "Sony A7R V, 24mm f/5.6, mounted directly overhead at 4.5m on a discreet ceiling rig, "
            "looking straight down at a large open living lounge — a true bird's-eye plan view. "
            "10:40am — bright, cool, even overhead daylight from a broad skylight slot and the "
            "perimeter glazing; clean neutral light that reveals every surface texture, crisp "
            "shadows, no amber. ISO 200, f/5.6.\n\n"
            "Seen from above: a deep sectional sofa wraps three sides of a large hand-knotted wool "
            "rug in soft slate and cream, a sculptural travertine coffee table at its centre "
            "holding a low orchid and stacked books. A couple reclines on the sofa, relaxed — one "
            "reading, one resting — small and centred within the architecture. The floor around "
            "the rug is large-format honed Bianco marble with a fine brushed-brass inlay tracing a "
            "slow curve. A console, a tall floor lamp, a potted olive and a folded throw fill the "
            "corners. Every plane carries material and texture; the composition reads as a calm, "
            "full, perfectly balanced overhead canvas."
        ),
        "composition_notes": (
            "PEOPLE DO NOT DISPLACE TEXT: the reclining couple are scene elements only — all text "
            "below is mandatory at full prominent size; keep the rug and marble clear around them "
            "for typography.\n"
            "'SINDHUBHAVAN' is one unbroken 12-character word (S-I-N-D-H-U-B-H-A-V-A-N) — no "
            "internal slash, hyphen, space, or split.\n"
            "DISTINCT SKELETON — this overhead variant anchors the location in a TOP BAND. The "
            "zenith_gaze device: the broad honed-marble border across the top of the frame is the "
            "primary typography surface.\n"
            "SINDHUBHAVAN ROAD: HEAVY or BLACK luxury display serif in warm slate-grey (#2A2E38), "
            "spanning 82% of canvas width across the cool marble top band, clear of the top edge. "
            "MONUMENTAL — individual letters legible at arm's length. 'AHMEDABAD' in clearly "
            "readable tracked geometric caps directly below, same band — a confident line.\n"
            "'4 & 5 BHK' — a STANDALONE LARGE element, NOT in any footer. Place it at roughly 30% "
            "canvas height on the RIGHT, against the clear marble floor beside the sofa. HEAVY "
            "slate display serif, same family as the location name, at roughly 50% of its cap "
            "height. A primary selling point.\n"
            "Campaign headline 'Some homes are best seen from above.' — bold italic display serif, "
            "deep slate, centred on the cream rug surface at roughly 50% canvas height (the rug is "
            "the natural headline canvas), with clear spacing around the coffee table.\n"
            "PRICE & CTA — both LARGE, both high-contrast, both within the central focus area, "
            "clearly separated (BHK sits high-right ~30%, price sits low-right ~70% — a wide "
            "gap, never stacked). Price 'FROM ₹3 CR ONWARDS' on the clear marble floor: white "
            "marble gives dark-on-light contrast, so render it as LARGE bold slate display text "
            "directly on the marble with a faint soft shadow — among the largest elements after "
            "the location name, no box needed. Sample badge 'SAMPLE FLAT READY' mid-left at "
            "roughly 46% height on the marble border: also a light surface, so bold slate text "
            "with a thin slate hairline underline, GENEROUSLY SIZED — unmissable, never a tiny "
            "tag.\n"
            "Supporting specs — an ICON-GRID footer suits the architectural overhead balance. A "
            "dark slate strip (#1E2430), ~10% height, full width, sitting clear of the price "
            "above. FOOTER GRID GEOMETRY: THREE equal columns on a strict invisible grid, each "
            "centred on its own axis, gold icon above gold label, two thin gold vertical hairlines "
            "at the exact column divisions, equal outer margins. LEFT: clubhouse icon + "
            "'CLUBCLASS AMENITIES'. CENTRE: ruler icon + '3,300–6,100 SQ FT'. RIGHT: gate icon + "
            "'GATED COMMUNITY'. Bold geometric gold caps filling each column generously.\n"
            "TYPOGRAPHIC INTEGRATION: flat scene-integrated typography — no bevel, sheen, gloss, "
            "or 3D depth.\n"
            "Top-left corner of full canvas: clear — logo compositing zone."
        ),
        "headline":    "Some homes are best seen from above.",
        "eyebrow":     "",
        "palette_tag": "slate_cream",
        "scene_tag":   "overhead_living_lounge_cool_daylight",
        "tone_tag":    "bright_aspirational",
        "recipe_tag":  "the_zenith_gaze",
        "logo_corner": "top-left",
        "badge_cta":   "SAMPLE FLAT READY",
    },

    # =========================================================================
    # VARIANT 4 — Lifestyle / Dynamic B
    # NEW scene: panelled reading nook, figure silhouetted at a bright window,
    #            COOL bright overcast morning (rebalanced away from warm lamp)
    # Skeleton: location in LEFT VERTICAL COLUMN (panel shadow); headline on the
    #           bright limestone foreground; CTA MID-FRAME above the seating
    #           group (the user's liked "above the sofa" placement);
    #           footer = DISTRIBUTED pair of floating lines (NO strip).
    # Palette: forest_gold   Recipe: the_backlit_silhouette
    # =========================================================================
    {
        "variant_key":  "lifestyle_dynamic_b",
        "prompt_num":   4,
        "scene_prose": (
            "Sony A7R V, 35mm f/2.2, tripod at 130cm deep in a panelled reading nook, looking "
            "toward a vast floor-to-ceiling picture window where a man stands silhouetted. 7:50am "
            "— bright, cool, slightly overcast morning light floods through the glass and blows "
            "out the sky behind him; the figure reads as a clean dark silhouette, the room's "
            "foreground lit by soft bounced daylight. ISO 400, f/2.2, no amber, crisp and "
            "contemplative.\n\n"
            "The nook is lined in emerald-stained oak panelling and integrated shelving — books, a "
            "brass globe, framed prints, a rolling library ladder. A man in a dark tailored shirt "
            "stands at the window holding a coffee, back to camera, watching the city wake. A "
            "deep-green velvet armchair and a leather ottoman sit on a patterned wool rug; a "
            "brass-and-marble side table holds a folded newspaper. Floor: pale honed limestone, "
            "brightly daylit in the foreground — a clean illuminated surface. The panelling falls "
            "into soft shadow up the left side, while the bright window dominates the centre-right. "
            "Layered, quietly expensive, every surface carrying material."
        ),
        "composition_notes": (
            "PEOPLE DO NOT DISPLACE TEXT: the silhouetted man is a scene element only — all text "
            "below is mandatory at full prominent size.\n"
            "'SINDHUBHAVAN' is one unbroken 12-character word (S-I-N-D-H-U-B-H-A-V-A-N) — no "
            "internal slash, hyphen, space, or split.\n"
            "SKELETON: the dark emerald-panel wall is the backdrop for the location name at the "
            "top. The bright daylit limestone foreground (lower 35%) is the headline and spec "
            "canvas. All other elements use the mid-zone between them. Keep all typography off "
            "the blown-out window. Elements are spread across the frame — not stacked.\n\n"
            "TEXT COLOUR PER ELEMENT — each element uses the colour that reads on ITS surface:\n"
            " • SINDHUBHAVAN ROAD & AHMEDABAD: GOLD on dark emerald panel — high contrast ✓\n"
            " • 4 & 5 BHK RESIDENCES: GOLD on dark panelling — high contrast ✓\n"
            " • SAMPLE FLAT READY badge: CREAM or warm WHITE text inside a near-black pill "
            "(#14110E) with a gold hairline — gold text inside a dark pill is redundant; cream "
            "on near-black reads instantly\n"
            " • Price: GOLD text inside a near-black pill (#14110E) with a gold hairline — "
            "gold pops on near-black ✓\n"
            " • Headline: warm CREAM italic on the bright limestone floor — cream on near-white "
            "stone reads as warm and refined, not gold on near-white which disappears\n"
            " • Specs: BOLD geometric CREAM or warm WHITE caps on the bright limestone foreground "
            "— the dark panel shadow makes gold spec text vanish at small size; cream on the "
            "bright floor is instantly readable. Do NOT place specs on the dark panel.\n\n"
            "LAYOUT — five elements at distinct positions, each anchored to a scene landmark:\n"
            "TOP (0–20%): SINDHUBHAVAN ROAD — ONE single horizontal line, left to right across "
            "the dark panel zone, spanning 76% of canvas width. NEVER two lines, NEVER a column "
            "label. HEAVY gold display serif. 'AHMEDABAD' in tracked gold caps directly below, "
            "same zone.\n"
            "LEFT (~30–45% height): '4 & 5 BHK RESIDENCES' — anchored against the shelving on "
            "the left-hand dark panelling, roughly mid-panel. HEAVY gold display serif at ~50% "
            "of location name cap height. At least 12% canvas height below AHMEDABAD.\n"
            "RIGHT (~50% height): Price 'FROM ₹3 CR ONWARDS' — on the darkest panel return strip "
            "beside the window, RIGHT side. Near-black pill (#14110E), gold hairline, gold text. "
            "LARGE — among the biggest elements after the location name. Clearly separated from "
            "the BHK on the left.\n"
            "CENTRE (~58% height, above the velvet armchair): 'SAMPLE FLAT READY' — anchored "
            "visually above the armchair grouping, slightly left of centre. Near-black pill "
            "(#14110E), gold hairline, CREAM or warm-white text (not gold). GENEROUSLY SIZED. "
            "The armchair is the visual anchor — the badge sits just above it, not floating.\n"
            "BOTTOM (75–85%): On the BRIGHT LIMESTONE FOREGROUND floor zone:\n"
            " — Headline 'Room enough for the long view.' as bold italic display serif in warm "
            "CREAM, spanning 65% of canvas width.\n"
            " — Below that, 'CLUBCLASS AMENITIES' and '3,300–6,100 SQ FT' as BOLD geometric "
            "CREAM caps, clearly readable, each on a thin warm hairline. These are on the BRIGHT "
            "FLOOR — use cream/off-white, NOT gold. Size them so both lines read at arm's length. "
            "Two items only — do not pad.\n"
            "TYPOGRAPHIC INTEGRATION: flat scene-integrated typography — no bevel, sheen, gloss, "
            "or 3D depth.\n"
            "Bottom-left corner of full canvas: clear — logo compositing zone."
        ),
        "headline":    "Room enough for the long view.",
        "eyebrow":     "",
        "palette_tag": "forest_gold",
        "scene_tag":   "panelled_reading_nook_window_silhouette_cool_morning",
        "tone_tag":    "bright_aspirational",
        "recipe_tag":  "the_backlit_silhouette",
        "logo_corner": "bottom-left",
        "badge_cta":   "SAMPLE FLAT READY",
    },

    # =========================================================================
    # VARIANT 5 — Interior Signature Moment
    # NEW scene: corner sitting room, two glazed walls, BLUE-HOUR city horizon
    # Skeleton: location in SKY BAND (upper navy); headline on dark marble mid;
    #           price BOTTOM-MID as bold gold text on dark stone (no box);
    #           CTA in sky zone; footer = single DISTRIBUTED floating line.
    # Palette: navy_gold   Recipe: the_horizon_anchor
    # =========================================================================
    {
        "variant_key":  "interior_signature_moment",
        "prompt_num":   5,
        "scene_prose": (
            "Sony A7R V, 28mm f/4, tripod at 125cm in a corner sitting room where two walls of "
            "frameless glazing wrap the city. 7:25pm — deep blue hour; the Ahmedabad sky is rich "
            "navy-indigo with a low warm band of city lights along the horizon, cool ambient light "
            "inside meeting the warm horizon glow on the stone. ISO 500, f/4, a faint reflection "
            "of the city in the polished floor. A single figure is seated low, gazing out.\n\n"
            "The corner lounge is a pure material set-piece: a low travertine plinth bench runs "
            "along the glazing, dressed with linen cushions; a sculptural lounge chair in cognac "
            "leather sits angled to the view. The inner wall is a full-height book-matched grey "
            "marble slab, mirror-polished, with a slim recessed bronze shelf holding a single "
            "sculpture. Floor: large-format honed stone catching the city reflection. A low "
            "brass-and-glass table holds a decanter and one glass. The glazing is frameless, the "
            "navy horizon unbroken. Quiet, exact and expensive — the frame is full of surface, "
            "reflection and the held blue light."
        ),
        "composition_notes": (
            "PEOPLE DO NOT DISPLACE TEXT: the seated figure is a scene element only — all text "
            "below is mandatory at full prominent size.\n"
            "'SINDHUBHAVAN' is one unbroken 12-character word (S-I-N-D-H-U-B-H-A-V-A-N) — no "
            "internal slash, hyphen, space, or split inside the word.\n"
            "DISTINCT SKELETON — this variant anchors the location in the SKY BAND. The "
            "horizon_anchor device: the deep navy blue-hour sky in the upper 30% of the wrapping "
            "glazing is dark enough to carry monumental letterforms with no backing; the warm "
            "city-light horizon sits just below as a natural baseline.\n"
            "SINDHUBHAVAN ROAD: HEAVY gold luxury display serif spanning 80% of canvas width "
            "across the navy sky band, clear of the top edge. MONUMENTAL — each individual letter "
            "legible at arm's length. Flanked by thin gold editorial hairlines. 'AHMEDABAD' in "
            "clearly readable tracked geometric gold caps below, same sky zone.\n"
            "'4 & 5 BHK' — a STANDALONE LARGE element at roughly 48% canvas height on the RIGHT, "
            "against the dark book-matched marble veining. HEAVY gold display serif, same family "
            "as the location name, at roughly 50% of its cap height. A primary selling point, not "
            "a label.\n"
            "Campaign headline 'The skyline keeps you company.' — bold italic display serif, warm "
            "cream, at roughly 40% canvas height set against the darker marble mid-zone, never "
            "over the bright city-light band.\n"
            "PRICE & CTA — both LARGE, both high-contrast, both within the central focus area, "
            "clearly spaced (price bottom-mid, CTA upper-left, BHK right — three separate "
            "stations, never stacked). Price 'FROM ₹3 CR ONWARDS' BOTTOM-MID at roughly 72% "
            "height on the dark polished-stone foreground: gold on near-black stone is full "
            "contrast, so render as LARGE bold gold display text with a faint soft glow — among "
            "the largest elements after the location name, no box needed. Sample badge "
            "'SAMPLE FLAT READY' upper-mid-left at roughly 36% height in the navy sky zone: gold "
            "on deep navy is full contrast, so bold gold text with a thin gold hairline "
            "underline, GENEROUSLY SIZED — not a tiny corner tag.\n"
            "Supporting specs — DISTRIBUTED, no strip, no box; this pristine material set-piece "
            "deserves the most refined treatment. Float 'CLUBCLASS AMENITIES  ·  3,300–6,100 SQ "
            "FT' as a single slim tracked gold line on a thin gold hairline set into the dark "
            "polished-stone foreground, just below the price, with clear spacing. Two items only "
            "— do not pad. The base of the frame stays open polished stone.\n"
            "TYPOGRAPHIC INTEGRATION: flat scene-integrated typography — no bevel, sheen, gloss, "
            "or 3D depth.\n"
            "Bottom-right corner of full canvas: clear — logo compositing zone."
        ),
        "headline":    "The skyline keeps you company.",
        "eyebrow":     "",
        "palette_tag": "navy_gold",
        "scene_tag":   "corner_lounge_blue_hour_city_horizon",
        "tone_tag":    "dark_luxury",
        "recipe_tag":  "the_horizon_anchor",
        "logo_corner": "bottom-right",
        "badge_cta":   "SAMPLE FLAT READY",
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
