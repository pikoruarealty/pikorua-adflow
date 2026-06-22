# AutoOptimiser — Roadmap & Next-Session Plan

> Renamed from "Autopilot" → **AutoOptimiser** (user-facing label only; internal route
> paths `/autopilot*`, module `services/autopilot.py`, and `active=='autopilot'` key are
> unchanged to avoid needless churn — rename those later only if desired).

This file is the continuation point. It captures (A) what's already built & verified,
(B) the four extensions the user approved, and (C) additional high-value ideas worth doing.
Pick from B + C next session.

---

## A. Current state (built + verified this session — 24/24 tests pass)

- **Dynamic Geo Opportunity engine** — `analytics/geo_intelligence.py`. No static city tables.
  Live signals: CRM quality-by-city + Meta reach. Never auto-removes geo; surfaces *review*
  (targeted city, leads but 0 quality) and *add* (untargeted city your CRM proves) decisions.
  Wired into `autopilot._ladder` rung 1 (APPROVE-only) + `meta_tool.add_geo_city` + apply/undo.
- **Refresh Creatives** — `POST /refresh-creatives/{run_id}` swaps creative on live ads in place
  (same ad/adset/targeting/budget/history). "Refresh live ads" button on campaign page.
  Rung-8 `fresh_creative` deep-links to the matched campaign (`/results/{run_id}`).
- **AutoOptimiser core** (prev session) — `services/autopilot.py`: 10-rung ladder, adaptive
  quality-CPL north-star, cooldowns, auto-apply safe fixes (DRY_RUN-gated), Undo, 3-zone UI.

**Key invariant:** `DRY_RUN` defaults to `true` — AutoOptimiser never writes to Meta until
`DRY_RUN=false` is set in `.env`. Nothing has touched the live account yet.

---

## B. Approved extensions (user picked all four)

### B1. Creative winner/loser auto-management  ★ highest leverage
**Goal:** within a multi-variant campaign, auto-pause the clear loser and shift its budget to
the clear winner — the single biggest lever Meta gives, and the comparison math already exists.
- **Reuse:** `routes/deploy.py::meta_performance` already computes `avg_cpl`, `best_cpl_v`,
  `avg_ctr`, `best_ctr_v` and per-variant `rank_label` (Top performer / Underperforming).
- **Build:** a new ladder rung (creative-level, AUTO-safe with guardrails) that, when a variant
  is >2× avg CPL **and** has statistically meaningful spend/impressions **and** a sibling is
  clearly better, calls `pause_variant(loser_ad_id)` + `update_adset_budget` to move spend to
  the winner. Capture undo (resume + restore budget). Gate behind a min-spend/min-impressions
  threshold so it never acts on noise; cooldown per variant.
- **Files:** `services/autopilot.py` (new rung + apply/undo handlers), maybe a small
  `creative_performance.py` helper extracting the comparison out of the route for reuse.
- **Risk:** acting too early on thin data → mitigate with min-impressions gate (e.g. ≥1000 impr
  or ≥3 leads per variant before judging) + keep it reversible.

### B2. A/B safe-swap on refresh
**Goal:** a creative refresh shouldn't blind-replace a working ad. Run new alongside old for a
learning window, then auto-pause the loser.
- **Build:** extend Refresh Creatives so instead of `swap_ad_creative` in place, it creates a NEW
  ad in the same ad set with the fresh creative (reuse `deploy_ad`'s ad/creative steps, existing
  adset_id), tags both old+new with an `ab_group` + `ab_started_at` in the run record. A new
  ladder check, after `AB_WINDOW_DAYS` (e.g. 4), compares the pair via meta_performance and
  pauses the loser.
- **Files:** `routes/deploy.py` (refresh gets an `?ab=true` mode / new endpoint), `meta_tool`
  (add `create_ad_in_adset(adset_id, creative_spec)`), `services/autopilot.py` (resolve A/B
  groups), run record schema (`ab_groups`).
- **Depends on:** B1's comparison helper (shared).

### B3. Clientele-scoped winning-creative learning
**Goal:** remember which palette / recipe / scene + copy angle produced the best quality-CPL per
clientele type, and bias the next generation toward it — bungalow winners never bleed into
apartment campaigns.
- **Build:** on a campaign with enough quality leads, attribute quality-CPL to the winning
  variant's `visual_prompts.json` tags (palette_tag/recipe_tag/scene_tag) + copy angle, and write
  a per-clientele "creative memory" (`outputs/creative_memory.json`, keyed by clientele_type).
  Feed it into `task_composer.compose_description` as a soft prior (prefer winning tags) and into
  the copywriter context. Respect the existing `dedupe_visual_batch` distinctness.
- **Files:** new `analytics/creative_learning.py`, hook in `campaign_service.run_pipeline`
  (read memory → inputs), `task_composer` (bias selection), write-back after quality data lands.
- **Note:** clientele-scoping is the hard constraint (see CLIENTELE_TARGETING_MAP / [[session36]]).

### B4. Per-geo spend breakdown (precision for the geo engine)
**Goal:** make geo trim/add cards show *actual spend wasted per city*, not just CRM lead counts.
- **Build:** `meta_tool.fetch_insights_by_region(campaign_id, token)` using Graph insights with
  `breakdowns=region` (or `country`). Feed spend-per-geo into `geo_intelligence.geo_recommendations`
  so a trim card reads "₹8,400 spent on Mumbai → 0 quality" and add cards can estimate upside.
- **Files:** `tools/meta_tool.py` (+1 fetch), `analytics/geo_intelligence.py` (consume it).
- **Low risk, high trust payoff.** Good first pick — small and makes B-geo decisions credible.

---

## C. Additional ideas (my proposals — automation / ease / usefulness)

Ranked roughly by value-to-effort.

1. **Daily morning brief (digest).** A scheduled run (cron / APScheduler) at, say, 8am IST that
   runs AutoOptimiser and sends a plain-language summary to `pikorua.marketing@gmail.com`
   (and/or WhatsApp via the existing lead infra): "Yesterday I did X, Y. 2 decisions need you."
   Turns the tool from pull → push. Pairs with setting `DRY_RUN=false` + safety rails (#4).
2. **Cross-campaign budget pacing.** Within a clientele, move daily budget from the worst
   quality-CPL campaign to the best (bounded daily step, reversible). The account-level version
   of B1. Needs the quality metric to be trustworthy first (#5).
3. **Creative-fatigue forecasting.** Track frequency/CPL trend per ad and predict the saturation
   date, so a refresh (B2) is *pre-generated* before CPL climbs — proactive, not reactive.
4. **Autonomous-mode safety rails.** Before flipping `DRY_RUN=false`: per-campaign daily spend
   cap, max N auto-actions/day, a global kill-switch, and a "require approval above ₹X impact"
   threshold. Non-negotiable prerequisite for true autonomy.
5. **Lead-quality feedback loop.** The whole north-star depends on CRM `buying_status` being
   filled. Add a nudge ("12 leads un-reviewed — tag them to sharpen AutoOptimiser") and explore
   auto-inferring quality from response/site-visit signals. Without this, quality-CPL stays sparse.
6. **Audience overlap / cannibalisation check.** Detect when two live campaigns target
   overlapping audiences (bidding against yourself) and flag a merge/exclusion. Meta has an
   overlap API; otherwise estimate via reach intersection.
7. **Placement hygiene.** Audience Network placements often burn budget on junk. Detect
   low-quality placement spend and suggest excluding it.
8. **"Why" on every decision.** A one-line, data-grounded rationale + the predicted-vs-actual
   track record (from `optimization_tracker`) shown on each card — builds trust, encourages
   approvals. Cheap, compounding.
9. **Lookalike refresh cadence.** Rebuild CRM lookalikes as the CRM grows (e.g. monthly) so the
   seed stays current; dedup guard already exists (`find_existing_audience`).
10. **Dayparting from lead timing.** If quality leads cluster at certain hours/days, suggest an
    ad schedule. Lower priority; needs volume.

---

## D. Suggested sequencing for next session
1. **B4 (per-geo spend)** — small, makes the geo engine credible. ~half day.
2. **B1 (winner/loser)** — highest leverage; extract a shared comparison helper. ~1 day.
3. **#4 safety rails + #1 daily digest** — the gate to running `DRY_RUN=false` safely. ~1 day.
4. **B2 (A/B safe-swap)** then **B3 (creative learning)** — both build on B1's helper.

## E. Things to wire when going live (from the handoff)
- Set `DRY_RUN=false` only after safety rails (#4).
- Confirm the lead webhook stamps `campaign_name`/`ad_id` so CRM↔campaign attribution (and the
  geo engine + quality-CPL) actually have data.
- Tune AutoOptimiser thresholds (BENCHMARK_CPL, FREQ_*, CPL_CEILING) + geo_intelligence
  thresholds (GEO_MIN_LEADS_TO_JUDGE, GEO_ADD_MIN_QUALITY) against real data.
- Schedule `/autopilot-run` (cron) once autonomous mode is trusted.
