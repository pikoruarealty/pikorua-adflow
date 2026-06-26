# Image Pipeline Map
## Every generation path, what overrides what, where every phase lives, and why recipes don't stick
### Last updated: 2026-06-26

---

## 0. Three Generation Paths (TL;DR)

Every ad image is created by one of three independent code paths. They share `call_ideogram()` at the end but diverge wildly in how they build the prompt. The root cause of "recipe fixes get overridden" is that these paths do NOT share the same assembly function, and the inpaint/regenerate paths skip most of it.

```
Path 1: Main batch generation  →  /generate-images/{run_id}
Path 2: Brush edit (inpaint)   →  /inpaint/{run_id}/{prompt_slug}
Path 3: Reference creative     →  /generate-reference-variant/{run_id}

Supporting:
  /generate-prompt/{run_id}/{n}   — on-demand lazy prompt write (same as crew, no CrewAI)
  /regenerate-prompt/{run_id}     — old-style prose rewrite (BYPASSES ENTIRE PIPELINE)
  /save-prompt / /revert-prompt   — prompt override CRUD
```

---

## 1. Full Pipeline Flow — Path 1 (Main Batch)

```
(real pipeline — LAZY_IMAGE_PROMPTS=1 default)
ContentCrew.crew() runs copy tasks only; visual tasks are skipped.
Placeholder visual_prompts.json written (variant_key + prompt_num, no scene_prose).

User clicks "Write Prompt & Generate" for each slot:
  → POST /generate-prompt/{run_id}/{n}
      compose_description(variant_key)   [task_composer.py]
        → ~800-word task description with scene_photography_brief +
          variant creative_brief + allowed_recipes (shuffled) + property brief
      litellm.completion(CREATIVE_MODEL, temp=0.85, max_tokens=1200)
        → JSON: scene_prose + composition_notes + headline + palette_tag +
                recipe_tag + scene_tag + tone_tag + logo_corner + eyebrow
      VisualPromptOutput validation (pydantic)
      Upsert into visual_prompts.json
      Return build_ad_prompt(entry, brief, variant_key) for preview

  → POST /generate-images/{run_id}
      _load_visual_prompts(review_folder)   [reads visual_prompts.json]
      For each entry → prompt dispatch (3 branches, see §3):
        BRANCH B (normal): build_ad_prompt() + sanitize_image_prompt(assembled=True)
        BRANCH A (override): raw prose + sanitize_image_prompt(assembled=False)
        BRANCH C (legacy): raw ideogram_prompt + sanitize_image_prompt(assembled=False)
      call_ideogram(sanitized, key, speed, aspect, recipe_tag)
        → POST https://api.ideogram.ai/v1/ideogram-v4/generate
        → 1792x2240 PNG bytes
      Save as images/image_{n}.png
      composite_logo(out_path, BRAND_LOGO_PATH, corner=logo_corner)
        → Backup to .logo_backup/ first

(crew-based — LAZY_IMAGE_PROMPTS=0)
ContentCrew.crew() runs ALL tasks including visual tasks.
Visual tasks use same compose_description() → LLM → VisualPromptOutput.
output_saver.save_for_review() collects all outputs:
  → dedupe_visual_batch() enforces distinct palette_tag + recipe_tag
  → Writes visual_prompts.json
```

---

## 2. Files and Their Roles

### `crews/content_crew/config/image_variants.yaml`
**What it controls:**
- 5 active variant keys: `lifestyle_private_retreat`, `lifestyle_social_home`, `lifestyle_dynamic_a`, `lifestyle_dynamic_b`, `interior_signature_moment`
- Opt-in (never in default batch): `exterior_establishing_shot`
- Legacy (backward compat only): `lifestyle_city_connection` — never appears in default batch
- Per variant: `allowed_recipes`, `allowed_palettes`, `scene_pool`, `creative_brief`, `sample_ready_cta`, `structure_map` (fallback when no recipe)
- `scene_photography_brief` — global LLM instructions for how to write scene_prose
- `hard_bans` — single source of truth for `sanitize_image_prompt()` (claims, conditional phrases, fabrication never-invent)

**What it does NOT control:**
- Which recipe is actually selected — that's the LLM's choice within `allowed_recipes`
- Layout of the final prompt — that's `build_ad_prompt()`
- Typography details — those are in `design_principles.yaml` + `image_service.py`

**When to edit:**
- Adding/removing scene pool entries
- Adding/removing allowed recipes per variant (when disabling a recipe, remove from here too)
- Changing sample_ready_cta per variant
- Adding new hard bans

---

### `crews/content_crew/config/design_principles.yaml`
**What it controls:**
- All recipe definitions. Each recipe has:
  - `name` — the recipe_tag key
  - `disabled: true` — must be set here to disable; also remove from all `allowed_recipes` in image_variants.yaml
  - `info_band_style` — controls which bottom-band template `_build_typography_block()` uses
    (ONLY applies when `composition_driven=False` — currently almost never active)
  - `layout_type` — structure label; used by `_expand_structures()` in image_service.py
  - `lighting`, `subject_rule`, `negative_space_rule`, `type_treatment` — injected as the
    "Design grammar reference" block in the final prompt (advisory only when composition_notes present)
  - `footer_backing` — hex for solid panel colour
  - `text_primary_hex` — primary text colour for the recipe
- `layout_discipline` — injected for full_detail recipes in legacy path
- `detail_principles`, `vocabulary_additions` — supplemental reference

**Currently active recipes (8):**
`the_architectural_dead_zone`, `the_editorial_triptych`, `the_glass_morphism_shield`, `the_golden_archway`, `the_horizon_anchor`, `the_open_room_anchor`, `the_sky_text_canvas`, `the_zoned_triptych`

**Currently disabled (6):**
`the_sky_chandelier`, `the_depth_integration`, `the_dark_water_canvas`, `the_physical_3d_intrusion`, `the_backlit_silhouette`, `the_zenith_gaze`

**When to edit:**
- Disabling a bad recipe: add `disabled: true`, AND remove from `allowed_recipes` in image_variants.yaml
- Updating recipe lighting/composition instructions
- Adding a new recipe

---

### `crews/content_crew/task_composer.py`
**What it controls:**
- `_RECIPES_BY_NAME` dict: loaded once at module init; filters `disabled: true` recipes
- `compose_description(variant_key, ...)` (L174): builds the ~800-word LLM task description from variant config + property brief placeholders. Returns string with {city}/{locality}/etc. placeholders that CrewAI or the lazy endpoint fills.
- `dedupe_visual_batch(entries)` (L124): enforces distinct palette_tag + recipe_tag across the 5-ad batch; reassigns later duplicates silently (see Bug §7)
- `get_variant_meta(variant_key)`: returns raw variant config dict
- `VisualPromptOutput` (Pydantic model): schema the LLM must match

**When to edit:**
- Changing the LLM's instruction template for composition_notes (the ~500-word instruction tail)
- Changing deduplication logic
- Adding new output fields to VisualPromptOutput

---

### `api/services/image_service.py`

This is the largest file and the most critical for prompt quality. Three subsystems:

#### `_build_typography_block(entry, brief, allowed_roles, info_band_style, composition_driven)` (L~688)

**Two modes (the mode switch is critically important):**

**`composition_driven=True`** (used when `composition_notes` exists in entry — almost always now):
- Emits a flat text-string list ONLY: "Primary Headline: ...", "City: ...", "Price: ...", etc.
- The `info_band_style` parameter is PASSED IN but COMPLETELY IGNORED — function returns early before reaching the band style code. **This is a bug.**
- Footer/spec list built from `brief["usps"]`; does NOT pad with defaults in CD mode
- Output goes AFTER composition_notes in the final prompt, creating a duplicate listing of text elements

**`composition_driven=False`** (legacy path, no composition_notes — almost never active now):
- Full templated output with band styles (icon_grid_strip, price_hero_strip, asymmetric_band, strip_three_col, compact_spec_row, column_footer)
- `info_band_style` actually controls the template here
- Price, badge, BHK config are laid out per the band template

**When to edit:**
- Change how price is formatted (₹ vs Rs)
- Change BHK config placement logic
- Change footer padding defaults
- Fix the CD mode to respect info_band_style (see Bug §2)

#### `build_ad_prompt(entry, brief, variant_key)` (L1047)

**The core assembly function. Two sub-paths:**

**Composition-driven path (when `entry["composition_notes"]` is non-empty — the normal path):**
1. Rebuilds typography_block in composition_driven=True mode (flat strings, info_band_style ignored)
2. Loads `_PALETTE_COMPACT[palette_tag]` — hex tokens only, no placement language
3. If recipe present: formats it as advisory "grammar reference (inform the approach, do not override the composition notes)"
4. Final prompt order (every block is a potential conflict with the others):
   - scene_prose
   - "Produce a finished luxury..." preamble
   - Human subjects dress code
   - CLEAN GLAZING ZONE rule
   - PEOPLE DO NOT DISPLACE TEXT rule
   - `layout_section` = "Composition and layout: {composition_notes}" ← LAYOUT AUTHORITY
   - TEXT COLOUR IS NOT ALWAYS GOLD
   - "Text strings to render: {typography_block}" ← DUPLICATE LISTING
   - Colour tokens
   - TEXT COLOUR IS PER-ELEMENT / SPEC VISIBILITY
   - `_TYPEFACE_QUALITY` block (~400 words)
   - `recipe_section` (advisory recipe grammar — subordinate to composition_notes)
   - COMPOSITIONAL DISTRIBUTION
   - PRICE & CTA PROMINENCE ("within central 70-80% focus area" ← CONTRADICTS off-axis placement in composition_notes)
   - SCENE NEGATIVE SPACE
   - ELEMENT SPACING
   - Legibility rule
   - CONFIGURATION TYPE RULE
   - FOOTER GRID GEOMETRY
   - MINIMUM SCALE (~300 words)
   - PROJECT NAME BAN
   - TEXT FIDELITY block
   - "Aspect ratio 4:5."

**Legacy path (no composition_notes — only fallback, rarely active):**
- Uses `AD_STRUCTURES[recipe.layout_type or variant structure]` as layout authority
- Recipe block is PRIMARY art-direction, not advisory
- `info_band_style` band templates are actually used by `_build_typography_block()`
- Simpler prompt, less contradictory

**When to edit:**
- The boilerplate blocks (PRICE PROMINENCE, MINIMUM SCALE, etc.)
- The "central 70-80% constraint" in PRICE & CTA PROMINENCE — contradicts off-axis composition_notes
- `_TYPEFACE_QUALITY` constant (typeface guidance, number disambiguation)

#### `PALETTE_CONFIGS` and `_PALETTE_COMPACT` (L~394–451)
Six palettes. Full descriptions used in legacy path. Compact hex-only used in composition-driven path.

| palette_tag | Primary text | Backing | Feel |
|------------|-------------|---------|------|
| navy_gold | Gold #C9A84C | Deep navy #0D1B2A | Formal, classic launch |
| charcoal_gold | Gold #C9A84C | Warm charcoal #2B2420 | Bold, contemporary |
| forest_gold | Gold #C9A84C | Deep forest green #1C3325 | Botanical luxury |
| burgundy_gold | Amber-gold #B8860B | Dark burgundy #3D0C02 | Heritage opulence |
| slate_cream | Gold #C9A84C | Cool slate #1E2430 | Architectural, cooler |
| ivory_warmth | Gold #C9A84C | NONE — text on scene surfaces | Warm, bright aspirational |

#### `AD_STRUCTURES` (L~455–553)
Five structure templates. Only used when composition_notes is absent (legacy fallback).

`bordered_campaign`, `structured_split`, `immersive_fullbleed`, `zoned_triptych` (detailed), `editorial_triptych` (ornate).

Also: `solid_footer_container`, `glass_morphism_panel`, `asymmetrical`, `framing_device`, `open_room_anchor` from vocabulary_additions.

#### `sanitize_image_prompt(raw, brief, assembled=False)` (L~333)

| Stage | What it does | Skipped when assembled=True |
|-------|-------------|----------------------------|
| 1 | Strip absolute-banned phrases | No |
| 2 | Strip conditional claims (RERA, awards) | No |
| 3 | Strip never-invent sentences | No |
| 4 | Strip tech noise (logo/font/pixel instructions) | **YES** |
| 5 | Normalize locality | No |
| 6 | Handle sample-ready badge language | **YES** |
| 7 | Enforce canonical price format | **YES** |
| + | Append `_ANTI_LOGO_GUARD` | No |

#### `call_ideogram(prompt, key, speed, aspect, recipe_tag)` (L~1455)
→ `POST https://api.ideogram.ai/v1/ideogram-v4/generate`
→ multipart/form-data: text_prompt, resolution (1792x2240 for 4x5), rendering_speed
→ Optional: style_reference_images (when IDEOGRAM_STYLE_REF=1)

#### `call_ideogram_inpaint(image_bytes, mask_bytes, prompt, key, aspect)` (L~1541)
→ `POST https://api.ideogram.ai/v1/ideogram-v4/edit`
→ multipart: image, mask, prompt, resolution, rendering_speed
→ **This URL may be wrong — see Bug §1**

#### `call_ideogram_remix(image_bytes, prompt, key, ...)` (L~1634)
→ `POST https://api.ideogram.ai/v1/ideogram-v4/remix`
→ Defined but NEVER CALLED from any code path — dead code. See Bug §6.

#### `composite_logo(image_path, logo_path, corner)` (L~1782)
PIL-based; adds soft rounded scrim; backs up original to `.logo_backup/` first.

#### Reference image analysis (L~106–244)
- `analyze_reference_image(img_path)` — vision LLM describes creative energy; cached to `.desc.txt`
- `extract_reference_ad_layout(img_path)` — vision LLM extracts text element positions; cached to `.layout.txt`
- `build_reference_images_context()` — builds text block for crew context from all uploaded reference ads

---

### `api/routes/visuals.py`

All image HTTP endpoints. Key routes:

| Endpoint | What it does |
|----------|-------------|
| `POST /generate-images/{run_id}` | Main batch generation (Path 1) |
| `POST /generate-prompt/{run_id}/{n}` | On-demand lazy prompt write |
| `POST /regenerate-prompt/{run_id}` | Rewrite one prompt as OLD-STYLE PROSE (bypasses pipeline) |
| `POST /save-prompt/{run_id}/{n}` | Save user-edited prose to prompt_overrides in edits.json |
| `POST /revert-prompt/{run_id}/{n}` | Remove override from edits.json |
| `POST /inpaint/{run_id}/{slug}` | Brush edit — mask + edit_prompt → Ideogram edit API |
| `POST /generate-reference-variant/{run_id}` | Reference creative (remix or new_scene mode) |
| `GET /image/{run_id}/{filename}` | Serve image file |
| `DELETE /image/{run_id}/{fname}` | Delete image |
| `POST /upload-image/{run_id}/{variant}` | User uploads their own image |
| `POST /revert-image/{run_id}/{variant}` | Restore from .ai_backup |
| `POST /revert-logo/{run_id}/{slug}` | Restore from .logo_backup (removes logo) |
| `POST /assign-image/{run_id}/{n}` | Link a copy variant to an image number |

---

### `utils/output_saver.py`
- `save_for_review()`: called after crew run; collects visual task outputs, runs dedup, writes `visual_prompts.json`
- In lazy mode: writes placeholder entries (variant_key + prompt_num only, no scene_prose)

### `_manual_llm_outputs.py` (project root)
Manual simulation of LLM output — hardcoded POOL-A + POOL-B per variant for testing.
Only used for testing; NOT in the production path.

```python
BRIEF = { ... }  # Property data — keep in sync with what the real pipeline receives
VARIANT_POOLS = {
    "lifestyle_private_retreat": [POOL_A_dict, POOL_B_dict],
    ...
}
```
Random pick + `dedupe_visual_batch()` runs on every execution.
Output: printed to stdout or `anamika_heights_prompts.txt`.

---

## 3. Prompt Dispatch Logic in generate_images()

For each entry in visual_prompts.json, three branches are checked in order:

```python
custom_or_saved = (
    payload.custom_prompts.get(i)
    or saved_edits.get("prompt_overrides", {}).get(str(i))
)

if custom_or_saved:
    # BRANCH A: User override or /regenerate-prompt output
    # NO build_ad_prompt(). NO recipe. NO typography templates.
    # Runs full sanitizer (assembled=False → stages 4/6/7 active, may strip things)
    sanitized = imgs.sanitize_image_prompt(custom_or_saved, entry_brief)

elif entry.get("scene_prose"):
    # BRANCH B: Structured format — the intended path
    raw_prompt = imgs.build_ad_prompt(gen_entry, entry_brief, variant_key)
    sanitized = imgs.sanitize_image_prompt(raw_prompt, entry_brief, assembled=True)

else:
    # BRANCH C: Legacy prose ideogram_prompt (old runs pre-Session 28)
    raw_prompt = entry.get("ideogram_prompt", "")
    sanitized = imgs.sanitize_image_prompt(raw_prompt, entry_brief)
```

**Any prompt slot touched by /regenerate-prompt or user edit goes to Branch A and skips the entire structured pipeline.**

---

## 4. Path 2 — Brush Edit (Inpaint)

### Frontend (campaign_detail.html:1246–1391)

1. User opens modal from image card brush button (`openInpaint(n)` or `openInpaintFile(fname)`)
2. User paints on HTML canvas overlay over the image
3. "Apply" click → `applyInpaint()`:
   - Creates black mask canvas; painted pixels (alpha > 30) → white
   - Posts `FormData`: `mask_png`, `edit_prompt`, optional `source_file`
4. On success: calls `closeInpaint()`, `loadDetail()`, `renderVisuals()`

### Backend (`visuals.py:554` → `image_service.py:1541`)

```
Resolve source image (latest _v{k}.png variant or explicit source_file)
call_ideogram_inpaint(image_bytes, mask_bytes, edit_prompt, key, aspect="4x5")
  → POST https://api.ideogram.ai/v1/ideogram-v4/edit
  → multipart: image, mask, prompt, resolution, rendering_speed
  → Returns PNG bytes
Save as image_{slug}_v{k}.png
Return {"file": "image_1_v2.png", "prompt_slug": "1"}
```

**What inpaint SKIPS entirely:**
- `build_ad_prompt()` — no recipe, no typography block, no palette assembly
- `sanitize_image_prompt()` — raw edit_prompt goes straight to Ideogram
- Logo compositing — logo is NOT re-applied after inpaint

---

## 5. Path 3 — Reference Creative Generation

### Mode A: "remix" (misleadingly named — does NOT use Ideogram's remix API)

```
extract_reference_ad_layout(ref_path)     → WHERE text elements sit (.layout.txt cached)
analyze_reference_image(ref_path)         → creative mood/energy (.desc.txt cached)

Build entry: {
  scene_prose:        ref_scene (mood description),
  composition_notes:  ref_layout (extracted text positions),
  headline:           first headline from effective_meta(),
  palette_tag:        "charcoal_gold"  (hardcoded),
  recipe_tag:         ""               (no recipe),
  tone_tag:           "dark_luxury"    (hardcoded)
}

build_ad_prompt(entry, brief, "interior")
  → Takes composition_driven path (composition_notes from reference layout)
  → No recipe active → no recipe grammar

call_ideogram(prompt, key, speed, aspect)   ← standard generate, NOT remix API
Save as image_r{k}.png → composite_logo → save provenance to edits.json
```

### Mode B: "new_scene"

```
extract_reference_ad_layout(ref_path)      → same layout extraction, cached

get_variant_meta(scene_variant)            → creative_brief, scene_pool, allowed_palettes

LLM scene_prose call (120-140 words, photography only, no layout language)
  → CREATIVE_MODEL, temp=0.85, max_tokens=250

Build entry: {
  scene_prose:        fresh LLM scene prose,
  composition_notes:  ref_layout (reference's extracted text positions),
  palette_tag:        allowed_palettes[0] from chosen variant,
  tone_tag:           default_tone_bias from chosen variant,
  recipe_tag:         ""  (no recipe in either reference mode)
}

build_ad_prompt(entry, brief, scene_variant)
  → Composition-driven path (ref_layout as composition authority)
  → No recipe

call_ideogram() → save → composite_logo → edits.json
```

---

## 6. The /regenerate-prompt Endpoint (Silent Pipeline Bypass)

`POST /regenerate-prompt/{run_id}` (`visuals.py:286`)

The user triggers this to rewrite a specific prompt slot. Output saved to `edits.json.prompt_overrides[str(n)]`.

**What it generates:** Old-style 200-400 word flowing prose prompt (pre-Session 28 format).

**System prompt used:** Hardcoded per-slot `_zone_rules` dict that describes scene types (Architectural Perspective / Lifestyle Moment / Iconic Detail / Exterior / Interior Signature) — does NOT reference `image_variants.yaml` at all.

**What it skips:** build_ad_prompt(), recipe selection, composition_notes, palette system, typography block templates, dedupe.

**Effect:** Next time generate_images() runs for that slot, it enters Branch A (override) and the entire structured pipeline is bypassed for that slot permanently until the override is reverted via `/revert-prompt`.

---

## 7. What Overrides What

```
Priority (highest → lowest) in the composition-driven path:
1. composition_notes (entry field, written by LLM or reference extraction)
   — SOLE layout authority in composition-driven path
2. Boilerplate rules in build_ad_prompt()
   — PRICE & CTA PROMINENCE, MINIMUM SCALE, COMPOSITIONAL DISTRIBUTION, etc.
   — These run after composition_notes and may contradict them
3. recipe block ("grammar reference — do not override the composition notes")
   — advisory only; image model may ignore it when prior instructions conflict
4. _build_typography_block() output
   — flat text strings in CD mode; a second listing of all elements
5. palette section (hex tokens only in CD mode)
```

**Known conflicts when composition_notes are present:**
- `PRICE & CTA PROMINENCE` says "within central 70-80% focus area" — contradicts composition_notes that say "price bottom-right, off-axis" or "mid-right"
- `_build_typography_block(composition_driven=True)` creates a second listing of ALL text elements including footer specs after the composition_notes — Ideogram sees the same specs described twice in different wording
- `FOOTER GRID GEOMETRY` block fires even when the chosen recipe uses `compact_spec_row` (no grid needed)
- The recipe's `info_band_style` is declared but ignored in CD mode (see Bug §2)

---

## 8. Root Cause Analysis: Why Recipes Don't Control Ad Layout

### Cause 1: composition_notes overrides the recipe structure

When composition_notes is present (which it always is now), `build_ad_prompt()` enters the composition-driven path where:
- `AD_STRUCTURES` templates are never injected
- `info_band_style` band templates in `_build_typography_block()` are never used
- The recipe block is injected only as "grammar reference — do not override"

The recipe's carefully defined structure (`editorial_triptych`, `zoned_triptych`, `icon_grid_strip`, etc.) is only honoured if the LLM independently wrote matching composition_notes. There is no mechanical enforcement.

### Cause 2: info_band_style is dead code in composition_driven mode

```python
# In _build_typography_block():
if composition_driven:
    ...
    return "\n".join(lines)   # ← returns here; info_band_style code is never reached
```

The recipe specifies `info_band_style: icon_grid_strip` or `asymmetric_band` — but in CD mode the image model never sees any template language for those layouts. It only gets whatever the LLM wrote in composition_notes. If the LLM described a three-column spec strip, that's what gets built. The recipe's footer layout choice has zero mechanical effect.

### Cause 3: The prompt is a ~2000-word instruction stack with competing authority

The composition-driven path assembles 15+ distinct instruction blocks, all giving placement guidance. An image model receiving this much contradictory input picks the path of least resistance — usually centre-stacking elements in obvious zones, ignoring recipe-specific layout nuances.

### Cause 4: /regenerate-prompt silently bypasses everything

Any slot touched by "Rewrite prompt" produces a legacy-format override. From that point on, no fix to build_ad_prompt(), recipes, or composition_notes can affect that slot until reverted.

### Cause 5: dedupe_visual_batch() may silently reassign recipe after LLM wrote composition_notes

If two variants pick the same recipe_tag, dedup reassigns the later one silently. The composition_notes the LLM wrote for recipe X may not match the reassigned recipe Y's info_band_style. The model receives composition_notes saying "icon grid footer" but the reassigned recipe expects "compact_spec_row".

### Cause 6: Reference variant modes use no recipe

Both reference modes hardcode `recipe_tag=""`. The reference layout is extracted by vision LLM (good), but no recipe grammar guides the typography treatment — no info_band_style, no lighting direction, no subject rules. The result mirrors the reference's text positions but lacks the recipe-specific typographic quality.

---

## 9. What Each Path Skips

| Phase | Main (Branch B) | Inpaint | Ref remix | Ref new_scene | Regen → Override |
|-------|----------------|---------|-----------|---------------|-----------------|
| LLM recipe selection | YES | NO | NO | NO | NO |
| build_ad_prompt() | YES | NO | YES | YES | NO |
| info_band_style template | YES (legacy only) | NO | NO (CD ignores) | NO (CD ignores) | NO |
| AD_STRUCTURES template | YES (legacy only) | NO | NO | NO | NO |
| _TYPEFACE_QUALITY block | YES | NO | YES | YES | NO |
| sanitize (assembled) | YES | NO (raw) | YES | YES | NO (legacy) |
| Logo compositing | YES | NO | YES | YES | N/A |
| dedupe_visual_batch | YES (at save) | NO | NO | NO | NO |

---

## 10. Data Flow: Files on Disk

```
outputs/pending_review/{timestamp}/
  visual_prompts.json        ← LLM structured outputs (5+ entries)
  images/
    image_1.png              ← Generated, logo composited
    image_2.png
    image_3.png
    image_4.png
    image_5.png
    image_r1.png             ← Reference variant (if generated)
    image_1_v2.png           ← Inpaint variant (if generated)
    .logo_backup/
      image_1.png            ← Pre-logo backup (used by /revert-logo)
    .ai_backup/
      image_1.png            ← User-upload backup (used by /revert-image)
  edits.json
  ad_copy.md
  targeting_brief.md
  copy_scorecard.md

edits.json structure:
{
  "meta": {
    "1": {"headline": "...", "body": "...", "added": false},
    ...
  },
  "prompt_overrides": {
    "1": "prose override text — saved by /save-prompt or /regenerate-prompt"
  },
  "reference_variants": {
    "1": {"reference_filename": "ad.jpg", "mode": "remix", "prompt_sent": "..."}
  }
}
```

---

## 11. Open Bugs

### Bug 1: Inpaint Apply appears to do nothing (popup stays open, no result shown)

The Ideogram v4 inpaint endpoint `https://api.ideogram.ai/v1/ideogram-v4/edit` is
likely incorrect or not available on the API key's plan. The backend raises a
`RuntimeError` which becomes HTTP 502. The frontend catches this and shows the
error in `#inpaint-status` text — but the modal intentionally stays open on error,
and the status element may not be in the viewport.

**Verify:** Check Ideogram's current API docs for the correct edit/inpaint endpoint.
Their v2 inpaint endpoint was `https://api.ideogram.ai/edit` (no versioning).
The `call_ideogram_inpaint()` function also passes `resolution` and `rendering_speed`
— check whether the edit endpoint accepts these.

**Secondary fix:** After a successful inpaint, `closeInpaint()` is called synchronously
before `loadDetail()` resolves. If `renderVisuals()` fails, the new thumbnail won't appear
but the modal is already closed. Add a try/catch around the reload.

### Bug 2: info_band_style is dead code in composition_driven mode

`_build_typography_block(composition_driven=True)` returns before the `if style == ...` band
template code. The recipe's `info_band_style` has no mechanical effect. The LLM is only
asked to describe the matching layout in composition_notes — but there's no enforcement.

**Fix location:** `image_service.py:_build_typography_block()` — after the flat text string
block in composition_driven mode, append a description of the recipe's expected footer
structure matching the `info_band_style` value:

```python
if composition_driven:
    ...
    # After emitting text strings, add footer format instruction
    if info_band_style and info_band_style != "strip_three_col":
        lines.append(f"\nFooter layout: {BAND_STYLE_DESCRIPTIONS[info_band_style]}")
    return "\n".join(lines)
```

### Bug 3: /regenerate-prompt produces legacy prose that bypasses all structured logic

The endpoint generates old-style 200-400 word prose. When saved as a prompt_override,
all recipe/palette/typography improvements are bypassed permanently for that slot.

**Fix:** Change `/regenerate-prompt` to produce JSON in the VisualPromptOutput format
and merge it back into visual_prompts.json as a proper entry, so generate_images()
uses Branch B rather than Branch A.

### Bug 4: Reference variant modes never select a recipe

Both modes hardcode `recipe_tag=""`. The reference layout guides text positions but
no recipe grammar guides lighting, subject treatment, or typographic character.

**Fix:** For `new_scene`, use `get_variant_meta(scene_variant)["allowed_recipes"][0]`
as a default, or let the UI offer a recipe selector.

### Bug 5: dedupe_visual_batch() may silently break composition_notes/recipe alignment

When dedup reassigns recipe Y instead of X, the LLM's composition_notes (written for X)
may describe a footer layout incompatible with Y's info_band_style.

**Fix:** Either run dedup BEFORE the LLM writes composition_notes (pre-selection), or
regenerate composition_notes for reassigned entries using the new recipe's requirements.

### Bug 6: call_ideogram_remix() is dead code

The function implementing the Ideogram v4 remix API (`/v1/ideogram-v4/remix`) is defined
in image_service.py:1634 and correct, but never called. The "remix" mode in
`/generate-reference-variant` calls the standard generate endpoint instead.

The Ideogram remix API is the right tool for "preserve this reference image's layout
while adapting text elements" — currently wasted capability. The current approach
(extract layout via vision LLM → inject as composition_notes → call generate) works but
is more expensive and less faithful to the reference's visual style.

---

## 12. Checklist for Any Pipeline Change

**When you disable a recipe:**
- [ ] Add `disabled: true` in `design_principles.yaml` under `name:`
- [ ] Remove from all `allowed_recipes` in `image_variants.yaml`
- [ ] Check `_manual_llm_outputs.py` — if any POOL uses that recipe_tag, update it

**When you change composition_notes format/instructions:**
- [ ] Check `_build_typography_block(composition_driven=True)` still produces compatible text
- [ ] Never use "rendered on the wall" — always "overlaid as flat typography in front of the dark zone"
- [ ] Always use `₹` (not `Rs`) for price references
- [ ] Check that composition_notes footer language doesn't conflict with the flat text list that follows it

**When you add a new recipe:**
- [ ] Add to `design_principles.yaml` with all required fields including `info_band_style`
- [ ] Add to `allowed_recipes` in appropriate variants in `image_variants.yaml`
- [ ] Add the recipe_tag to relevant POOL entries in `_manual_llm_outputs.py` for testing

**When you change property brief fields:**
- [ ] Update `BRIEF` in `_manual_llm_outputs.py` to match
- [ ] Update `usps` list — drives what appears in the footer spec strip
- [ ] `cheque_only: True` adds "100% CHEQUE PAYMENT" as a spec item

**When you add a new variant:**
- [ ] Add to `image_variants.yaml` under `variants:`
- [ ] Add POOL_A + POOL_B in `_manual_llm_outputs.py`
- [ ] Ensure `lifestyle_city_connection` and `exterior_establishing_shot` are NOT in the default 5-slot batch

---

## 13. Active Variant Lineup (Default Batch)

| Slot | variant_key | Default scene type | Typical recipes |
|------|-------------|-------------------|----------------|
| V1 | lifestyle_private_retreat | Solo sanctuary / bedroom / balcony morning | open_room_anchor, architectural_dead_zone, editorial_triptych |
| V2 | lifestyle_social_home | Dining party / sundowner group / kitchen hosting | golden_archway, horizon_anchor, editorial_triptych |
| V3 | lifestyle_dynamic_a | Family / couple / home office / parent+child | open_room_anchor, golden_archway, zoned_triptych |
| V4 | lifestyle_dynamic_b | Different scene from V3: evening / solo / wellness | glass_morphism_shield, golden_archway, sky_text_canvas |
| V5 | interior_signature_moment | Empty room, no people, light+material only | architectural_dead_zone, glass_morphism_shield, editorial_triptych |

**Never in default batch:** `lifestyle_city_connection` (legacy), `exterior_establishing_shot` (opt_in: true)

---

## 14. Variant → Recipe → Layout Type Mapping

| Recipe | info_band_style | layout_type | Bottom section |
|--------|----------------|-------------|----------------|
| the_open_room_anchor | compact_spec_row | full-bleed | Thin spec strip at very bottom edge |
| the_horizon_anchor | icon_grid_strip | solid_footer_container | Narrow solid footer with icon+label columns |
| the_glass_morphism_shield | compact_spec_row | glass_morphism_panel | Small floating pill upper-left; spec at very bottom |
| the_architectural_dead_zone | asymmetric_band | asymmetrical | Config LARGE on left, stacked USPs on right |
| the_golden_archway | strip_three_col | framing_device | Standard 3-column footer strip |
| the_editorial_triptych | editorial_triptych | editorial_triptych | Ivory footer: icon cols + dominant price + USP |
| the_zoned_triptych | zoned_triptych | zoned_triptych | Ivory footer: 3 columns with price dominant centre |
| the_sky_text_canvas | compact_spec_row | full-bleed | NO footer strip; all text in upper sky zone |

---

## 15. Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| LAZY_IMAGE_PROMPTS | "1" | "1" = skip visual tasks in crew run; prompts written on demand |
| IDEOGRAM_API_KEY | (required) | Without this, generate returns an error |
| IDEOGRAM_STYLE_REF | "0" | "1" = attach exemplar images from recipe.exemplar_images |
| IDEOGRAM_STYLE_REF_DIR | project_context/reference_ads | Where exemplar images live |
| CREATIVE_MODEL | "gemini/gemini-2.5-flash" | LLM for visual_prompter + regenerate-prompt + new_scene |
| VISION_MODEL | falls back to CREATIVE_MODEL | LLM for reference image analysis |
| INPAINT_MOCK | "0" | "1" = skip Ideogram call, return prompt text (for testing) |
| REMIX_MOCK | "0" | "1" = skip Ideogram call in reference variant (for testing) |

---

## 16. Known Issues Table (Symptom → Source → Fix Location)

| Issue | Root Source | Fix Location |
|-------|-------------|--------------|
| Text carved/3D into walls | composition_notes say "on the wall" not "overlaid as flat typography" | `_manual_llm_outputs.py` + task_composer.py instruction |
| Price placed off-axis despite "central" rule | `PRICE & CTA PROMINENCE` block says "central 70-80%" contradicting composition_notes | `image_service.py:~1229` — remove "central 70-80%" sentence from CD path |
| Spec items listed twice | composition_notes describe footer AND `_build_typography_block` also lists them | Structural: ensure composition_notes footer language matches what _build_typography_block emits |
| recipe info_band_style has no effect | `composition_driven=True` early return skips all band style code | `image_service.py:_build_typography_block()` — append band style description before return in CD mode |
| /regenerate-prompt kills recipe quality | Generates old prose format → Branch A bypass | Rewrite endpoint to produce VisualPromptOutput JSON format |
| inpaint Apply does nothing / no result | Ideogram v4 edit endpoint URL likely wrong | `image_service.py:1596` — verify endpoint URL against Ideogram API docs |
| Reference variants have no recipe grammar | Both ref modes hardcode recipe_tag="" | `visuals.py:generate_reference_variant()` — pass recipe from variant or let user pick |
| dedup breaks composition_notes/recipe alignment | Dedup runs after LLM writes notes | Pre-select recipe before LLM call, or regenerate notes after dedup reassignment |
| GATED COMMUNITY appears in every footer | `_COMP_FOOTER_DEFAULTS` padding in legacy path | `image_service.py:_build_typography_block()` — remove or only pad when strip explicitly requested |
