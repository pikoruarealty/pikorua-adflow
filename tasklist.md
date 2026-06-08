# Pikorua AdFlow — Task Progress

## Phase 0 — Foundation
- [x] 0.1 Brand voice doc — drafted from live Meta ads; [CONFIRM] items flagged for Jitendra
- [ ] 0.2 Data audit — needs CRM access
- [ ] 0.3 project_context/ finalised — blocked on 0.2
- [x] 1.2 Wire brand voice into agents — copywriter, evaluator, ad_ops_manager all receive brand voice context

## Phase 1 — Pipeline (COMPLETE)
- [x] 0.4 Pipeline runs end-to-end (~98s), no errors
- [x] 0.5 .env.example committed
- [x] 1.1 AudienceCrew — persona_researcher, competitor_scout, trend_analyst (SerperDev)
- [x] 1.3 ContentCrew — 5 Meta variants, Google ads, WhatsApp script, email, dry-run Meta JSON
- [x] 1.4 Human review checkpoint (outputs/pending_review/<timestamp>/)
- [x] 1.5 Audience → Content crews wired in main.py
- [x] 1.6 FastAPI portal — POST /launch-campaign, /status/{run_id}, /runs, /portal
- [x] 1.7 Content quality fixes — banned invented facts, headline examples, body copy rules
- [x] 1.8 Targeting researcher agent — build_targeting_brief task (5-part geo/demo/pro/Meta/Google)
- [x] 1.9 Targeting wiring — CampaignBrief expanded (locality, buyer_type, nri_geographies,
         campaign_duration_days), inputs dict updated in both main.py and api/main.py,
         portal form updated, {targeting} wired into format_for_api

## Phase 2 — Quality & Memory
- [x] 2.1 Copy evaluator agent — scores all 5 Meta variants on 4 dimensions, flags below 6
- [x] 2.2 Regeneration loop — rewrite_flagged task retries flagged variants (max 2 attempts)
- [ ] 2.3 Qdrant vector memory — store successful campaigns for future retrieval

## Phase 3 — Deployment (GATED: needs Phase 2 complete + platform credentials)
- [ ] 3.1 Meta Ads live launch via API
- [ ] 3.2 Google Ads live launch via API
- [ ] 3.3 WhatsApp Business send (opted-in contacts only — C3)
- [ ] 3.4 Brevo email send
- [ ] 3.5 n8n orchestration
