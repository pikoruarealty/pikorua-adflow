# Pikorua Realty — Data Audit

> **STATUS: Updated June 9, 2026.**
> Meta Ads export analysed. Real CRM export received and analysed (123 leads, May 8 – Jun 1, 2026).
> WhatsApp contacts and Google Ads history still unknown.

---

## Meta Ads Campaign Performance (export: May 1 – June 8, 2026)

5 active campaigns. All lead generation objective. All running on daily budgets.

| Campaign | Leads | CPL (₹) | Spend (₹) | CTR | Daily Budget |
|---|---|---|---|---|---|
| bungalow ahmd general | 205 | 175 | 35,899 | 0.94% | ₹1,000 |
| bungalow ahmd general – quality lead | 127 | 246 | 31,230 | 1.67% | ₹1,000 |
| LAARGE Apts campaign | 90 | 389 | 35,004 | 0.80% | ₹900 |
| NN New Leads campaign | 89 | 430 | 38,267 | 0.71% | ₹1,500 |
| GODREJ VASTRAPUR Leads campaign | 39 | 1,025 | 39,976 | 0.57% | ₹1,500 |

**Key benchmarks for the AI system to use:**
- Good CPL for Ahmedabad luxury: ₹175–₹400
- Underperforming CPL: ₹800+ (GODREJ campaign at ₹1,025 is an outlier — likely poor targeting or creative)
- Typical daily budget: ₹900–₹1,500
- Good CTR for this segment: 1.0%+ (quality lead campaign hit 1.67%)
- Reach per campaign over ~40 days: 32,000–86,000 (varies widely by targeting)
- CPM range: ₹186–₹297

**Observations:**
- "bungalow ahmd general" is the strongest campaign: lowest CPL (₹175), highest lead volume (205), adequate CTR
- "quality lead" variant performs better on CTR (1.67%) but higher CPL (₹246) — likely tighter targeting
- GODREJ VASTRAPUR is significantly underperforming — 6× worse CPL than best campaign despite highest reach (86k). Likely a creative/audience mismatch, not a budget problem
- Attribution: all on 7-day click or 1-day view

---

## CRM (updated June 9, 2026)

- **System:** Unknown CRM (export format suggests a web-based lead management tool)
- **Export received:** 123 leads, May 8 – June 1, 2026
- **Campaigns represented:** "Nehru Nagar" and "Large Apartments"
- **All leads status:** Migrated / Unassigned — call status, HWC, and buying stage are all blank. The CRM is capturing leads but not tracking follow-up activity yet.

### Budget distribution

| Budget range | Approx. count | Notes |
|---|---|---|
| ₹2 Cr – ₹3 Cr | ~40 | Mostly Nehru Nagar campaign |
| ₹4 Cr – ₹5 Cr | ~12 | Mixed campaigns |
| ₹5 Cr – ₹6 Cr | ~10 | Mixed campaigns |
| ₹6 Cr – ₹8 Cr | ~4 | Mixed |
| ₹7 Cr – ₹8 Cr | ~10 | Large Apartments campaign |
| ₹9 Cr – ₹10 Cr | ~10 | Large Apartments campaign |
| ₹11 Cr – ₹12 Cr | ~6 | Large Apartments campaign |
| ₹12 Cr & Above | ~18 | Large Apartments campaign — strongest HNI segment |

**Insight:** The ₹2Cr–₹3Cr bucket is the largest single segment but falls below Pikorua's ideal buyer profile (₹5Cr+). These are likely lower-intent leads from broad targeting. The ₹9Cr+ segment (~34 leads) is the most relevant for the current property pipeline.

### Geographic breakdown

- **Ahmedabad local:** ~85% of leads
- **Out-of-city (domestic):** Surat, Gandhinagar, Indore, Jaipur, Kolkata, Gurugram, Lucknow, Rajkot, Dehradun, Barnala, Howrah, Junagadh, Sidhi (~13%)
- **International / NRI signals:** Abu Dhabi (1 lead, KPMG manager), London (1 lead, TUK Global Director) (~2%)

**Insight:** NRI pipeline is very thin in this export — only 2 confirmed international leads. Either NRI targeting is not being run actively, or the form is not capturing current city. Worth noting for audience brief.

### Profession breakdown (sample)

Strong representation of: business owners, CEOs, MDs, proprietors, directors — consistent with HNI profile.
Also present: doctors, CAs, VPs, senior engineers, retired professionals.
Some noise: a few entries with dummy profession/company fields (e.g., "A", "Yruery", "Qwerty") — likely test submissions or junk leads.

### Data quality issues

- Several entries with dummy Profession/Company values — should be filtered before lookalike upload
- One lead with Zomato noreply email (junk)
- One lead with phone number as name field
- Budget field uses string ranges (e.g., "2 Cr – 3 Cr", "12 Cr & Above") — crm_analyser.py handles these exact strings

---

## Competitor Ad Analysis (from screenshots, June 9, 2026)

### Godrej Altus Vastrapur — full Meta lead form flow observed

This is the GODREJ VASTRAPUR campaign that delivered ₹1,025 CPL — the worst performer in our data.

**Lead form structure:**
1. Ad creative: "Super Luxurious Bungalows in Ahmedabad (INDIA)" + spec bullets
2. Qualifier Q1: "What is your budget?" — INR 4Cr to 6Cr / INR 7Cr to 10Cr
3. Qualifier Q2: "Are you ready to proceed with a refundable EOI (₹5L)?" — Yes online / Yes cheque at site / Maybe need more info
4. Contact form: Email, Full name, Phone
5. Privacy + WhatsApp opt-in checkbox (pre-checked)
6. Post-submit: "Get details on WhatsApp immediately!" CTA → WhatsApp redirect

**Why it underperforms:**
- Asking for a ₹5L commitment in step 2 of a cold ad is a conversion killer — disqualifies genuinely interested leads who aren't ready to commit
- "Super Luxurious Bungalows in Ahmedabad (INDIA)" is the exact copy pattern Pikorua's brand voice rejects
- The WhatsApp redirect post-form means the lead is immediately handed to a mass messaging flow — no personalisation signal

**Lesson for Pikorua:** Do not gate leads behind EOI intent qualifiers in the cold ad form. Qualify through conversation after the lead is captured.

### RD Group Ahmedabad — Sindhu Bhavan Road Sky-Mansion ads

Direct competitor to the Sindhu Bhavan Road property Pikorua ran a dry-run for.

**Copy pattern:**
- Body: "🏠 9000 to 12000+ sqft Sky-Mansion Apartments off Sindhu Bhavan Road, Ahmedabad starting Rs 7Cr+!!"
- Bullets: "Meeting on Invitation Basis Only", "Iconic 20+ Storeyed Tower", "Limited Edition for the select Elites of India"
- Footer: Multiple hashtags (#LuxuryLiving, #AhmedabadRealEstate, #IskonAmbi, etc.)
- CTA form headline: "Sky-Mansion Apartments off Sindhu Bhavan Road, ..."
- Creative: AI-rendered ultra-luxury interiors (curved staircases, chandeliers, city skyline views)

**Why this style is a liability:**
- Emoji-heavy body copy undercuts luxury positioning
- "Limited Edition for the select Elites of India" — stating exclusivity explicitly is the opposite of demonstrating it
- Hashtag avalanche reads as social media desperation, not curated advisory
- "Meeting on Invitation Basis Only" is a strong exclusivity signal buried in bullet noise

**Lesson for Pikorua:** The "invitation only" angle is genuinely strong — but it needs to lead the copy and be the headline, not a bullet point after the square footage. Pikorua can own this territory with restraint.

---

## Lead Form Fields — resolved from screenshots

The Godrej form confirms real market practice for budget brackets and lead capture flow. Combined with Pikorua's existing CRM budget ranges, the confirmed budget brackets for Pikorua lead forms are:

- ₹2 Cr – ₹4 Cr
- ₹4 Cr – ₹7 Cr
- ₹7 Cr – ₹10 Cr
- ₹10 Cr – ₹15 Cr
- ₹15 Cr+

**Fields to capture:** Full name, Phone, Email, City, Budget bracket.
**Do not include:** EOI commitment question in cold form. Job title / company optional (see brand_voice.md [CONFIRM]).
**Post-submit CTA:** Offer a WhatsApp connection but do not auto-redirect — let the lead choose. A forced redirect signals mass handling.

---

## Google Ads

- **History:** Unknown — no export provided yet
- **Action needed:** Request Google Ads export if campaigns are running

---

## Property Listings

- **Where they live:** Unknown — likely WhatsApp/email from developers
- **Action needed:** Ask how new listings come in and whether any structured list exists

---

## WhatsApp Contacts

- **Opted-in list exists:** Unknown
- **BSP in place:** Unknown (Interakt or WhatsApp Business Cloud)
- **Action needed:** Confirm before any Phase 3 WhatsApp sends (constraint C3 — opted-in only)
- **NRI note:** The CRM export shows Abu Dhabi and London leads — these are candidates for WhatsApp outreach if opted-in

---

## Verdict

> **Can Stage 1 run today?**
> Yes — the pipeline runs fully on brief inputs. CRM data now feeds into targeting brief via crm_analyser.py.

> **What needs to happen before Phase 3?**
> 1. Confirm WhatsApp opted-in list and BSP provider
> 2. Get Meta App + long-lived Access Token (META_ACCESS_TOKEN)
> 3. Get Google Ads credentials if Google campaigns are planned
> 4. Define how property listings are sourced and structured
> 5. Clean junk leads from CRM before lookalike audience upload
