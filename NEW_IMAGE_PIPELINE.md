# NEW IMAGE PIPELINE — Pikorua AdFlow

> **Implementing session:** read this entire file before writing a single line of code.
> The embedded kickoff prompt below is your starting point.

---

## KICKOFF PROMPT (copy-paste to start the implementing session)

```
You are implementing the redesigned image-generation pipeline for the Pikorua AdFlow
luxury real-estate ad system. This md file IS your spec — read it fully before acting.

CONTEXT GATHERING (do this first, do not skip):
1. Read this entire file. The architecture, modes, libraries, deletions, and creative
   contract below are authoritative — when older code disagrees, this file wins.
2. Load project memory: read MEMORY.md and the image-pipeline session notes it indexes
   (session25→session47, pikorua_brand_visuals, ui_design_system).
3. Use graphify to locate code — DO NOT pull the whole graph. From
   `d:\Pikorua\AI Digital Marketing\pikorua-adflow\` run `graphify query "<question>"`,
   `graphify explain "<symbol>"`, `graphify path "<A>" "<B>"`; read only surfaced files.
4. Study the target look: open the reference ads in `project_context/ads/` and the analyses
   in `project_context/ads_layout_analysis.json` and `project_context/references_analyses.json`.
   The brand logo + voice live in `project_context/ad_images_examples/` and
   `project_context/brand_voice.md`. These define the luxury bar you must hit.
5. The OLD pipeline is archived in `pikorua-adflow/old_image_generation/` with a README —
   reference it for behaviour to preserve (Ideogram calls, logo compositing, brief fields),
   but DELETE the conflict sources listed in this spec; never run old + new together.

NON-NEGOTIABLES: every text element legible at a glance on a phone; elements follow a
layout format (never random); max 2–3 font styles per image (refined, not plain, not
funky); luxury colour beyond gold; the photo breathes (~80–90% of frame). Build RENDER
mode (AI art-directs, code renders) as default and BAKED mode (single clean prompt) as a
toggle. Ask the user before inventing any data field that isn't in the brief.

Then implement per the module layout and stage descriptions in this spec, deleting the
listed legacy pieces as you replace them. Add tests mirroring the archived test intent.
```

---

## 1. Why this redesign exists

The old pipeline accumulated art-direction rules in **four places that contradicted each
other**, which is why changes "don't apply" and "old rules override new ones":

1. `task_composer.py → compose_description()` — ~250 lines of layout rules injected into
   the LLM prompt.
2. `image_variants.yaml` — `structure_map`, `recipes`, `hard_bans`.
3. `design_principles.yaml` — `layout_discipline`, `detail_principles`, `vocabulary_additions`.
4. `image_service.py → build_ad_prompt()` — `AD_STRUCTURES`, `_build_typography_block` (6
   branches), repeated prose tail (MINIMUM SCALE / PRICE PROMINENCE / NEGATIVE SPACE …).

On top of that, **Ideogram renders all ad text probabilistically**, which causes illegible /
tiny text, random element placement, and reference-creative text-replacement failures.

The redesign fixes both problems:

- **One home per concern** (see §3). Rules live in data or code exactly once.
- **RENDER mode**: Ideogram generates the *scene only* (no text). Python composites all
  ad text deterministically — legibility is a code guarantee, not a model hope.

---

## 2. Locked decisions

| Decision | Value |
|----------|-------|
| Default mode | **RENDER** — AI art-directs scene, code renders text |
| Fallback mode | **BAKED** — single clean Ideogram prompt, text baked in |
| Mode toggle | `IMAGE_MODE=render\|baked` env var; per-variant override allowed |
| AI image model | Ideogram v4 (existing API key, unchanged endpoint) |
| Canva | **Excluded** — brand-template dataset requires a paid plan (verified: dataset call returned "requires a Canva paid plan"; `list-brand-kits` is empty) |
| Variant anchors | Private Retreat, Social/Family, Interior Signature (fixed); Exterior (opt-in) |
| Rotating pool | Draws from `scene_library.yaml` toward 20–30 per campaign without repeating |
| Text legibility | Hard minimum size floors enforced by **code**, not prose |

---

## 3. Governing principle: ONE home per concern

Every rule lives in exactly one place and is **never restated**:

| Concern | Where it lives | Never in |
|---------|---------------|----------|
| Scene / photography | `scene_prose` (LLM output) + renderer quality block | YAML data |
| Layout / element placement | `libraries/layouts.yaml` (data, % zone boxes) | Prose / LLM prompt |
| Colour | `libraries/palettes.yaml` (data, hex values) | Prose / LLM prompt |
| Legibility / sizing / contrast | Compositor code (legibility engine) | Prose rules |
| Claims / fabrication safety | `sanitizer.py` (data-driven from one ban list) | Multiple places |
| Exact text strings | Derived from the canonical `BriefModel` | LLM-generated |

**The LLM never writes layout prose.** It only **selects** from enumerated libraries
(layout_id, palette_id, type_pairing_id) and writes the scene. This is the structural
fix for "rules conflict / changes don't apply."

---

## 4. Pipeline overview — stages 1–7

Stages 1–5 are identical for both modes. Only stage 6 differs.

```
[Brief] → [1: BriefModel] → [2: VariantPlanner] → [3: ArtDirector LLM]
        → [4: BatchDedup] → [5: SceneRenderer → Ideogram v4]
        → [6a: Compositor (RENDER)]   or   [6b: BakedPrompt (BAKED)]
        → [7: PNG output at existing image paths]
```

### Stage 1 — BriefModel (`brief_model.py`)

One schema + extractor for canonical fields. Every downstream module reads from this
model; nothing reaches into the raw brief dict.

**Required fields:**
- `locality` — dominant location name (printed large)
- `city` — secondary location
- `price_cr` — formatted as `₹{value} Cr`
- `config` — BHK string
- `headline` — one line from copy crew output
- `eyebrow` — short aspirational line (may be empty)
- `cta` — call-to-action badge text

**Optional fields** (render only if present — never fabricated):
- `size_sqft`
- `usps` — list of up to 3 selling points for footer
- `sample_ready` — boolean flag
- `cheque_only` — boolean flag

### Stage 2 — VariantPlanner (`variant_planner.py`)

Builds the batch for this campaign.

**Fixed anchors** (always generated unless user skips):
1. `private_retreat` — solitary luxury, calm, one person or empty
2. `social_family` — warm, people present, social energy
3. `interior_signature` — empty room, material + light hero

**Opt-in anchor:**
4. `exterior` — building facade; only generated when user provides a building description

**Rotating pool:**
- Draws scene families from `libraries/scene_library.yaml`
- Grows toward 20–30 creatives across regenerations without repeating used scenes
- Pool families: single person / couple / family / group / varied luxury interiors /
  spacious-not-empty living / named-amenities-only

Also runs **`batch_dedup`** (see Stage 4) after the pool is sampled.

### Stage 3 — ArtDirector (`art_director.py`)

**One LLM call per variant.** Returns a strict JSON `AdSpec`:

```json
{
  "scene_prose": "<two paragraphs, 120–140 words, photography only — camera, lens, light, materials, subject; NO text, NO layout language>",
  "layout_id": "<id from libraries/layouts.yaml>",
  "palette_id": "<id from libraries/palettes.yaml>",
  "text_anchor": "<zone label from the chosen layout, e.g. 'lower_panel', 'sky_strip'>",
  "ornament_id": "<id from libraries/ornaments/ or empty string>",
  "type_pairing_id": "<id from libraries/type_pairings.yaml>",
  "tone": "<dark_luxury | bright_aspirational>"
}
```

The system prompt is **short** (~80 words): list the available library IDs; ask for the
scene prose; ask the LLM to select one ID from each enumerated library. Nothing else.
This replaces the old ~250-line `compose_description()` rule dump.

### Stage 4 — BatchDedup (`variant_planner.batch_dedup`)

Guarantees across the batch:
- No two ads share `(layout_id, palette_id)`.
- Minimises repetition of `type_pairing_id` and `text_anchor` back-to-back.
- Port the intent of the old `dedupe_visual_batch`; logic is simpler because the LLM
  now picks enumerated IDs rather than free-text tags.

### Stage 5 — SceneRenderer (`scene_renderer.py`)

Builds a **scene-only** Ideogram prompt from `AdSpec.scene_prose` +
photographic-quality cues + one instruction to keep the `text_anchor` zone visually calm
(low texture, no competing elements). No text strings, no layout doctrine.

Calls Ideogram v4 (`/generate`). Returns raw image bytes.

### Stage 6a — Compositor (`compositor.py`, RENDER mode, default)

Deterministic PIL rendering. Everything text-related happens here in code.

**Inputs:** `image_bytes` from Stage 5 + `AdSpec` + `BriefModel`

**Rendering order:**

1. Load image into PIL canvas.
2. `layout_id` → load zone boxes (% coordinates) from `libraries/layouts.yaml`.
3. `palette_id` → load per-element hex colours from `libraries/palettes.yaml`.
4. **Legibility engine** (enforces the old prose non-negotiables as code):
   - For each text zone: auto-fit the text string to the zone with `_fit_text(string, zone_box, font)`.
   - Hard **minimum size floor**: locality name ≥ 48pt equivalent; price ≥ 28pt; headline ≥ 22pt; subtext ≥ 14pt.
   - Auto-contrast: sample the 5×5 pixel grid under the zone; if luminance > 0.4 and no scrim
     already in the layout, add a gradient scrim or pill background to reach 7:1 contrast.
   - Scale contrast between tiers is mandatory: locality name is 5–8× the size of body copy.
5. Render each element **only if data is present** in the BriefModel:
   - `locality` — dominant text, ALL CAPS tracked, `palette.locality_color`
   - `city` — secondary, smaller weight
   - `headline` — display serif or geometric sans
   - `eyebrow` — if not empty
   - `price_cr` — inside a bounded container (rounded rect or scrim), `palette.price_bg` + `palette.price_text`
   - `config` (BHK) — prominent, not buried in footer
   - `cta` badge — rounded pill, `palette.cta_bg` + `palette.cta_text`
   - Footer row (up to 3 USPs from `brief.usps`) — only if USPs exist
   - `SAMPLE READY` label — only if `brief.sample_ready`
   - `CHEQUE PAYMENT ACCEPTED` — only if `brief.cheque_only`
   - Ornament — only if `AdSpec.ornament_id` is non-empty
6. `type_pairing_id` → load font paths from `libraries/type_pairings.yaml` +
   `libraries/fonts/`. Use heavy display serif for locality/headline; geometric sans for
   price/body/footer. At most 3 type styles total.
7. Logo composite: reuse `composite_logo()` from the old pipeline.
   Always reserved corner; backed by logo backup in `.logo_backup/`.
8. Return final PNG bytes.

### Stage 6b — BakedPrompt (`baked_prompt.py`, BAKED mode)

Single clean Ideogram prompt (~150–250 words):
- `scene_prose` (from AdSpec, verbatim)
- Exact brief text strings (locality, price, headline, eyebrow, CTA, footer items)
- One-line concise description of the chosen `layout_id` zone placement
- Palette colours (from palettes.yaml, not prose)
- Legibility / text-fidelity non-negotiables stated **once** (~20 words)

Sanitize with `sanitizer.py`, call Ideogram v4, composite logo. No conflicting layers.

### Stage 7 — Output

PNG saved to the existing image path convention:
`{review_folder}/images/image_{prompt_num}.png`

Routes and deploy are unchanged. No new file naming scheme needed.

---

## 5. Module layout

```
src/pikorua_adflow/api/services/image/
  __init__.py
  brief_model.py          # Stage 1 — BriefModel dataclass + extractor
  variant_planner.py      # Stage 2 — batch builder + batch_dedup
  art_director.py         # Stage 3 — LLM AdSpec call (short prompt)
  scene_renderer.py       # Stage 5 — Ideogram scene-only prompt builder + caller
  baked_prompt.py         # Stage 6b — BAKED mode single-prompt assembler
  compositor.py           # Stage 6a — PIL deterministic text compositor
  reference.py            # Reference-creative helpers (extract_reference_ad_layout,
                          #   analyze_reference_image) — ported from old image_service.py
  ideogram_client.py      # Thin wrapper: call_ideogram, call_ideogram_inpaint
  sanitizer.py            # Data-driven ban list (one source) + sanitize()
  libraries/
    layouts.yaml          # Layout templates — zone boxes, BAKED descriptions
    palettes.yaml         # 6+ palettes — pure hex data
    scene_library.yaml    # Scene families for anchors + rotating pool
    type_pairings.yaml    # Type pairing definitions (id, display_font, body_font, accent_font)
    ornaments/            # Ornament assets
    fonts/                # Bundled font files (free/open-licensed — verify before bundling)
```

Existing `api/routes/visuals.py` re-points its image route functions to these new modules.
The old route functions are archived in `old_image_generation/routes_visuals_image_parts.py`.

---

## 6. Library file shapes

### `libraries/layouts.yaml`

```yaml
layouts:
  - id: top_band
    description: "Narrow band across the top third; scene occupies bottom two thirds."
    baked_description: "Text elements float in a semi-transparent band across the top third of the frame."
    zones:
      locality:    {x: 0.05, y: 0.04, w: 0.90, h: 0.10, align: left}
      headline:    {x: 0.05, y: 0.14, w: 0.70, h: 0.06, align: left}
      price:       {x: 0.70, y: 0.04, w: 0.25, h: 0.10, align: right}
      cta_badge:   {x: 0.75, y: 0.85, w: 0.20, h: 0.08, align: right}
      footer:      {x: 0.05, y: 0.92, w: 0.90, h: 0.06, align: left}

  - id: lower_panel
    description: "Full-bleed scene above; text in a graded dark panel at the bottom third."
    baked_description: "Text clustered in a graded dark panel occupying the lower third of the frame."
    zones:
      locality:    {x: 0.05, y: 0.68, w: 0.70, h: 0.10, align: left}
      price:       {x: 0.70, y: 0.68, w: 0.25, h: 0.10, align: right}
      headline:    {x: 0.05, y: 0.78, w: 0.90, h: 0.07, align: left}
      cta_badge:   {x: 0.75, y: 0.86, w: 0.20, h: 0.07, align: right}
      footer:      {x: 0.05, y: 0.93, w: 0.90, h: 0.05, align: left}

  - id: full_bleed_gradient
    description: "Scene fills frame; text floats on a radial dark-to-transparent vignette at lower left."
    baked_description: "Text floats over a radial dark vignette at lower-left; rest of frame is clear photography."
    zones:
      locality:    {x: 0.04, y: 0.60, w: 0.55, h: 0.12, align: left}
      headline:    {x: 0.04, y: 0.73, w: 0.55, h: 0.07, align: left}
      price:       {x: 0.04, y: 0.81, w: 0.40, h: 0.08, align: left}
      cta_badge:   {x: 0.04, y: 0.90, w: 0.30, h: 0.07, align: left}
      footer:      null

  - id: side_rail
    description: "Narrow vertical rail on the right edge; full scene on left."
    baked_description: "Narrow vertical text rail on the right edge; the photographic scene fills the left."
    zones:
      locality:    {x: 0.73, y: 0.05, w: 0.24, h: 0.18, align: center}
      price:       {x: 0.73, y: 0.25, w: 0.24, h: 0.10, align: center}
      headline:    {x: 0.73, y: 0.38, w: 0.24, h: 0.30, align: center}
      cta_badge:   {x: 0.73, y: 0.80, w: 0.24, h: 0.08, align: center}
      footer:      null

  - id: framed_border
    description: "Thin decorative border inset; text clusters in the upper and lower border bands."
    baked_description: "Thin decorative inset frame; text occupies upper and lower border bands."
    zones:
      locality:    {x: 0.05, y: 0.04, w: 0.65, h: 0.09, align: left}
      price:       {x: 0.72, y: 0.04, w: 0.23, h: 0.09, align: right}
      headline:    {x: 0.05, y: 0.84, w: 0.65, h: 0.07, align: left}
      cta_badge:   {x: 0.72, y: 0.84, w: 0.23, h: 0.07, align: right}
      footer:      {x: 0.05, y: 0.92, w: 0.90, h: 0.05, align: center}

  - id: editorial_split
    description: "Upper 60% is pure photography; lower 40% is a solid dark editorial block with structured text."
    baked_description: "Upper 60% pure photography; lower 40% a solid editorial dark block with structured text grid."
    zones:
      locality:    {x: 0.04, y: 0.62, w: 0.90, h: 0.12, align: left}
      price:       {x: 0.65, y: 0.62, w: 0.30, h: 0.12, align: right}
      headline:    {x: 0.04, y: 0.75, w: 0.90, h: 0.08, align: left}
      eyebrow:     {x: 0.04, y: 0.58, w: 0.60, h: 0.04, align: left}
      cta_badge:   {x: 0.04, y: 0.84, w: 0.30, h: 0.07, align: left}
      footer:      {x: 0.04, y: 0.92, w: 0.90, h: 0.05, align: left}
```

> Each `zones` entry is **% of image dimensions**. Compositor multiplies by pixel size.
> `null` means this element is not rendered for this layout.
> Add more layouts freely — the LLM picks `layout_id`, dedup prevents repetition.

### `libraries/palettes.yaml`

```yaml
palettes:
  - id: charcoal_gold
    locality_color: "#C9A84C"
    headline_color: "#F5F0E8"
    body_color: "#D4CFC6"
    price_bg: "#1A1A1A"
    price_text: "#C9A84C"
    price_border: "#C9A84C"
    cta_bg: "#C9A84C"
    cta_text: "#1A1A1A"
    scrim_color: "#000000"
    scrim_opacity: 0.55

  - id: navy_cream
    locality_color: "#F5F0E8"
    headline_color: "#F5F0E8"
    body_color: "#C8C0B0"
    price_bg: "#0D1B2A"
    price_text: "#F5F0E8"
    price_border: "#8AACC8"
    cta_bg: "#8AACC8"
    cta_text: "#0D1B2A"
    scrim_color: "#0D1B2A"
    scrim_opacity: 0.60

  - id: forest_ivory
    locality_color: "#F2EDE0"
    headline_color: "#F2EDE0"
    body_color: "#C5BDA8"
    price_bg: "#1C2B1E"
    price_text: "#F2EDE0"
    price_border: "#7A9E7E"
    cta_bg: "#7A9E7E"
    cta_text: "#1C2B1E"
    scrim_color: "#1C2B1E"
    scrim_opacity: 0.55

  - id: burgundy_sand
    locality_color: "#F5ECD7"
    headline_color: "#F5ECD7"
    body_color: "#D6C9B0"
    price_bg: "#3D0A16"
    price_text: "#F5ECD7"
    price_border: "#C4886A"
    cta_bg: "#C4886A"
    cta_text: "#3D0A16"
    scrim_color: "#3D0A16"
    scrim_opacity: 0.58

  - id: slate_silver
    locality_color: "#E8E8F0"
    headline_color: "#E8E8F0"
    body_color: "#B8B8C8"
    price_bg: "#252535"
    price_text: "#E8E8F0"
    price_border: "#9090B0"
    cta_bg: "#9090B0"
    cta_text: "#252535"
    scrim_color: "#252535"
    scrim_opacity: 0.52

  - id: warm_terracotta
    locality_color: "#FFF5E6"
    headline_color: "#FFF5E6"
    body_color: "#E8D5BB"
    price_bg: "#3D1F0A"
    price_text: "#FFF5E6"
    price_border: "#C87941"
    cta_bg: "#C87941"
    cta_text: "#3D1F0A"
    scrim_color: "#3D1F0A"
    scrim_opacity: 0.56
```

> `luxury ≠ only gold` — these 6 palettes give the batch genuine visual range.
> Add more; the LLM picks `palette_id`; BatchDedup prevents two ads sharing the same palette.

### `libraries/type_pairings.yaml`

```yaml
type_pairings:
  - id: playfair_montserrat
    display_font: "Playfair Display ExtraBold"   # heavy serif — locality, headline
    body_font: "Montserrat Medium"                # geometric sans — price, body, footer
    accent_font: null                             # no third style for this pairing
    tone: refined                                 # use for both dark_luxury + bright_aspirational

  - id: cormorant_inter
    display_font: "Cormorant Garamond SemiBold"
    body_font: "Inter SemiBold"
    accent_font: "Cormorant Garamond Light Italic" # eyebrow only
    tone: refined

  - id: abril_source
    display_font: "Abril Fatface"
    body_font: "Source Sans Pro Regular"
    accent_font: null
    tone: editorial

  - id: dm_serif_nunito
    display_font: "DM Serif Display Regular"
    body_font: "Nunito Sans Light"
    accent_font: null
    tone: aspirational
```

> **Font licence check (required before bundling):** Playfair Display, Cormorant Garamond,
> Inter, Montserrat, Abril Fatface, Source Sans Pro, DM Serif Display, Nunito Sans are all
> available under SIL OFL 1.1 via Google Fonts. Download from fonts.google.com and place
> under `libraries/fonts/`. If a font is unavailable, pick a comparable OFL alternative and
> update this file — do not bundle unlicensed fonts.

### `libraries/scene_library.yaml`

```yaml
anchors:
  private_retreat:
    creative_brief: "Solitary luxury; stillness; one person or empty space; high-end calm."
    scene_families:
      - balcony_dawn_solo
      - reading_window_interior
      - pool_edge_dusk_solo
      - skyline_terrace_single

  social_family:
    creative_brief: "Warm aspiration; people present; social energy inside a beautiful home."
    scene_families:
      - kitchen_morning_couple
      - living_room_family_evening
      - rooftop_gathering
      - dining_celebration

  interior_signature:
    creative_brief: "Empty room; light quality + material are the hero; no people."
    scene_families:
      - diagonal_light_marble_floor
      - dusk_city_through_glazing
      - staircase_geometry
      - lobby_vanishing_point

  exterior:
    creative_brief: "Building facade in urban context; three-quarter angle; twilight preferred."
    scene_families:
      - blue_hour_facade
      - street_level_approach
      - aerial_oblique

rotating_pool:
  - spa_interior_empty
  - rooftop_terrace_couple
  - double_height_living
  - glass_balcony_morning
  - kitchen_hero_detail
  - library_reading_nook
  - pool_interior_reflection
  - corridor_vanishing_point
  - panoramic_cityscape_window
  - master_bedroom_sunrise
```

---

## 7. What is DELETED (conflict sources, never ported)

| What | Where it lived | Why deleted |
|------|---------------|-------------|
| `design_principles.yaml` entirely | `crews/content_crew/config/design_principles.yaml` | Was a second home for the same rules as image_variants.yaml + task_composer — caused conflicts |
| `compose_description()` ~250-line rule dump | `task_composer.py` | Replaced by the short art_director.py prompt (library selection only) |
| `composition_notes` concept | `VisualPromptOutput`, `build_ad_prompt` composition path | Layout is now in layouts.yaml + code, not LLM prose |
| `_build_typography_block()` (6 info_band_style branches) | `image_service.py` | Replaced by compositor.py zones from layouts.yaml |
| `AD_STRUCTURES` prose blocks | `image_service.py` | Replaced by layouts.yaml data |
| Duplicated prose tails (MINIMUM SCALE / PRICE PROMINENCE / NEGATIVE SPACE / ELEMENT SPACING / TEXT FIDELITY …) | `image_service.py`, `task_composer.py` | Re-expressed once as code in the legibility engine |
| `structure_map` / `allowed_recipes` / `recipe_tag` | `image_variants.yaml` | Replaced by `layout_id` from layouts.yaml |
| `design_principles.yaml` recipe/style-ref exemplar machinery | `design_principles.yaml` | Deleted entirely |
| Legacy `build_ad_prompt` dual-path assembly | `image_service.py` | Replaced by compositor (RENDER) + baked_prompt (BAKED) |

---

## 8. Reference-creative handling (rebuilt)

Text-replacement failures disappear because **Ideogram no longer renders ad text in RENDER
mode — code does.**

Vision extracts the reference image's layout as structured data (zone boxes + which element
sits where + palette). This is stored as a synthetic `layout_id` for that reference.

### Mode REPLACE ("our ad in their style")

1. Generate fresh scene via `scene_renderer.py` OR remix the reference photo via
   `ideogram_client.call_ideogram_remix()` for closer photographic style match.
2. Code-composite OUR brief text into the vision-extracted layout → **exact text, every time**.
3. No LLM text generation involved in the text elements.

### Mode RELAYOUT ("keep their layout/text/stamps, new scene")

1. Vision extracts reference text strings + background element positions.
2. Generate new AI scene via `scene_renderer.py`.
3. Composite extracted reference elements over the new scene.

Both modes run through the RENDER compositor. BAKED remains available via `IMAGE_MODE=baked`
override. Logic lives in `api/services/image/reference.py`.

---

## 9. Creativity & design contract

The redesign must not trade variety for reliability. These are the constraints the
art_director LLM prompt and the libraries are built around:

### Typography
- Max **2–3 type styles per image** (display serif for locality/headline; geometric sans for
  price/body/footer; at most one accent style for eyebrow).
- Tone: **refined-premium** — not plain/system (never Arial/Calibri feel) and not
  decorative/funky (no scripts that hurt instant legibility).
- LLM picks `type_pairing_id` per ad from the library; dedup discourages consecutive reuse.

### Colour
- Luxury ≠ only gold. `palettes.yaml` holds 6 distinct palettes (navy, charcoal, forest,
  burgundy, slate, warm terracotta) — extend freely.
- LLM picks `palette_id`; BatchDedup guarantees no two ads in a batch share the same palette.

### Layout structure
- A library of **distinct layouts** (not one skeleton): top_band, lower_panel,
  full_bleed_gradient, side_rail, framed_border, editorial_split.
- LLM picks `layout_id`; dedup prevents consecutive variants feeling templated.

### Scene breathing room
- Photographic scene is the hero: **≈80–90% of frame**.
- Text occupies designed calm zones, never crammed.
- Compositor never shrinks an element below the minimum floor — it grows the zone or adds
  a scrim before reducing size.

### Variety math
Real variety = **(infinite AI photography) × (layout library) × (palette library) ×
(type pairings) × (ornament set)**, deduped per batch. This is what keeps 20–30 creatives
from feeling repetitive even though text rendering is deterministic.

### Anti-repetition
BatchDedup runs on `(layout_id, palette_id)` and should also discourage reusing the same
`type_pairing_id` or `text_anchor` back-to-back within a batch.

---

## 10. Legibility engine specification

This is the code that replaces the old prose rules. Implement in `compositor.py`.

```python
MIN_SIZE = {
    "locality": 48,     # pt equivalent at 1080px canvas
    "price":    28,
    "headline": 22,
    "eyebrow":  16,
    "body":     14,
    "footer":   12,
}

CONTRAST_THRESHOLD = 0.4   # luminance above which a scrim is required

def _fit_text(draw, text, zone_box, font_path, max_pt, min_pt, color):
    """Fit text into zone_box at the largest pt that fits, never below min_pt."""
    ...

def _ensure_contrast(image, zone_box, scrim_color, scrim_opacity):
    """Sample luminance under zone; add scrim if above threshold."""
    ...
```

The legibility engine is called for every text element before rendering. It is the single
source of truth for size floors and contrast requirements — no other file should state them.

---

## 11. Sanitizer (`sanitizer.py`)

Single data-driven ban list. Port from old `sanitize_image_prompt` but remove the inline
rule prose — only the data list and the match/replace logic.

**Banned fabrications:**
- Phone numbers, URLs, possession dates, RERA numbers, floor counts, sq ft in isolation
- Brand names, competitor names, logos, wordmarks
- Invented superlatives with no brief basis ("best", "only", "India's finest" unless in USPs)

**In RENDER mode:** sanitizer only validates `scene_prose` (the Ideogram scene prompt).
Text strings come from the brief; they are never LLM-generated in RENDER mode.

**In BAKED mode:** sanitizer validates the full assembled prompt before sending to Ideogram.

---

## 12. Routes integration (no new route signatures)

Existing routes in `api/routes/visuals.py` re-point to the new modules:

| Route | Old call | New call |
|-------|----------|----------|
| `POST /generate-images/{run_id}` | `imgs.build_ad_prompt` + `imgs.call_ideogram` | `scene_renderer.render()` + `compositor.composite()` OR `baked_prompt.build()` + `ideogram_client.call()` |
| `POST /generate-prompt/{run_id}/{num}` | `task_composer.compose_description` → LLM | `art_director.build_ad_spec(variant_key, brief)` |
| `POST /regenerate-prompt/{run_id}` | prose LLM rewrite | `art_director.build_ad_spec()` re-run for that slot |
| `POST /generate-reference-variant/{run_id}` | `imgs.build_ad_prompt` with `composition_notes` | `reference.extract_layout()` + `compositor.composite()` |
| `POST /inpaint/{run_id}/{slug}` | `imgs.call_ideogram_inpaint` | `ideogram_client.call_inpaint()` (unchanged behaviour) |
| `POST /revert-logo/{run_id}/{slug}` | unchanged | unchanged |

The `AdSpec` JSON written to `visual_prompts.json` replaces the old `VisualPromptOutput`
structure. New required fields per entry:

```json
{
  "variant_key": "private_retreat",
  "prompt_num": 1,
  "scene_prose": "...",
  "layout_id": "lower_panel",
  "palette_id": "charcoal_gold",
  "type_pairing_id": "playfair_montserrat",
  "text_anchor": "lower_panel",
  "ornament_id": "",
  "tone": "dark_luxury"
}
```

---

## 13. Tests

Mirror the archived test intent from the old test files. Minimum test coverage for the new
pipeline:

| Test | What to assert |
|------|---------------|
| `test_brief_model` | All required fields present; optional fields absent when not in input |
| `test_variant_planner` | Fixed anchors always present; rotating pool distinct across two calls |
| `test_batch_dedup` | No two entries share `(layout_id, palette_id)` after dedup |
| `test_art_director_mock` | Mocked LLM returns valid `AdSpec`; all IDs resolve in libraries |
| `test_scene_renderer` | Prompt contains only scene prose + quality cues; no text strings |
| `test_compositor_text_floors` | All rendered text elements ≥ MIN_SIZE for their type |
| `test_compositor_contrast` | Scrim added when sampled luminance > CONTRAST_THRESHOLD |
| `test_baked_prompt` | Output is ≤ 250 words; contains locality, price, headline strings verbatim |
| `test_sanitizer` | Phone numbers / URLs / RERA stripped; brief text passes through |
| `test_reference_extract` | Layout extraction returns zone boxes with valid % coordinates |

---

## 14. What the implementing session needs

### Nothing mandatory beyond the repo + this file

The kickoff prompt at the top tells the session to self-serve context from memory,
graphify, and `project_context/`.

### Recommended to point at explicitly

- `project_context/ads/` — the look to match (reference ads)
- `project_context/ads_layout_analysis.json` — existing layout analyses
- `project_context/references_analyses.json` — reference creative analyses
- `project_context/ad_images_examples/` — brand logo source
- `project_context/brand_voice.md` — copy tone and voice

### Only needed if the user wants changes from the locked decisions

- A different font choice (else the session picks free/open licence fonts and lists them
  for approval before bundling).
- Exterior opt-in default change.
- Target creatives-per-campaign number (default: grow toward 20–30).

### API keys (must already be in `.env`)

- `IDEOGRAM_API_KEY` — for live Ideogram generation
- `VISION_MODEL` — for reference image layout extraction (e.g. `gemini/gemini-2.5-flash`)
- `CREATIVE_MODEL` — for art_director.py LLM call
