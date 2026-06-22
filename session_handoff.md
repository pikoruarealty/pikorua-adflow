# Session 38 Handoff — Dynamic Footer & Material Density

**Date:** 2026-06-22  
**User:** Bhavarth  
**Milestone:** 5 new material-dense scenes generated, footer treatment made dynamic, production pipeline verified  
**Status:** All composition_notes rewritten, task_composer rules updated, prompts ready for render test

## What Was Built in Session 38

### 1. Scene Rewrite — Material Density Over Minimalism
All 5 LLM_OUTPUTS completely redesigned to fill the frame with layered material:
- **V1** (charcoal_gold, the_open_room_anchor): Double-height living room, bright cool afternoon, full-height fluted-oak library wall, Pietra Grey marble foreground
- **V2** (burgundy_gold, the_golden_archway): Kitchen island with Calacatta Viola marble through plaster archway, warm evening hosting, backlit onyx wine wall  
- **V3** (slate_cream, the_zenith_gaze): Grand double-height foyer, curved staircase, crystal chandelier cascade, cool bright morning
- **V4** (forest_gold, the_backlit_silhouette): Panelled study, dusk city silhouette, warm 2700K lamp + backlit shelving, emerald-stained oak
- **V5** (navy_gold, the_horizon_anchor): Marble bathroom, Ahmedabad night through frameless glazing, honed stone bath, pure material set-piece

**Why:** Previous scenes (dawn balcony, daybed) rendered as sparse lifestyle photos with vast empty dark interior columns. New scenes are material-dense, architectural, every surface carries weight.

### 2. Footer Treatment — Dynamic Per Scene (NOT Forced 3-Column)
**Root cause of earlier error:** I had hardcoded "FOOTER ALWAYS 3 ITEMS" in task_composer.py, forcing GATED COMMUNITY onto every footer. This is template thinking — the exact opposite of what the pipeline is designed to do.

**The fix:** Removed the mandate. Composition_notes prose is the source of truth — the image model reads it, not code-level fallbacks.

Each scene now gets a scene-appropriate footer treatment:
- **V1** (bright open): Single slim tracked line ('CLUBCLASS AMENITIES · 3,300–6,100 SQ FT') on a gold hairline beneath location name in floor zone — lets marble breathe
- **V2** (warm hosting): Slim deep-burgundy strip with diamond divider, 2 items centred — grounded but not heavyweight
- **V3** (grand foyer): Two-column icon grid (clubhouse + ruler icons) — architectural balance suits the staircase
- **V4** (study): Single centred line, understated — fits quiet masculine palette
- **V5** (pure material): Float on thin hairline in dark marble foreground, no strip — respect the pristine surface

### 3. Production Pipeline Rules Updated & Verified

**Files modified:**
- `task_composer.py` (3 edits):
  - SCENE FABRICATION RULE: "The scene may only show what the property brief supports. Generic USP such as 'Clubclass Amenities' does NOT authorise showing a specific amenity."
  - BADGE TEXT LENGTH: "Corner pill = 3-word form 'SAMPLE FLAT READY'. Only use longer phrase for mid-frame badge with 30%+ canvas width."
  - SUPPORTING SPECS ARE DYNAMIC (replaced "FOOTER ALWAYS 3 ITEMS"): "Let item count follow the brief — usually two. Never force a third as default."
  - MINIMUM SCALE: "Location name ≥ 75% canvas width; headline ≥ 3% height; footer fills column (Bold/ExtraBold, never condensed); price instantly readable."

- `_manual_llm_outputs_s36.py` (all 5 composition_notes):
  - Rewritten with material-dense scenes
  - Dynamic footer prose matching scene geometry, not a forced template
  - Explicit scale percentages (75-82% for location name, 55% for BHK in photo zone)
  - Verified: SINDHUBHAVAN ROAD broken into 12 characters (no slashes), BHK as standalone large element

- `image_service.py` (already correct from prior session):
  - badge_cta priority: `entry.get("badge_cta")` first, then `brief.get("sample_ready_cta")`
  - Fallback pool padding intact (dead code in composition-driven path, safe in legacy)
  - MINIMUM SCALE rule in build_ad_prompt composition-driven path

## Key Decisions & Corrections

### The Mandate Mistake
**What I did:** Added "FOOTER ALWAYS 3 ITEMS" rule, forcing GATED COMMUNITY + SIGNATURE LIVING + ELEVATED SPACES onto every footer.
**Why it was wrong:** User correctly called this out: "you dumb piece of ai, i am telling keep the design dynamic then why are you forcing the bottom strip and 3 footer items in every recipe/composition"
**The fix:** Removed the mandate. Composition_notes prose is the law. Code-level fallbacks (in `_build_typography_block`) don't reach the image model — only the prose does.

### Footer Sparseness (2 Items)
**User complaint:** "bottom STILL has only 2 elements!!!"
**Root cause:** Composition_notes explicitly said "Two items, uncluttered" — the image model obeyed the prose, not my code-level padding.
**Fix:** Rewrite composition_notes prose to specify scene-appropriate treatment. If a wide strip needs a third item to balance, add one. If a scene needs just a single line, write that. Let the scene geometry decide.

### Material Density vs Minimalism
**User feedback:** "the ads look too simple to me, doesnt give that luxury feeling and also feels too empty"
**Root cause:** Open-room-anchor recipe (headline pill + location name + minimal layout) is elegant and editorial, but reads as lifestyle not advertising.
**Fix:** Chose scenes that are material-dense (double-height rooms, full-height libraries, marble, panelling, lighting fixtures) and fill the frame. Every surface carries architectural weight.

## What Stays the Same

- **5-variant architecture** (lifestyle_private_retreat, lifestyle_social_home, lifestyle_dynamic_a/b, interior_signature_moment)
- **Recipe system** (the_open_room_anchor, the_golden_archway, the_zenith_gaze, the_backlit_silhouette, the_horizon_anchor)
- **Palette diversity** (5 distinct palettes per batch, deduped by dedupe_visual_batch)
- **Composition-driven path** (composition_notes prose + scene_prose → build_ad_prompt → image model)
- **All hard bans, scene fabrication checks, sanitizer rules** (no changes to enforcement)

## Next Steps for Next Session

### 1. Render the 5 New Prompts
Generate to Ideogram/gpt-image-1 to verify:
- Material density reads as premium, not sparse
- Light diversity (afternoon, evening, morning, dusk, night) works
- No dead interior columns

### 2. Verify Footer Rendering Per Scene
- **V1** should render as single line on hairline (not a strip)
- **V2** should render as 2-item slim strip with divider (not 3-column grid)
- **V3** should render as 2-column icon grid with hairline between
- **V4** should render as single centred line (not a strip)
- **V5** should render as floating text on dark marble (not a strip)

### 3. Validate Key Text Elements
- **SINDHUBHAVAN ROAD** spanning 75-82% canvas width
- **4 & 5 BHK** as standalone large element in photo zone (not footer label)
- **Location name:** each individual letter legible at arm's length
- **Price badge** (bottom-right): instantly readable at across-a-table distance
- **Sample badge** (varies per scene): 3-word form, appropriately placed

### 4. Confirm No Amenity Fabrication
- V3: no pool (brief only says "Clubclass Amenities", not a specific pool)
- All variants: only scenes supported by brief USPs

### 5. Compare to Prior Batch
Do these feel premium/architectural vs. the sparse daybed/balcony scenes from prior run?

## Design Philosophy Reminder

The image pipeline is **data-driven and dynamic**, not template-based:
- Palette picked per-scene, deduped across batch
- Recipe is advisory; **composition_notes prose is what the image model reads**
- Footer treatment flows from scene geometry, NOT a fixed rule
- Every rule (SCENE FABRICATION, BADGE TEXT LENGTH, MINIMUM SCALE) serves legibility and authenticity

**If the next session finds prompts still rendering as sparse or generic**, the fix is NOT another rule but a deeper rewrite of the **scene prose itself** — light direction, material specificity, spatial layering, how surfaces carry meaning.

---

# Session 37 Handoff — AutoOptimiser Phase B Extensions

**Date:** 2026-06-22  
**User:** Bhavarth  
**Milestone:** Phase B AutoOptimiser Extensions Complete (B1, B2, B3, B4)  
**Status:** All four Phase B extensions built, wired up, and logic tested successfully. The roadmap's Phase B is now complete.

---

## What Was Built in Session 37

### 1. B4 — Per-Geo Spend Breakdown (Smart Location Budgets)
- Added `fetch_insights_by_region` to pull exact ₹ spend per city from Meta.
- Trim cards now show exact "spend wasted" (e.g. ₹8,400 spent on Mumbai → 0 quality leads), making location decisions data-driven rather than just based on lead count.

### 2. B1 — Creative Winner/Loser Auto-Management
- Created a comparative scoring logic in `creative_performance.py`.
- Added a "Rung 0" to the Autopilot ladder that runs before anything else.
- If an ad costs 2x the average cost-per-lead (with at least 1,000 impressions and 3 leads), it gets automatically paused, and the winning ad in that adset gets a boost.

### 3. B2 — A/B Safe-Swap on Refresh
- Extended the Ad Flow deployment so that clicking "refresh creatives" can safely run a new ad side-by-side with an existing one instead of just replacing it.
- Registered A/B groups in the autopilot state tracking.
- Autopilot runs a nightly check to resolve these tests after a 7-day window, automatically keeping the winner and pausing the loser.

### 4. B3 — Clientele-Scoped Creative Learning (AI Memory)
- Created an Exponential Moving Average (EMA) memory module (`creative_learning.py`).
- As the autopilot runs, it tracks which color palettes and design recipes bring in high-quality leads for specific buyer types (e.g., Luxury Bungalow vs NRI).
- When generating new ads, this memory is fed into the CrewAI agents as a "soft prior" to subtly bias the AI toward proven winning design choices.

---

# Session 36 Handoff — Campaign Autopilot (Core)

**Date:** 2026-06-22  
**User:** Bhavarth  
**Milestone:** Autopilot brain complete + UI scaffolded + clientele targeting + 100% cheque flag  
**Status:** All core code in place, logic verified.

## What Was Built

### 1. The Autopilot Brain (`api/services/autopilot.py`)
Core decision-making engine that reads every ACTIVE Meta campaign and scores them against CRM lead quality:

- **`run_autopilot(apply_safe=True)`** — Main entry point. Orchestrates all campaigns, auto-applies safe fixes (if not DRY_RUN), surfaces ≤2 human decisions per campaign, returns payload for UI.
- **`evaluate_campaign(campaign, token, crm_leads, crm_report, state)`** — Scores one campaign: computes adaptive quality metric, walks decision ladder, returns auto-fixes + decisions.
- **`adaptive_quality(campaign_name, spend_7d, leads_7d, crm_leads, crm_report)`** — North-star metric picker:
  - Real quality-CPL (cost per quality-lead matching CRM `buying_status≥2`) if ≥5 matched quality leads exist
  - Fallback: profile-match score (0-100, how well campaign leads resemble historical best converters) when sparse
  - Includes `{metric_used, label, value, unit, n_quality, n_matched, building}`
- **`_ladder(campaign, adsets, ads, metrics, brief, registry, camp_state)`** — 10-rung decision ladder:
  1. Remove wrong-city geo (AUTO)
  2. Add bad-leads exclusion CA (AUTO)
  3. Add CRM Lookalike (AUTO)
  4. Enable Advantage+ on saturation (AUTO)
  5. Add NRI layer (APPROVE)
  6. Broaden radius +15km (APPROVE)
  7. Add interests from CRM profiles (APPROVE, clientele-scoped)
  8. Fresh creative (APPROVE, deep-link to Ad Flow)
  9. Reduce budget 30% (APPROVE)
  10. Pause (APPROVE, last resort)
- **`apply_fix(fix, *, auto=False)`** — Executes one rung, captures undo payload, logs to state, returns `{ok, impact, undo_token}`.
- **`undo_fix(campaign_id, fix_type)`** — Reverts most recent fix of a given type.
- **`get_applied_log()`** — Flattens per-campaign applied history (newest first) for Zone 2 render.

**State persistence:** `outputs/autopilot_state.json`  
```json
{
  "last_run": "2026-06-22T...",
  "campaigns": {
    "act_207925274|Bungalow ahmd general": {
      "applied": [
        {"fix_type": "remove_geo", "rung": 1, "title": "...", "detail": "...", "undo_payload": {...}, "applied_at": "..."}
      ],
      "cooldowns": {
        "add_nri": "2026-06-27T...",
        "broaden_radius": "2026-06-27T..."
      }
    }
  }
}
```

**Tunables** (top of file, adjust per real-world performance):
- `BENCHMARK_CPL=85` — ₹85 is the historical best (from bungalow general ahmd 2222)
- `FREQ_SATURATED=3.0` — 3× frequency threshold for saturation diagnosis
- `FREQ_EXHAUSTED=5.0` — 5× threshold for "pause if all else failed"
- `CPL_CEILING=500` — ₹500 CPL is unsustainable
- `QUALITY_LEAD_MIN=5` — Minimum matched quality leads to trust real quality-CPL
- `COOLDOWN_DAYS=5` — 5 days between same-type fixes (Meta learning phase)

### 2. The Autopilot UI (`api/routes/autopilot.py` + `templates/autopilot.html`)

**Routes:**
- `GET /autopilot` — Render the page
- `GET /autopilot-data` — Call `run_autopilot(apply_safe=True)`, 30-min in-process cache, return 3-zone payload
- `POST /autopilot-apply {campaign_id, fix_type}` — Apply one decision, invalidate cache
- `POST /autopilot-undo {campaign_id, fix_type}` — Undo, invalidate cache
- `POST /autopilot-run` — Force full pass now

**Template** (`autopilot.html`):
Three stacked zones:

**Zone 1 — Hero metric:**
```
Cost per quality lead    ₹1,240
(4 active campaigns · ₹41,000 spent last 7 days · 52 leads · 6 look promising · quality scoring building)
```
- Flips label based on `metric_used` (quality_cpl vs cost_per_lead)
- Subtext: plain English summary (campaign count, spend, lead count, promising leads, building status)

**Zone 2 — What I did (auto-applied log):**
```
✓ Stopped wasting GODREJ budget on Mumbai & Gurgaon
   Undo
```
- Each card: title + detail + Undo button
- Empty state: "Nothing needed fixing automatically…"

**Zone 3 — Needs your call (≤2 decisions, max):**
```
NN (Nehrunagar) — Same buyers seeing this ad 4× now
[Do it]  [Not now]
```
- Red border if rung 10 (pause)
- "Do it" for approvals (POST /autopilot-apply)
- "Open Ad Flow" deep-link for fresh-creative (rung 8)
- Empty state: "No decisions waiting…"

**Collapsed "See full numbers":**
Per-campaign table: spend, leads, CPL, frequency, cost-rising status.

### 3. Clientele Targeting (`meta_targeting.py` + `models.py` + form)

**Clientele types** (gated by property, not account):
- `luxury_bungalow` — HNI, 5Cr+, 40-60yo → luxury interests, private banking, HNI behaviours
- `premium_apartment` — IT/corporate, 1-3Cr, 28-45yo → tech industry, mid-career, metro living
- `nri_investment` — Diaspora, 2-5Cr, rental focus → NRI geos (AE/US/UK/SG), investment interests
- `commercial_office` — Business owner, 2Cr+ → business decision-makers, B2B interests

**`CLIENTELE_TARGETING_MAP`** in `meta_targeting.py`:
```python
CLIENTELE_TARGETING_MAP: dict[str, dict] = {
  "luxury_bungalow": {
    "label": "Luxury Bungalow / Villa",
    "interests": ["Luxury goods", "Luxury vehicles", ...],
    "behaviours": ["Frequent international travellers"],
    "age_min": 38, "age_max": 65,
  },
  ...
}
```

**Form field** (`templates/index.html`):
```html
<select id="clientele_type">
  <option value="premium_apartment">Premium Apartment</option>
  <option value="luxury_bungalow">Luxury Bungalow / Villa</option>
  <option value="nri_investment">NRI Investment</option>
  <option value="commercial_office">Commercial / Office</option>
</select>
```

**Autopilot integration:**
- Rung 7 (add interests from CRM) only applies interests matching the campaign's clientele type
- Cross-campaign learning is clientele-scoped: a bungalow audience never leaks into an apartment campaign

### 4. "100% Cheque Payment Only" Checkbox

**Form field** (`templates/index.html`, section 1):
```html
<input type="checkbox" id="cheque_only"> 100% Cheque Payment Only
```

**Flow:**
- `brief["cheque_only"]` → Boolean
- `image_service.py` / `_build_typography_block()`: adds "100% CHEQUE PAYMENT" to USP callouts in both composition + recipe branches
- `task_composer.py` context block: exposes to content crew LLM

**Rendered in images:** Both composition-driven and recipe-driven paths include it; off by default.

### 5. Account Hygiene Fixes

**Audience dedup guard** (`meta_audience_tool.py`):
- `find_existing_audience(base_url, headers, name, requests_lib)` checks for name match before creating
- `upload_crm_split_audiences()` reuses existing CA + lookalike instead of minting duplicates
- Stops the 4× duplicate CAs issue

**Registry role tagging** (`routes/audience.py`):
- Split-audience IDs now register with `role` field (`seed` / `lookalike` / `exclusion`)
- Autopilot rungs 2/3 read these roles to find the right audiences

---

## How to Test End-to-End

### Prerequisites
1. Meta Graph API token with `ads_read,ads_manage` scopes (already in `.env`)
2. Active campaigns on the account (verify `fetch_active_campaigns()` pulls them)
3. CRM leads in Supabase (at least one imported; webhook auto-tags with campaign_name)

### Test Script

```bash
cd d:/Pikorua/AI\ Digital\ Marketing/pikorua-adflow
export DRY_RUN=false  # Actually apply fixes; set to true to preview
python -c "
import sys; sys.path.insert(0,'src'); sys.stdout.reconfigure(encoding='utf-8')
import os
os.environ.setdefault('DRY_RUN','false')

from pikorua_adflow.api.services import autopilot as ap

# Full run
result = ap.run_autopilot(apply_safe=True)
print('Campaigns evaluated:', len(result.get('campaigns', [])))
print('Auto-fixes applied:', sum(len(c.get('applied', [])) for c in result.get('campaigns', [])))
print('Decisions queued:', len(result.get('decisions', [])))
print('Applied log:', len(result.get('applied_log', [])))

# Check state file
import json
if os.path.exists('outputs/autopilot_state.json'):
    with open('outputs/autopilot_state.json') as f:
        state = json.load(f)
    print('State persisted:', bool(state.get('last_run')))
"
```

### Manual UI Test

1. Start the server: `python -m uvicorn pikorua_adflow.api.main:app --reload`
2. Visit `http://localhost:8000/autopilot`
3. Wait for Zone 1 hero to load (should show account-wide CPL or cost-per-lead)
4. Zone 2 should list any auto-applied fixes from the state file
5. Zone 3 should queue decisions (or "No decisions waiting" if all campaigns are clean)
6. Click "Do it" on a decision → should POST to `/autopilot-apply`, re-fetch, and reload
7. Click "Undo" on an applied fix → should POST to `/autopilot-undo`, re-fetch, and reload
8. Click "See full numbers" → should expand per-campaign metrics table

---

## What Works ✓

1. **App composition** — all 5 routes register; full FastAPI app compiles
2. **Autopilot logic** — decision ladder, rungs, cooldown checks, undo capture all work
3. **Clientele targeting** — CLIENTELE_TARGETING_MAP profiles load, targeting filters by type
4. **Cheque flag** — renders in both image branches, off by default
5. **Audience dedup** — `find_existing_audience()` prevents duplicates
6. **CRM lead matching** — `match_meta_leads()` joins Meta submissions to CRM rows
7. **Profile-match fallback** — scores campaign leads against best-converters when sparse
8. **Smoke tests** — clientele profiles, lead matching, image spec inclusion all verified
9. **Syntax** — all 14 modified/created files pass AST parsing

---

## Known Issues & Gotchas

### Pre-Existing Test Failures (NOT this session)
**`tests/test_image_pipeline.py` — 2 failures:**
- `test_all_variants_non_empty_and_distinct` — expects 5 variants, config has 7 (dynamic_a/b, exterior, city_connection)
- `test_dedupe_visual_batch_enforces_distinct_palette_and_recipe` — palette dedup logic doesn't account for 7 variants

**Root cause:** Image config was updated to 7 variants, but test assertions weren't updated. **Fix:** Either (a) revert config to 5, or (b) update test expectations to 7 variants. Not blocking Autopilot.

### Windows Console Encoding
Running the brain on Windows can emit `UnicodeEncodeError` for `₹` symbols. Workaround: wrap test calls with `sys.stdout.reconfigure(encoding='utf-8')`. Production app handles this automatically.

### DRY_RUN Mode
- If `DRY_RUN=true` in env, fixes are logged to state but NOT applied to Meta (safe preview mode)
- Production should set `DRY_RUN=false` before going live
- Cron job / `/autopilot-run` should read this flag

### State File Location
Autopilot state lives in `outputs/autopilot_state.json`. If the file is missing, it starts from a clean slate (first run). State is NOT cleared on app restart.

### 30-min Cache
`/autopilot-data` caches for 30 minutes in-process. If you want a fresh evaluation immediately, call `/autopilot-run` first or pass `?force=true`.

### No Lead-Webhook Stamping yet
Current code assumes leads are already tagged with `campaign_name` and `ad_id` via the webhook. If the webhook isn't running or lags, CRM matching will be empty, and adaptive quality will fall back to "Cost per lead" with no quality data. **Check:** Ensure `meta_webhooks.py` or equivalent is running and pushing `campaign_name` to Supabase `meta_leads` table.

---

## Next Steps (Priority Order)

### 1. **End-to-end test with live account** (blocking)
Run the test script above on the live Meta token. Verify:
- All 4 active campaigns pull correctly
- At least one rung fires (ideally rung 1 on GODREJ)
- Applied fixes get logged to `outputs/autopilot_state.json`
- State file is human-readable and contains the expected structure

### 2. **GODREJ geo fix** (manual, outside Autopilot)
Until Autopilot is deployed, **manually pause the bad GODREJ ad set** (wrong geo) in Ads Manager. The good ad set (with CRM audiences) is ready to resume.

### 3. **CRM lead webhook verification** (blocking for quality metric)
Check that leads flowing in from Meta → Supabase are tagged with:
- `campaign_name` (e.g. "Nehrunagar" or "Bungalow ahmd general")
- `ad_id` (for per-ad diagnostics, optional for MVP)
- `buying_status` (populated by user reviewing in portal, or defaulting to 1)

If webhook is NOT running: start it, or manually backfill a few test leads with these fields.

### 4. **Tune decision ladder thresholds** (after first month of data)
Run the Autopilot daily for a month, collect real-world impact data, adjust tunables:
- `BENCHMARK_CPL` — update to current best baseline
- `FREQ_SATURATED` / `FREQ_EXHAUSTED` — watch for false positives (saturation alerts when CPL isn't actually rising)
- `CPL_CEILING` — adjust if ₹500 is too tight or too loose for this market

### 5. **Cosmetic UI polishes** (low priority)
- Money formatter: test edge cases (₹0, NULL, -₹500 from a refund)
- Empty states: verify all three (no campaigns, no fixes, no decisions)
- Responsive layout: test on mobile

### 6. **Add `/autopilot-run` to the cron schedule** (deployment)
Wire daily autopilot pass into existing cron / APScheduler so it runs automatically (and auto-applies safe fixes while humans sleep).

### 7. **Optional: Deep-link from Ad Flow to Autopilot**
When user clicks "Refresh creatives" on a stale campaign, pre-fill a new Ad Flow run for that property and link back to Autopilot for impact tracking.

### 8. **Optional: Predictive impact calibration** (EMA learning)
`optimization_tracker.py` already has EMA calibration; Autopilot can log every fix's predicted impact. Over time, train a per-account, per-fix-type impact model.

---

## Code Locations (Quick Reference)

| File | Role | Key Functions |
|---|---|---|
| `api/services/autopilot.py` | Brain | `run_autopilot`, `evaluate_campaign`, `adaptive_quality`, `apply_fix`, `undo_fix` |
| `api/routes/autopilot.py` | Routes | `/autopilot`, `/autopilot-data`, `/autopilot-apply`, `/autopilot-undo`, `/autopilot-run` |
| `templates/autopilot.html` | UI | 3-zone layout, JS event handlers, money formatter |
| `tools/meta_tool.py` | Meta API | `fetch_active_campaigns`, `add_geo_countries`, `remove_geo_locations`, `add_custom_audiences` |
| `tools/meta_targeting.py` | Targeting | `CLIENTELE_TARGETING_MAP`, `clientele_profile`, `interests_from_crm_profiles`, `build_default_audience` |
| `tools/meta_audience_tool.py` | Audiences | `find_existing_audience` (dedup guard) |
| `analytics/crm_analytics.py` | CRM | `match_meta_leads`, `profile_match_score` |
| `models.py` | Schema | `brief.clientele_type`, `brief.cheque_only` |
| `templates/index.html` | Form | Clientele select, cheque checkbox, JS payload |
| `routes/audience.py` | Registry | Audience registry write with role tagging |
| `base.html` | Nav | Autopilot link in topbar |

---

## State File Example

```json
{
  "last_run": "2026-06-22T14:32:00Z",
  "campaigns": {
    "act_207925274|Nehrunagar": {
      "applied": [
        {
          "fix_type": "remove_geo",
          "rung": 1,
          "title": "Stopped wasting NN budget on Mumbai & Gurgaon",
          "detail": "Removed 2 wrong-city locations; est. saves ₹200/lead",
          "applied_at": "2026-06-22T14:30:00Z",
          "undo_payload": {
            "adset_id": "...",
            "geo_locations": [123456, 789012]
          }
        }
      ],
      "cooldowns": {
        "add_nri": "2026-06-27T14:30:00Z"
      }
    }
  }
}
```

---

## Handoff Checklist for Next Session

- [ ] Run end-to-end test script (above) on live account
- [ ] Verify Zone 1 hero loads with correct CPL/cost-per-lead
- [ ] Click one "Do it" button and confirm it applies (check state file)
- [ ] Check GODREJ campaign — should either show rung-1 fix or offer approval for rung 5+ (NRI)
- [ ] Confirm audience dedup worked (no new CAs created on re-run)
- [ ] Check CRM lead webhook is active (tail logs or query Supabase `meta_leads` for recent entries)
- [ ] Adjust `BENCHMARK_CPL` and other tunables based on real data
- [ ] Schedule daily cron run

---

## Files Modified / Created This Session

**Created:**
- `api/services/autopilot.py` (1000+ lines, the brain)
- `api/routes/autopilot.py` (200+ lines, endpoints)
- `templates/autopilot.html` (180 lines, 3-zone UI)

**Modified:**
- `api/main.py` — added autopilot router import + registration
- `templates/base.html` — added nav link to `/autopilot`
- `models.py` — added `clientele_type`, `cheque_only` to CampaignBrief
- `templates/index.html` — added clientele select + cheque checkbox + JS payload
- `tools/meta_targeting.py` — added CLIENTELE_TARGETING_MAP, clientele_param, interests_from_crm_profiles
- `api/services/image_service.py` — consume cheque_only in _build_typography_block
- `api/services/campaign_service.py` — pass clientele_type to effective_audience, add crew inputs
- `crews/content_crew/task_composer.py` — add cheque_only to context_block
- `tools/meta_audience_tool.py` — added find_existing_audience, dedup logic
- `tools/meta_tool.py` — added campaign/adset/ad fetchers, geo/CA edits
- `analytics/crm_analytics.py` — added match_meta_leads, profile_match_score
- `routes/audience.py` — fixed registry role tagging for split audiences
- `.gitignore` — added root-level scratch-file patterns

---

**Graph updated:** `graphify update .` ran; 871 nodes, 1316 edges in graphify-out/

**Tests:** 22 pass, 2 pre-existing failures (variant count mismatch).

---

*End of Session 36. All implementation complete. Ready for live testing.*
