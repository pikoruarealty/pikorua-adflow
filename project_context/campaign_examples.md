# Pikorua Realty — Campaign Examples

> **Status:** Updated June 9, 2026. Real CRM data (123 leads) and competitor ad screenshots analysed.
> Competitor formats documented. Lead form fields resolved from observed live forms.
> Creative examples below are style references — not verbatim copy to reuse.

---

## How to use this file

These examples are loaded as few-shot context into the content crew agents. Each example shows what a complete Pikorua campaign looks like across all formats. The agents use these to calibrate tone, specificity, and format — not to copy verbatim.

---

## Real campaign performance benchmarks (Meta Ads, May–June 2026)

Use these numbers to set realistic expectations in targeting briefs and API payloads.
Do not invent performance figures — use these as the reference range.

| Metric | Strong | Acceptable | Underperforming |
|---|---|---|---|
| CPL (cost per lead) | ₹175–₹300 | ₹300–₹600 | ₹800+ |
| CTR (link click-through) | 1.2%+ | 0.7–1.2% | below 0.7% |
| CPM | ₹186–₹220 | ₹220–₹280 | ₹280+ |
| Daily budget (Ahmedabad) | ₹1,000–₹1,500 | — | — |

**Best performing campaign observed:** "bungalow ahmd general" — ₹175 CPL, 205 leads, ₹35,899 spend, 0.94% CTR at ₹1,000/day.

**Worst performing observed:** "GODREJ VASTRAPUR" — ₹1,025 CPL, 39 leads despite ₹39,976 spend. Highest reach (86k) but worst conversion — likely a creative or audience targeting mismatch, not a budget problem.

**Lesson for copy:** High reach does not mean high leads. Creative quality and audience specificity matter more than budget at this price point.

---

## Competitor ad formats — what NOT to do (and why)

These are real ads observed in the Ahmedabad luxury real estate market, June 2026. Document them here so the AI avoids replicating these patterns.

### Godrej Altus Vastrapur — ₹1,025 CPL (worst performer in our data)

**Ad copy observed:**
- Headline: "Super Luxurious Bungalows in Ahmedabad (INDIA)"
- Bullet specs: "300 to 2000 sq yard plots", "Ample Car Parking", "Plush with Amenities", "Less Loading ! Max Carpet Area"
- Lead form qualifier: "Are you ready to proceed with a refundable EOI (₹5L)?" with options: Yes online / Yes cheque at site / Maybe need more info
- Post-submit: "Get details on WhatsApp immediately!" → forced WhatsApp redirect

**Why it fails:**
- "(INDIA)" appended to city name — signals NRI targeting but reads awkwardly for everyone
- Spec bullets with exclamation marks ("Less Loading ! Max Carpet Area") — noisy, transactional
- EOI commitment question on a cold ad form kills conversion — a ₹7Cr buyer won't commit ₹5L to a stranger via Facebook
- Forced WhatsApp redirect signals mass handling, not personal advisory

**Pikorua must never:** Ask for financial commitment in the lead form. Force-redirect to WhatsApp. Use "(INDIA)" appended to city names.

---

### RD Group Ahmedabad — Sindhu Bhavan Road competitor

**Ad copy observed:**
- Body: "🏠 9000 to 12000+ sqft Sky-Mansion Apartments off Sindhu Bhavan Road, Ahmedabad starting Rs 7Cr+!!"
- Bullets: "Meeting on Invitation Basis Only 🎟", "Iconic 20+ Storeyed Tower 🏢", "Limited Edition for the select Elites of India 🎗"
- Hashtag block: #LuxuryLiving #AhmedabadRealEstate #Luxury #SindhuBhavanRoad #IskonAmbi #RajpathRangoli #Ahmedabad #AhmedabadLuxury
- CTA form: "Sky-Mansion Apartments off Sindhu Bhavan Road, ..." / "Get quote"
- Creative: AI-rendered interiors — curved staircase, chandelier, city skyline

**Why it fails:**
- Emoji-heavy body in a luxury ad undercuts the premium positioning immediately
- "Limited Edition for the select Elites of India" — stating exclusivity is not the same as demonstrating it; this reads as insecurity
- "Meeting on Invitation Basis Only" is the strongest line — but it's buried as a bullet, not the headline
- Hashtag avalanche at the end reads as desperation for organic reach
- "Get quote" CTA is transactional for a ₹7Cr decision

**What Pikorua can take:** The "invitation only" angle is genuinely powerful. Lead with it — make it the headline concept, not a bullet afterthought.

---

## Lead form fields — confirmed from live competitor forms (June 2026)

The Godrej Vastrapur form (real, observed) used: Budget bracket → EOI intent → Contact info (Email, Full name, Phone).

**Pikorua's confirmed lead form structure:**
1. Property-specific headline (calm, brand-voice-compliant)
2. Budget bracket qualifier — pre-qualifies without feeling intrusive:
   - ₹2 Cr – ₹4 Cr
   - ₹4 Cr – ₹7 Cr
   - ₹7 Cr – ₹10 Cr
   - ₹10 Cr – ₹15 Cr
   - ₹15 Cr+
3. Contact fields: Full name, Phone, Email, City
4. Form intro copy: "Tell us a little about what you're looking for. Jitendra reviews every enquiry personally."
5. Post-submit: Offer WhatsApp option — do not auto-redirect. Let the lead choose.

**Do not include:** EOI commitment question. Job title / company name (see brand_voice.md — optional).

---

## Example 1 — Ultra-luxury penthouse, Sindhu Bhavan Road, Ahmedabad

### Property
- **Type:** Penthouse, 12,000 sq ft
- **City:** Ahmedabad — Sindhu Bhavan Road
- **Price:** ₹14.5 Cr
- **Developer:** [Confidential / Pikorua curated]

### Persona targeted
Gujarat-based industrialist, 50–58, net worth ₹40 Cr+. Has lived in a bungalow for 20 years. Children are settled abroad. Looking for a lock-and-leave lifestyle upgrade with investment credibility. Trusts relationships, not brochures.

### Meta Ad

**Variant 1 — Aspiration**
Headline: Above the city. Finally. [22 chars]
Body: 12,000 sq ft of considered living on Sindhu Bhavan Road. Pikorua invites you to see it privately. [98 chars]

**Variant 2 — Legacy**
Headline: Built for the name after yours. [30 chars]
Body: A penthouse on Ahmedabad's finest address. Because some homes are chosen, not bought. [85 chars]

### WhatsApp Message

**Greeting:** Hello [Name],

**Hook:** A 12,000 sq ft penthouse on Sindhu Bhavan Road has just come to us — the kind that rarely reaches the market.

**Offer:** It's a curated opportunity I thought was worth sharing with you personally, given what you've told me about your next move.

**CTA:** Would you like me to arrange a private walkthrough this week? No commitment — just a conversation.

**Opt-out:** Reply STOP to unsubscribe.

*(Word count: 71)*

### Email

**Subject:** A penthouse worth seeing, Sindhu Bhavan Road
**Preview:** 12,000 sq ft. Pikorua curated. Available by private appointment.

---

Dear [Name],

A property came to us recently that I wanted to share before it goes further.

It's a 12,000 sq ft penthouse on Sindhu Bhavan Road — one of very few at this scale in Ahmedabad. Priced at ₹14.5 Cr, it's been developed to a standard that the city hasn't seen at this address before.

At Pikorua, we don't present properties we wouldn't stand behind. This one, we would.

If it's of interest, I'd welcome the chance to walk you through it privately.

Warmly,
Jitendra

**Schedule a private viewing →**

*(Word count: 108)*

---

## Example 2 — Sea-view apartment, Mumbai

### Property
- **Type:** 3BHK sea-view apartment, 2,800 sq ft
- **City:** Mumbai — Worli / Lower Parel corridor
- **Price:** ₹4.5 Cr
- **Developer:** [Confidential / Pikorua curated]

### Persona targeted
NRI in Dubai, 42, working in finance. Net worth ₹8 Cr. Parents in Mumbai. Considering buying now for parents' use and eventual return. Emotional trigger is reconnection and wanting a Mumbai address that reflects his success. Financially literate — understands asset value but leads with lifestyle.

### Meta Ad

**Variant 1 — NRI homecoming**
Headline: Your Mumbai address. Finally. [28 chars]
Body: A 3BHK sea-view on the Worli corridor. For the version of you that always planned to come back. [95 chars]

**Variant 2 — Investment return**
Headline: Sea view. Sound investment. [26 chars]
Body: Worli sea-facing inventory at this price tier is shrinking. Pikorua can show you what remains. [94 chars]

**Variant 3 — Exclusivity**
Headline: Not listed. Just curated. [25 chars]
Body: This 3BHK sea-view in Mumbai came to us before the market. As it should, for clients like you. [94 chars]

### WhatsApp Message

**Greeting:** Hello [Name],

**Hook:** A sea-view 3BHK in the Worli corridor just came through us — 2,800 sq ft, ₹4.5 Cr, available now.

**Offer:** Given your interest in Mumbai, I thought this was worth a direct message before we take it further.

**CTA:** Would a virtual walkthrough work for you this week? Happy to arrange it at a time that suits Dubai.

**Opt-out:** Reply STOP to unsubscribe.

*(Word count: 68)*

### Email

**Subject:** A sea-view apartment in Mumbai — curated for you
**Preview:** Worli corridor, 3BHK, 2,800 sq ft. Available by private appointment.

---

Dear [Name],

I'm reaching out because a property came to us that I think is worth your time.

A 3BHK sea-view apartment in the Worli corridor — 2,800 sq ft, priced at ₹4.5 Cr. Sea-facing inventory at this scale and price point in Mumbai is thinning faster than the market acknowledges.

We can arrange a virtual walkthrough for you from Dubai, with full documentation prepared in advance.

If this is the right moment, I'd be glad to walk you through it.

Warmly,
Jitendra

**Request a virtual walkthrough →**

*(Word count: 104)*

---

## Example 3 — Luxury villa, Goa (second home / lifestyle)

### Property
- **Type:** 4BHK private pool villa, 5,500 sq ft
- **City:** Goa — North Goa, Assagao / Siolim area
- **Price:** ₹8.5 Cr
- **Developer:** [Confidential / Pikorua curated]

### Persona targeted
Mumbai-based entrepreneur, 45, net worth ₹25 Cr+. Frequent Goa visitor. Rents every year, has been telling himself for three years he should buy. Primary motivation is lifestyle and a private retreat. Secondary motivation is rental yield when not in use.

### Meta Ad

**Variant 1 — Aspiration**
Headline: Stop renting what could be yours. [34 chars — trim to: Stop renting it. [17 chars]]
Body: A private pool villa in North Goa. 5,500 sq ft of the life you've been visiting for years. Pikorua. [100 chars]

**Variant 2 — Exclusivity**
Headline: North Goa. Private. Yours. [26 chars]
Body: 4BHK, private pool, Assagao. A villa Pikorua curated before it reached any portal. [82 chars]

### WhatsApp Message

**Greeting:** Hello [Name],

**Hook:** A 4BHK private pool villa in Assagao, North Goa just came to us — 5,500 sq ft, ₹8.5 Cr.

**Offer:** It hasn't reached any portal yet. I thought of you specifically, given your connection to Goa.

**CTA:** Would you like to see it the next time you're there — or virtually in the meantime?

**Opt-out:** Reply STOP to unsubscribe.

*(Word count: 65)*

### Email

**Subject:** A private pool villa in North Goa — before it's listed
**Preview:** Assagao, 4BHK, 5,500 sq ft. Pikorua curated. Available now.

---

Dear [Name],

A 4BHK private pool villa in Assagao came to us this week — the kind of property that doesn't stay quiet for long in North Goa.

5,500 sq ft, ₹8.5 Cr. It hasn't been listed anywhere yet. That's intentional — we prefer to offer our curated properties to the right buyers first.

I thought of you immediately.

If you'd like a walkthrough — in person on your next visit, or virtually — I'm happy to arrange it this week.

Warmly,
Jitendra

**Explore privately →**

*(Word count: 99)*