"""
Simulates ContentCrew + Visual Prompter pipeline for Anamika Heights.
FULL REFRESH — brand-new scenes, headlines, compositions.

RULES APPLIED (after reference ad study + image review):

  Eyebrow:   One SHORT contextual fact not already dominant elsewhere. Max 5 words.
             Never repeats location name or price already shown at large scale.
             Omit entirely if the composition is self-explanatory.

  CTA badge: Max 4 words. Assertive. Specific, not generic.

  Price:     Always exactly Rs 3 Cr. "3,300 sq ft" is apartment SIZE — never confused
             with price. Kept as separate, clearly distinct elements.

  Separator: Each ad picks ONE — either · or — — used consistently throughout.
             Stated explicitly in composition_notes so the model doesn't mix them.

  Location:  Must be HEAVY/BLACK weight serif. ALWAYS. Composition notes reinforce this.

  Reference ad lessons applied (DreamYug / Shreeji / Suncity / Prakrit / Nehru Nagar):
    V1 — Architectural-scale location name: "SINDHUBHAVAN / ROAD" stacked, each
         word filling its own line. Like Nehru Nagar's "NEHRU / NAGAR". HEAVY weight.
    V2 — "SINDHUBHAVAN" as a single frame-spanning graphic element like Prakrit's
         "LUXURY" — one word, edge-to-edge, is the entire creative device.
    V3 — Bold provocation headline in sky zone. Price itself is the opening line.
         Attention through brevity and scale, not decoration.
    V4 — "100% CHEQUE ONLY" as the status bottom bar, like Nehru Nagar's cheque bar.
         The payment policy IS the brand signal. Made into the bottom statement.
    V5 — Mixed-scale typography: number (3,300) in HUGE serif, descriptor in italic
         script, address in tracked caps — three weights, one composition. Shreeji-style.

Run:  python _manual_llm_outputs.py
"""

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
    "usps":                    ["Clubclass Amenities / 30+ Storey Tower"],
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
# STAGE 1 — Campaign copywriter output (5 fresh headlines)
#
# V1  3,300 sq ft of morning.          (size as poetry, not a spec — specific moment)
# V2  Full floors. Not floor plans.    (category reframe — what a 5 BHK actually means)
# V3  Three crore. Thirty floors.      (both numbers together = the pitch in one line)
# V4  100% Cheque. 0% Compromise.      (payment policy as creative mirror device)
# V5  3,300.                           (just the number — the provocation)
# ---------------------------------------------------------------------------

LLM_OUTPUTS = [

    # =========================================================================
    # VARIANT 1 — Lifestyle / Private Retreat
    # scene: master_bedroom_dawn_corner
    # brief: private, unhurried, one figure, back to camera. Space is the star.
    #
    # REFERENCE INSPIRATION: Nehru Nagar ad — location name ARCHITECTURAL-SCALE,
    # each word on its own line, filling that line width, HEAVY HEAVY HEAVY.
    # "SINDHUBHAVAN" on one line. "ROAD" on the line below. Both HUGE.
    # That IS the ad. Nothing else needs to be the hero.
    #
    # COMPOSITION (scene-derived):
    #   The master bedroom at dawn has a large dark plaster wall to the left of
    #   the corner glazing — this is a natural wide canvas.
    #   "SINDHUBHAVAN" fills this wall as a single line — H-E-A-V-Y gold serif.
    #   "ROAD" fills the line below at the same scale — equally HEAVY, equally gold.
    #   The two words together ARE the design.
    #   Below "ROAD": "AHMEDABAD" in small tracked geometric sans, gold.
    #   The campaign tagline "3,300 sq ft of morning." appears in a fine italic serif,
    #   warm white, tucked below AHMEDABAD — caption-scale, not a second headline.
    #   Top-right corner: eyebrow "4 & 5 BHK" in tracked caps — 3 words, that's all.
    #   PRICE: a bold dark pill — charcoal fill, gold border — bottom-right of photo
    #   zone, off-axis. "FROM Rs 3 CR" inside it, HEAVY serif. Unmissable.
    #   BADGE: "FLAT OPEN" — 2 words. Small stamped rectangle, bottom-left.
    #   Bottom: single thin tracked line: "4 & 5 BHK · Clubclass Amenities · 30+ Storeys"
    #   Separator throughout this ad: · (centered dot). No mixing.
    #   Top-right corner: clear for logo.
    # =========================================================================
    {
        "variant_key":  "lifestyle_private_retreat",
        "prompt_num":   1,
        "scene_prose": (
            "Sony A7R V, 35mm f/1.4, tripod at chest height angled toward the corner "
            "glazing wall where two glass faces meet. 6am — the first amber light of dawn "
            "at 3200K enters diagonally from the east face, catching the left edge of a "
            "white-on-white Calacatta marble floor and the bottom half of the far wall. "
            "The ceiling and upper-left wall remain in a warm, deep shade. ISO 200, f/4.\n\n"
            "The master bedroom is 28 feet wide with 10.5-foot ceilings. The corner window "
            "bay frames a view of Ahmedabad's quieter residential canopy, still grey-blue "
            "in the pre-dawn. The far wall to the left of the glazing is smooth warm plaster "
            "— no shelving, no art, no hardware — a bare expanse of dark warm shadow. "
            "A man in a charcoal cashmere robe stands at the corner glass, back to camera, "
            "hands in the robe's deep pockets, looking out at the waking city. "
            "Floor: polished Calacatta Gold marble 1200x2400. The room is still. "
            "The scale of the space is apparent before anything else."
        ),
        "composition_notes": (
            "The creative device is the location name rendered at architectural scale — "
            "like a building name carved into its own facade. Everything else is secondary.\n"
            "SINDHUBHAVAN: rendered across the top half of the dark left wall in a single "
            "horizontal line — HEAVY or BLACK weight luxury serif, gold (#C9A84C). "
            "Each letter must be individually legible. Scale to fill the available wall width "
            "from left edge to where the glazing begins. This one word is visually dominant.\n"
            "ROAD: directly below SINDHUBHAVAN on its own line, same HEAVY weight, same gold, "
            "same scale — fills the same horizontal span. The two-line stack IS the design.\n"
            "AHMEDABAD: below ROAD, in small tracked geometric sans-serif, gold. "
            "Two scales below the location name. City as quiet confirmation, not a banner.\n"
            "Tagline '3,300 sq ft of morning.': fine italic serif, warm white, caption-scale, "
            "tucked immediately below AHMEDABAD. Reads like a photographer's whisper on the wall. "
            "NOTE: 3,300 here is apartment size in square feet — not the price.\n"
            "Eyebrow '4 & 5 BHK': very top of frame, right-aligned, tracked geometric caps, "
            "warm white, small. Three words only. No price here — price is the pill.\n"
            "Price pill: bottom-right of the photo zone, off-axis (not centred). "
            "Warm charcoal fill (#2B2420), gold border. Inside it: 'FROM Rs 3 CR' in HEAVY "
            "display serif, gold. Size this pill so the text inside is large and unmissable.\n"
            "Badge 'SAMPLE APARTMENT READY': bottom-left of the photo zone. A compact stamped "
            "rectangle, same charcoal fill, gold border. Geometric sans, medium-bold. 3 words.\n"
            "Single spec line at very bottom of canvas, inside a slim charcoal backing strip "
            "(full width, 7% canvas height): "
            "'4 & 5 BHK · Clubclass Amenities · 30+ Storeys' — tracked geometric caps, gold. "
            "Separator throughout: · (centred dot). No dashes, no pipes.\n"
            "Top-right corner of canvas: kept entirely clear — logo compositing zone."
        ),
        "headline":    "3,300 sq ft of morning.",
        "eyebrow":     "4 & 5 BHK",
        "palette_tag": "charcoal_gold",
        "scene_tag":   "master_bedroom_dawn_corner",
        "tone_tag":    "bright_aspirational",
        "recipe_tag":  "the_editorial_sidebar",
        "logo_corner": "top-right",
        "badge_cta":   "SAMPLE APARTMENT READY",
    },

    # =========================================================================
    # VARIANT 2 — Lifestyle / The Social Home
    # scene: great_room_evening_reverse_angle
    # brief: 2-3 people, warmth, ease. Never posed. The room reveals its scale.
    #
    # REFERENCE INSPIRATION: Prakrit's "LUXURY" edge-to-edge as the creative device.
    # One word. Frame-spanning. The word IS the design.
    # Here: "SINDHUBHAVAN" in HEAVY serif, spanning the full frame width across the
    # bottom 22% of the photo zone — gold on the dark floor/shadow zone.
    # This becomes the base of the entire composition.
    # "ROAD" in much smaller tracked caps directly below it as an address qualifier.
    #
    # COMPOSITION:
    #   The room shot from an unusual reverse angle — camera near the glazing looking
    #   INTO the room, city behind (reflected in the glass). Warm amber cove lighting.
    #   The scene has a naturally dark foreground floor zone (where the room floor
    #   meets the camera position near the glass).
    #   "SINDHUBHAVAN" sweeps across this dark foreground zone — edge-to-edge.
    #   Above it, the room unfolds into warm amber light with three figures.
    #   The campaign tagline floats over the sofa area in italic serif.
    #   Price: compact burgundy pill, right side, mid-height, figure eyeline level.
    #   Badge: "ARRANGE A PRIVATE VIEWING" — 4 words, compact pill, upper-left inside the frame.
    #   No footer strip. The big word at the bottom IS the footer.
    #   Separator: — (em dash). Consistent.
    #   Top-left: clear for logo.
    # =========================================================================
    {
        "variant_key":  "lifestyle_social_home",
        "prompt_num":   2,
        "scene_prose": (
            "Sony A7R V, 24mm f/1.8, tripod positioned at the glazing wall looking "
            "INTO the great room. 8:30pm — warm amber cove lighting at 2700K fills the "
            "room's depth; the city is reflected faintly in the glass behind the camera. "
            "Three gold-finish cylindrical pendants (matte brass, 280mm diameter) hang "
            "from the recessed plaster ceiling at 10.5 feet — their pools of light "
            "structure the upper two-thirds of the frame. ISO 640, f/2.0.\n\n"
            "The 5 BHK great room is 38 feet wide. A modular sofa in deep slate linen "
            "sits central; a low travertine coffee table between it and camera. "
            "Three figures — two women in silk slip midi-dresses (ivory and moss), "
            "one man in a loose linen overshirt — stand at ease near the sofa in "
            "conversation, champagne flutes present but incidental. Wide-plank European "
            "oak flooring in natural oil finish. The room's scale becomes apparent only "
            "when the figures register as small against the room's depth and width."
        ),
        "composition_notes": (
            "The creative device: 'SINDHUBHAVAN' in HEAVY gold serif spans the full width "
            "of the frame across the dark foreground floor zone — edge-to-edge. "
            "Each letter must be large enough that all 12 characters fill the frame width "
            "comfortably — roughly 1/12th of frame width per character. "
            "This is deliberately oversized as a graphic element, not a normal headline. "
            "It is the visual anchor of the entire composition.\n"
            "Directly below SINDHUBHAVAN: 'ROAD — AHMEDABAD' in small tracked geometric caps, "
            "gold, centred below the big word. The address qualifier, minimal.\n"
            "Above the big word, floating over the dark sofa zone at mid-height: "
            "campaign tagline 'Full floors. Not floor plans.' in bold italic display serif, "
            "cream, large — this commands the visual centre of the photo zone.\n"
            "Price: compact dark burgundy pill (#3D0C02), gold border (#9A7B4F), "
            "right side of frame at mid-height, roughly aligned with the figures' "
            "eyeline — off-axis, not centred. Inside: 'Rs 3 CR ONWARDS' in HEAVY serif, gold.\n"
            "Badge 'ARRANGE A PRIVATE VIEWING': compact pill, upper-left of frame inside the gold border. "
            "Dark backing, gold border, geometric sans bold. 4 words.\n"
            "A slim gold hairline border runs edge-to-edge around the entire canvas — "
            "the governing frame within which everything sits.\n"
            "No separate footer strip. 'SINDHUBHAVAN' at the bottom IS the footer.\n"
            "Spec line below the big word (between SINDHUBHAVAN and the canvas bottom): "
            "'4 & 5 BHK — Clubclass Amenities — 30+ Storeys' in fine tracked geometric caps, "
            "warm ivory, very small — it reads below the big word, before the canvas edge.\n"
            "Separator throughout: — (em dash). No dots, no pipes.\n"
            "Top-left corner, inside the gold border: clear for logo compositing."
        ),
        "headline":    "Full floors. Not floor plans.",
        "eyebrow":     "",
        "palette_tag": "burgundy_gold",
        "scene_tag":   "great_room_evening_reverse_angle",
        "tone_tag":    "dark_luxury",
        "recipe_tag":  "the_golden_archway",
        "logo_corner": "top-left",
        "badge_cta":   "ARRANGE A PRIVATE VIEWING",
    },

    # =========================================================================
    # VARIANT 3 — Lifestyle / City Connection
    # scene: balcony_floor_level_dusk
    # brief: City is visible. View IS the subject. Bold, attention-catching.
    #
    # REFERENCE INSPIRATION: Make the TEXT the attention event, not decoration.
    # Price and floor count together = the provocation. Both numbers in one line.
    # Suncity's approach — let one typographic element be the drama.
    #
    # COMPOSITION:
    #   Camera at balcony floor level — railing at mid-frame, city drops away below.
    #   The sky occupies the upper 45% of the frame — deep cobalt at dusk.
    #   THIS is the primary text zone.
    #   "SINDHUBHAVAN ROAD" centred in the cobalt sky — HEAVY serif, gold, very large.
    #   The headline "Three crore. Thirty floors." — directly below, in italic serif,
    #   cream, large. This is the provocation. Both numbers. Both facts. One line.
    #   "AHMEDABAD" in small tracked caps, gold, below the headline.
    #   Eyebrow "4 & 5 BHK" — very top of sky, centred, tiny tracked caps.
    #   The balcony railing and city = completely untouched. No text in this zone.
    #   Price is already IN the headline — no separate price module.
    #   Bottom-right: "EXPERIENCE THE SHOW APARTMENT" compact badge — 4 words, slate backing, gold border.
    #   Bottom: thin slate-backed line: "30+ Storeys · Clubclass Amenities · Sample Flat Ready"
    #   Separator: · throughout.
    #   Bottom-left: clear for logo.
    # =========================================================================
    {
        "variant_key":  "lifestyle_city_connection",
        "prompt_num":   3,
        "scene_prose": (
            "Sony A7R V, 20mm f/2.8, tripod at balcony floor level — lens 15cm above "
            "the polished granite balcony surface, aimed along the floor toward the railing "
            "and city beyond. 6:15pm blue-hour — deep cobalt sky at 5800K above; the city "
            "grid below is beginning to light up, warm orange arterial glow at mid-distance. "
            "ISO 400, f/8, 4-second exposure. The railing is frameless glass with a slim "
            "dark powder-coated top rail at mid-frame height. Atmospheric perspective "
            "compresses the distant city into soft warm bokeh.\n\n"
            "The balcony is 12 feet deep with a honed dark grey granite floor — the long "
            "floor plane, shot at this angle, creates strong perspective recession toward "
            "the railing. A single sculptural outdoor chair in dark powder-coated steel "
            "with teak armrests sits left of centre — occupied by a man in a grey linen "
            "suit, half-turned toward the railing, ankle crossed. The city is 30 floors "
            "below. The balcony and city together make the argument for the address."
        ),
        "composition_notes": (
            "The cobalt sky in the upper 45% of the frame is the primary text zone — "
            "open, dark, naturally reads text.\n"
            "Very top of sky: eyebrow '4 & 5 BHK' — tracked geometric caps, platinum white, "
            "very small, centred. New context — not repeating anything else in the ad.\n"
            "Below it: 'SINDHUBHAVAN ROAD' — the primary location name in HEAVY gold "
            "luxury display serif, centred. Scale it so the two words together span "
            "roughly 55% of the frame width. Each letter must be clearly legible. "
            "HEAVY weight — not medium, not regular.\n"
            "Below the location name: 'Three crore. Thirty floors.' — the headline. "
            "Bold italic display serif, cream, large — roughly 60% of the scale of "
            "the location name. This is the provocation. The price and the floor count "
            "in one sentence. Nothing else says this.\n"
            "Below that: 'AHMEDABAD' — small tracked geometric caps, gold. "
            "Confirmation, not a feature.\n"
            "The railing, balcony floor, and city occupying the lower 55% of the frame: "
            "completely clear. No text in this zone. The city makes its own argument.\n"
            "Bottom-right of the photo zone (just above the canvas bottom): "
            "'EXPERIENCE THE SHOW APARTMENT' — a compact slate pill, gold border, bold geometric sans. 4 words.\n"
            "Canvas bottom: thin slate-backed strip (7% height), "
            "'30+ Storeys · Clubclass Amenities · Sample Flat Ready' — tracked geometric "
            "caps, gold on slate. Separator: · (centred dot) throughout this ad only.\n"
            "Bottom-left corner: clear for logo compositing."
        ),
        "headline":    "Three crore. Thirty floors.",
        "eyebrow":     "4 & 5 BHK",
        "palette_tag": "slate_cream",
        "scene_tag":   "balcony_floor_level_dusk",
        "tone_tag":    "dark_luxury",
        "recipe_tag":  "the_horizon_anchor",
        "logo_corner": "bottom-left",
        "badge_cta":   "EXPERIENCE THE SHOW APARTMENT",
    },

    # =========================================================================
    # VARIANT 4 — Exterior Establishing Shot
    # scene: tower_night_from_street
    # brief: Building authority. Scale. Night exterior. 100% cheque as the statement.
    #
    # REFERENCE INSPIRATION: Nehru Nagar's bottom bar — "100% CHEQUE PAYMENT ONLY"
    # as the single dominant bottom statement. For Anamika Heights, this is a genuine
    # USP (100% cheque only is rare). The bottom bar becomes the brand signal.
    # Like Nehru Nagar, it does not need anything else in that bar.
    #
    # COMPOSITION:
    #   Tower at 11pm, shot from 70m on the approach road. Full building in frame.
    #   Sky: dark cobalt to black. The tower is luminous — every lit floor glowing.
    #   SKY ZONE (top 35%): "SINDHUBHAVAN ROAD" centred, HEAVY serif, gold.
    #   Below: "AHMEDABAD" in small tracked caps.
    #   Below: "Rs 3 CR ONWARDS" in large HEAVY serif, gold — the price is prominent
    #   because the sky zone is open enough to hold it.
    #   Below the price: "4 & 5 BHK — 30+ STOREY TOWER" in fine tracked caps.
    #   The tower (middle 55%): completely untouched.
    #   Sample badge: "SAMPLE APARTMENT NOW OPEN" — 4 words. A bold forest-green pill with gold
    #   border, anchored above the podium entry canopy, centred.
    #   BOTTOM BAR: Like Nehru Nagar's cheque bar — dark forest-green strip, full
    #   canvas width, 10% canvas height. Single dominant statement:
    #   "100% CHEQUE ONLY" — bold, large, geometric caps, gold. Nothing else in this bar.
    #   The bar IS the brand signal.
    #   Eyebrow: "Ahmedabad" — one word. Top edge.
    #   Separator: — throughout this ad.
    #   Top-right: clear for logo.
    # =========================================================================
    {
        "variant_key":  "exterior_establishing_shot",
        "prompt_num":   4,
        "scene_prose": (
            "Sony A7R V, 50mm f/5.6 tilt-shift on carbon-fibre tripod, positioned on "
            "the approach road 70 metres from the tower's primary facade, perfectly "
            "centred. 11pm — the sky is dark cobalt to black above. ISO 800, 15-second "
            "exposure at f/8. No light trails — traffic is paused for the shot. "
            "A faint warm glow from the podium entry canopy reads as a horizon line "
            "at the base of the building.\n\n"
            "The 30+ storey tower presents its primary south facade symmetrically: "
            "champagne-finished aluminium curtain wall, flush dark bronze mullions at "
            "900mm centres, honed travertine spandrel cladding. Interior warm amber "
            "glow from occupied floors reads through the glazing as a luminous grid "
            "of warm rectangles — each floor a separate band of light. Podium uplift "
            "lighting: Queen palms at the base glow amber-green. The building is "
            "the tallest and brightest point in the entire frame — it commands the night."
        ),
        "composition_notes": (
            "The dark cobalt sky in the upper 35% of the frame is the primary text zone. "
            "The building is the visual hero; typography hangs from the sky above it.\n"
            "Very top of sky: eyebrow 'Ahmedabad' — tracked geometric caps, warm cream, "
            "tiny, centred. One word only.\n"
            "Below it: 'SINDHUBHAVAN ROAD' — HEAVY or BLACK weight gold luxury display "
            "serif, centred. Scale to span roughly 60% of frame width. "
            "CRITICAL: both words must be clearly legible, HEAVY strokes, no thin cuts.\n"
            "Below: 'AHMEDABAD' in small tracked geometric caps, gold. Confirmation.\n"
            "Below: 'Rs 3 CR ONWARDS' in large HEAVY display serif, gold. "
            "The price is given room here because the sky zone is open and dark.\n"
            "Below the price: '4 & 5 BHK — 30+ STOREY TOWER' in fine tracked caps, "
            "warm cream, small. Separator in this ad: — (em dash). Consistent throughout.\n"
            "The luminous tower occupying the middle 55% of the frame: completely clear. "
            "The building makes its own visual argument.\n"
            "Sample badge: 'SAMPLE APARTMENT NOW OPEN' — 4 words. A prominent forest-green pill "
            "(#1C3325 fill, gold border), centred horizontally, anchored just above the "
            "podium entry canopy glow. Bold geometric sans, clearly legible.\n"
            "BOTTOM BAR: Full canvas width, dark forest-green (#1C3325) backing strip, "
            "10% canvas height. Inside: ONE statement — '100% CHEQUE ONLY' — in bold "
            "large tracked geometric caps, gold (#C9A84C). Nothing else in this bar. "
            "This bar IS the brand signal. Do not add price or config here.\n"
            "Top-right corner: sky is open and dark — logo compositing zone."
        ),
        "headline":    "100% Cheque. 0% Compromise.",
        "eyebrow":     "Ahmedabad",
        "palette_tag": "forest_gold",
        "scene_tag":   "tower_night_from_street",
        "tone_tag":    "dark_luxury",
        "recipe_tag":  "the_sky_chandelier",
        "logo_corner": "top-right",
        "badge_cta":   "SAMPLE APARTMENT NOW OPEN",
    },

    # =========================================================================
    # VARIANT 5 — Interior Signature Moment
    # scene: living_room_empty_arrival_angle
    # brief: NO PEOPLE. Room is protagonist. One carefully placed detail.
    #        Light and material carry everything.
    #
    # REFERENCE INSPIRATION: Shreeji's mixed-scale mixed-weight typography.
    # Three different type treatments in one composed lockup:
    #   "3,300" — HUGE heavy serif, gold (the number is the drama)
    #   "sq ft." — italic script-weight serif, smaller (a qualifier, not a label)
    #   "SINDHUBHAVAN ROAD" — tracked geometric caps, medium, below
    # This creates scale contrast and visual hierarchy WITHOUT being a template.
    # The number is the creative device. The address follows as quiet confirmation.
    #
    # COMPOSITION:
    #   The shot is from the front door, looking down the full 40-foot living room
    #   toward the floor-to-ceiling glazing wall. The room is empty — no staging,
    #   no furniture except a single low travertine console against the far wall.
    #   The marble floor stretches away. Dusk light fills the glazing wall.
    #   The scale of the room is overwhelming from this perspective.
    #   Left wall (dark plaster): the mixed-scale typography lockup lives here.
    #   "3,300" in HUGE HEAVY gold serif — fills most of the upper-left quadrant.
    #   "sq ft." in italic display serif, smaller, immediately below.
    #   "SINDHUBHAVAN ROAD" in tracked geometric caps, medium-small, below.
    #   "AHMEDABAD" in smaller tracked caps, gold.
    #   The floor, glazing, and city beyond: completely untouched.
    #   Price: "Rs 3 CR ONWARDS" in a slim ivory-backed label, bottom of left wall zone.
    #   NOTE: 3,300 IS the apartment size. Rs 3 Cr IS the price. Separate. Distinct.
    #   Badge: "SCHEDULE YOUR PRIVATE TOUR" — 4 words. Bottom-right corner, ivory fill, gold border.
    #   Bottom: single thin tracked line directly on the dark floor reflection zone:
    #   "4 & 5 BHK · Clubclass Amenities · 30+ Storeys" — fine gold caps, no backing strip.
    #   Separator: · throughout. NO backing strip at bottom (ivory_warmth = no dark panels).
    #   Top-left: clear for logo.
    # =========================================================================
    {
        "variant_key":  "interior_signature_moment",
        "prompt_num":   5,
        "scene_prose": (
            "Sony A7R V, 20mm f/4.0, tripod at eye level in the apartment's entry threshold "
            "— the door frame partially visible at the extreme left and right edges. "
            "6:50pm — warm amber dusk light floods through the full-height west-facing "
            "glazing wall at the far end of the room, at 2400K. The room stretches 40 feet "
            "away from camera; the floor recedes in strong perspective to the glazing. "
            "ISO 100, f/11. No artificial lighting inside.\n\n"
            "The 5 BHK living room is completely empty — no furniture, no rugs, no art. "
            "The floor is large-format Calacatta Gold marble 1200x2400, book-matched — "
            "the gold veining catches the dusk light and renders near three-dimensional. "
            "Left wall: smooth warm plaster, no hardware, no openings — a single clean "
            "expanse from floor to 10.5-foot ceiling. Far wall: a single low travertine "
            "console against the glazing, 4 metres wide, 35cm deep — nothing on it. "
            "Through the glazing: Ahmedabad 30 floors below, dusk sky above. "
            "The room communicates scale before a single word is read."
        ),
        "composition_notes": (
            "The left plaster wall — in partial shadow from the dusk light entering "
            "at the far end — is the text zone. No backing panels anywhere on this canvas. "
            "Text lives on the wall's own surface.\n"
            "The typographic lockup on the left wall, reading downward:\n"
            "'3,300' — HUGE HEAVY gold luxury display serif. Scale this number so it "
            "occupies the upper half of the left wall from roughly 15% down from the "
            "ceiling to mid-wall. The number must feel monumental — this is the visual "
            "drama of the ad. NOTE: 3,300 is the apartment size in square feet, not the price.\n"
            "'sq ft.' — immediately below '3,300', in italic display serif, warm gold, "
            "at roughly 35% of the scale of '3,300'. Reads as a qualifier, not a label. "
            "Mixed weight creates the visual contrast — this is the creative device.\n"
            "'SINDHUBHAVAN ROAD' — below 'sq ft.', in tracked geometric caps, gold, "
            "at roughly the same scale as 'sq ft.' or slightly smaller. "
            "One line. The address follows the scale like a caption.\n"
            "'AHMEDABAD' — below, tracked geometric caps, gold, smaller still. Quiet confirmation.\n"
            "The marble floor, glazing wall, city beyond: completely clear of text. "
            "The scale of the room is the emotional event — protect it.\n"
            "Price: 'Rs 3 CR ONWARDS' — a slim one-line label on the left wall, below "
            "AHMEDABAD. Warm gold on the natural wall surface, no pill, no backing. "
            "Price as a measured statement, not a badge.\n"
            "Badge 'SCHEDULE YOUR PRIVATE TOUR': bottom-right corner of the photo zone — a compact "
            "rectangle, warm ivory fill, gold border, geometric sans medium-bold. 4 words.\n"
            "Bottom edge: '4 & 5 BHK · Clubclass Amenities · 30+ Storeys' — single "
            "fine tracked line set directly on the darkest band of the floor reflection "
            "at the canvas bottom. No backing strip. Gold on the natural dark surface. "
            "Separator: · (centred dot) throughout this ad only.\n"
            "Top-left corner — the darkest corner of the frame — reserved for logo compositing."
        ),
        "headline":    "3,300.",
        "eyebrow":     "",
        "palette_tag": "ivory_warmth",
        "scene_tag":   "living_room_empty_arrival_angle",
        "tone_tag":    "bright_aspirational",
        "recipe_tag":  "the_architectural_dead_zone",
        "logo_corner": "top-left",
        "badge_cta":   "SCHEDULE YOUR PRIVATE TOUR",
    },
]

# ---------------------------------------------------------------------------
entries = dedupe_visual_batch(LLM_OUTPUTS)

output_lines = []
output_lines.append("=" * 70)
output_lines.append("ANAMIKA HEIGHTS - FINAL IDEOGRAM PROMPTS (FULL REFRESH)")
output_lines.append("Reference-ad-informed composition. No template layout blocks.")
output_lines.append("=" * 70)

for entry in entries:
    vk = entry["variant_key"]
    n  = entry["prompt_num"]

    meta = get_variant_meta(vk)
    cta_brief = dict(BRIEF)
    # entry-level badge_cta overrides the variant yaml's sample_ready_cta so
    # both composition_notes and the text-strings list stay in sync.
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
