"""
Meta Custom Audience + Lookalike upload tool.

Reads all rows from the CRM export, hashes contact data (Meta requirement:
SHA-256, lowercase, trimmed), and uploads to Meta Ads API.

Good leads  (warm buying/client status)  → Custom Audience seed → Lookalike
Bad leads   (cold/not interested)        → Custom Audience for ad EXCLUSION
Broker leads                             → Separate Custom Audience for EXCLUSION
                                           (different use case: wastes budget + distorts
                                           Meta learning even more than a plain rejection)

Gated: requires META_ACCESS_TOKEN in environment.
Phase 3 only — not called during dry-run pipeline.
"""
import hashlib
import os
import pathlib

_CRM_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "project_context" / "crm_export.csv"

# ── Buying Status (lead_crm_details.buying_status) ───────────────────────────
# Values seen in live Supabase CRM as of 2026-06-22.
_GOOD_BUYING_STATUSES = frozenset([
    "exploring", "hot", "warm", "interested", "active", "qualified",
    "follow up", "postponed", "still searching",
])

# SiteVisitStatus values that confirm a lead physically visited or is confirmed to visit.
# These override all other status signals — a site visitor is always good.
_SITE_VISIT_CONFIRMED = frozenset(["visited", "completed", "confirmed", "visit date confirmed"])

# Budget ceiling below which an unclassified lead is excluded from the lookalike seed
# (but still kept in the good CA for ad delivery). Pikorua's lowest-priced inventory
# is ~4 Cr, so a 2-3 Cr budget is a genuine mismatch. The threshold is conservative
# to avoid incorrectly excluding legitimate leads who under-report their budget.
_LOOKALIKE_BUDGET_MIN_CR = 3.5
_BAD_BUYING_STATUSES = frozenset([
    "not_ready", "not ready", "cold", "not interested", "no interest",
    "dead", "lost", "spam", "duplicate", "invalid", "not_interested",
])

# ── Client Status (meta_leads.status) ────────────────────────────────────────
# Sales-team disposition set after the lead is worked.  Takes priority over
# buying_status when present, because it represents an explicit human judgement.
_GOOD_CLIENT_STATUSES = frozenset([
    "warm", "interested", "construction biz owner",
])
_BAD_CLIENT_STATUSES = frozenset([
    "not interested", "cold", "lost", "low budget",
])
_BROKER_CLIENT_STATUSES = frozenset([
    "broker",
])

# Combined convenience sets (used in _categorise for substring scanning)
_GOOD_STATUSES  = _GOOD_BUYING_STATUSES  | _GOOD_CLIENT_STATUSES
_BAD_STATUSES   = _BAD_BUYING_STATUSES   | _BAD_CLIENT_STATUSES


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _categorise(buying_status: str, client_status: str = "") -> str:
    """
    Return 'good', 'bad', 'broker', or 'unclassified'.

    client_status (meta_leads.status) is checked first — it represents an
    explicit human judgement and therefore takes priority.  buying_status
    (lead_crm_details.buying_status) is the fallback.  Substring match so
    'follow up (warm)' → good, 'cold lead' → bad.
    """
    cs = client_status.strip().lower()
    bs = buying_status.strip().lower()

    # Broker is a special category — checked before good/bad
    for bk in _BROKER_CLIENT_STATUSES:
        if bk in cs:
            return "broker"

    # client_status takes priority when set
    if cs:
        for g in _GOOD_CLIENT_STATUSES:
            if g in cs:
                return "good"
        for b in _BAD_CLIENT_STATUSES:
            if b in cs:
                return "bad"

    # Fall through to buying_status
    if bs:
        for g in _GOOD_BUYING_STATUSES:
            if g in bs:
                return "good"
        for b in _BAD_BUYING_STATUSES:
            if b in bs:
                return "bad"

    return "unclassified"


def _load_leads(crm_path: pathlib.Path) -> list[dict]:
    """
    Load all CRM rows with at least a phone or email.

    Returns list of dicts with:
      phone, email       — SHA-256 hashed
      category           — 'good' | 'bad' | 'broker' | 'unclassified'
      is_site_visitor    — True when SiteVisitStatus confirms a physical/confirmed visit
                           (site visitors are always 'good', overriding other statuses)
      is_low_budget      — True when budget is below _LOOKALIKE_BUDGET_MIN_CR AND the
                           lead has no explicit positive client_status (unclassified only)
      is_spoken_to       — True when CallStatus == 'spoken' AND no negative client_status
                           (secondary quality tier — better signal than untouched leads)
      city               — normalised city name (after crm_normalise, used for geo split)
    """
    from pikorua_adflow.utils import crm_source
    from pikorua_adflow.analytics.crm_analytics import parse_budget_cr

    rows, _source = crm_source.fetch_rows(crm_path)
    if not rows:
        return []

    headers = [h.strip().lower() for h in rows[0].keys()]
    phone_key    = next((h for h in headers if h in ("phone", "mobile", "contact")), None)
    email_key    = next((h for h in headers if h in ("email", "email address", "email_address")), None)
    bstatus_key  = next((h for h in headers if h in ("buyingstatus", "buying_status", "buying status")), None)
    cstatus_key  = next((h for h in headers if h in ("clientstatus", "client_status", "status")), None)
    svisit_key   = next((h for h in headers if h in ("sitevisiststatus", "site_visit_status", "sitevisit")), None)
    budget_key   = next((h for h in headers if h in ("budget", "budgetrange", "budget_range")), None)
    callstat_key = next((h for h in headers if h in ("callstatus", "call_status")), None)
    city_key     = next((h for h in headers if h in ("city", "currentcity", "current_city")), None)

    def _actual(row: dict, target: str | None) -> str | None:
        if not target:
            return None
        return next((k for k in row if k.strip().lower() == target), None)

    leads = []
    for row in rows:
        entry: dict = {}
        pk  = _actual(row, phone_key)
        ek  = _actual(row, email_key)
        bk  = _actual(row, bstatus_key)
        ck  = _actual(row, cstatus_key)
        svk = _actual(row, svisit_key)
        bgk = _actual(row, budget_key)
        cak = _actual(row, callstat_key)
        cik = _actual(row, city_key)

        if pk and str(row.get(pk, "")).strip():
            entry["phone"] = _sha256(str(row[pk]))
        if ek and str(row.get(ek, "")).strip():
            entry["email"] = _sha256(str(row[ek]))
        if not entry:
            continue

        raw_buying  = str(row.get(bk, "")).strip() if bk else ""
        raw_client  = str(row.get(ck, "")).strip() if ck else ""
        raw_svisit  = str(row.get(svk, "")).strip().lower() if svk else ""
        raw_budget  = str(row.get(bgk, "")).strip() if bgk else ""
        raw_call    = str(row.get(cak, "")).strip().lower() if cak else ""
        raw_city    = str(row.get(cik, "")).strip() if cik else ""

        # Site visitors override all other signals — they confirmed intent physically.
        is_site_visitor = any(v in raw_svisit for v in _SITE_VISIT_CONFIRMED)

        category = _categorise(raw_buying, raw_client)
        if is_site_visitor and category not in ("bad", "broker"):
            category = "good"

        # Budget signal: low-budget unclassified leads are kept in ad-delivery pool
        # but excluded from the tight lookalike seed (Item 9).
        # Only applies to unclassified leads — explicit warm/interested overrides.
        is_low_budget = False
        if category == "unclassified" and raw_budget:
            cr = parse_budget_cr(raw_budget)
            if cr is not None and cr < _LOOKALIKE_BUDGET_MIN_CR:
                is_low_budget = True

        # Spoken-to leads: reachable + not rejected → better than untouched unclassified.
        # Tag only; no CA behavior change (Item 10).
        is_spoken_to = (
            raw_call == "spoken"
            and category not in ("bad", "broker")
        )

        entry["buying_status"]   = raw_buying
        entry["client_status"]   = raw_client
        entry["category"]        = category
        entry["is_site_visitor"] = is_site_visitor
        entry["is_low_budget"]   = is_low_budget
        entry["is_spoken_to"]    = is_spoken_to
        entry["city"]            = raw_city
        leads.append(entry)

    return leads


def find_existing_audience(base_url: str, headers: dict, name: str, requests_lib) -> str | None:
    """
    Return the id of an existing Custom Audience with this exact name, or None.

    Dedup guard: the tool used to create a brand-new Custom Audience on every CRM
    upload, leaving the account with 4× duplicate "Good Leads" / "Bad Leads" CAs and
    spreading the CRM data thin. Callers reuse the existing CA (and just refresh its
    members) instead of minting another. Never raises — returns None on any failure.
    """
    try:
        resp = requests_lib.get(
            f"{base_url}/customaudiences",
            params={"fields": "id,name", "limit": 200},
            headers=headers, timeout=30,
        )
        if not resp.ok:
            return None
        for row in resp.json().get("data", []):
            if (row.get("name") or "").strip().lower() == name.strip().lower():
                return str(row["id"])
    except Exception:
        pass
    return None


def _create_custom_audience(base_url: str, headers: dict, name: str, description: str,
                             leads: list[dict], requests_lib) -> tuple[str | None, int, str | None]:
    """
    Create (or reuse) a Meta Custom Audience from a list of hashed leads.

    If a Custom Audience with the same name already exists it is REUSED — its member
    list is refreshed rather than creating a duplicate. Returns
    (audience_id, rows_uploaded, error_message).
    """
    ca_id = find_existing_audience(base_url, headers, name, requests_lib)
    if not ca_id:
        ca_payload = {
            "name": name,
            "subtype": "CUSTOM",
            "customer_file_source": "USER_PROVIDED_ONLY",
            "description": description,
        }
        ca_resp = requests_lib.post(
            f"{base_url}/customaudiences", json=ca_payload, headers=headers, timeout=30
        )
        if not ca_resp.ok:
            try:
                err = ca_resp.json().get("error", {})
                subcode = err.get("error_subcode")
                msg = err.get("message", ca_resp.text)
                if subcode == 33:
                    msg = (
                        "Ad account not found or your access token doesn't have permission for it. "
                        "Check that META_AD_ACCOUNT_ID matches the account shown in Meta Business Suite, "
                        "and that the token has ads_management permission."
                    )
            except Exception:
                msg = ca_resp.text
            return None, 0, msg

        ca_id = ca_resp.json().get("id")

    has_email = any("email" in lead for lead in leads)
    has_phone = any("phone" in lead for lead in leads)
    schema = []
    if has_email:
        schema.append("EMAIL_SHA256")
    if has_phone:
        schema.append("PHONE_SHA256")

    data_entries = []
    for lead in leads:
        row = []
        if has_email:
            row.append(lead.get("email") or "")
        if has_phone:
            row.append(lead.get("phone") or "")
        if any(v for v in row):
            data_entries.append(row)

    if not data_entries:
        return ca_id, 0, None

    up_resp = requests_lib.post(
        f"https://graph.facebook.com/v20.0/{ca_id}/users",
        json={"payload": {"schema": schema, "data": data_entries}},
        headers=headers, timeout=60,
    )
    if not up_resp.ok:
        return ca_id, 0, f"Audience user upload failed: {up_resp.text}"

    return ca_id, len(data_entries), None


def upload_crm_lookalike(
    ad_account_id: str,
    crm_path: pathlib.Path = _CRM_PATH,
    target_countries: list[str] | None = None,
) -> dict:
    """
    Upload all CRM contacts as a Meta Custom Audience and create a Lookalike.
    target_countries: ISO-2 list for the lookalike (default ["IN"]; use ["AE","US","SG"] for NRI).
    Returns dict with custom_audience_id, lookalike_audience_id, or error.
    """
    if target_countries is None:
        target_countries = ["IN"]
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        return {"error": "META_ACCESS_TOKEN not set — Phase 3 prerequisite missing."}

    try:
        import requests
    except ImportError:
        return {"error": "requests library not installed — run: pip install requests"}

    try:
        leads = _load_leads(crm_path)
    except Exception as exc:
        return {"error": f"CRM load error: {exc}"}

    if not leads:
        return {"error": "No CRM leads available — neither Supabase nor crm_export.csv returned data."}

    if len(leads) < 100:
        return {
            "error": f"Only {len(leads)} leads with contact data in CRM. "
                     "Meta requires at least 100 records for a Custom Audience. "
                     "Add more leads to the CRM export."
        }

    base_url = f"https://graph.facebook.com/v20.0/act_{ad_account_id}"
    headers = {"Authorization": f"Bearer {token}"}

    ca_id, count, err = _create_custom_audience(
        base_url, headers,
        name="PIKORUA CRM — All Contacts",
        description="Hashed CRM contacts (all leads with phone or email)",
        leads=leads,
        requests_lib=requests,
    )
    if err:
        return {"error": err}

    lal_payload = {
        "name": f"PIKORUA Lookalike — All Contacts — {','.join(target_countries)}",
        "subtype": "LOOKALIKE",
        "origin_audience_id": ca_id,
        "lookalike_spec": {
            "type": "similarity",
            "country": target_countries[0],
            "ratio": 0.01,
        },
    }
    lal_resp = requests.post(
        f"{base_url}/customaudiences", json=lal_payload, headers=headers, timeout=30
    )
    if not lal_resp.ok:
        try:
            err = lal_resp.json().get("error", {})
            lal_msg = err.get("message", lal_resp.text)
        except Exception:
            lal_msg = lal_resp.text
        return {
            "error": f"Lookalike creation failed: {lal_msg}",
            "custom_audience_id": ca_id,
            "leads_uploaded": count,
        }

    return {
        "custom_audience_id": ca_id,
        "lookalike_audience_id": lal_resp.json().get("id"),
        "leads_uploaded": count,
        "target_countries": target_countries,
    }


def upload_crm_split_audiences(
    ad_account_id: str,
    crm_path: pathlib.Path = _CRM_PATH,
    target_countries: list[str] | None = None,
) -> dict:
    """
    Split CRM leads into three categories, then:
    - Good leads     → Custom Audience + Lookalike (warm buyers + unclassified)
    - Bad leads      → Custom Audience for EXCLUSION (cold/not interested/lost)
    - Broker leads   → Separate Custom Audience for EXCLUSION (brokers distort
                       Meta's learning and waste budget — kept separate from bad
                       leads because their ad behaviour is fundamentally different)
    - Unclassified   → included in good leads pool (conservative: don't penalise unknowns)

    Returns dict with counts and audience IDs for all groups.
    target_countries: ISO-2 list for the lookalike seed (default ["IN"]).
    """
    if target_countries is None:
        target_countries = ["IN"]
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        return {"error": "META_ACCESS_TOKEN not set — Phase 3 prerequisite missing."}

    try:
        import requests
    except ImportError:
        return {"error": "requests library not installed — run: pip install requests"}

    try:
        all_leads = _load_leads(crm_path)
    except Exception as exc:
        return {"error": f"CRM load error: {exc}"}

    if not all_leads:
        return {"error": "No CRM leads available — neither Supabase nor crm_export.csv returned data."}

    # ── Split pools ─────────────────────────────────────────────────────────────
    # Good CA (ad delivery): all good + unclassified — broad for reach, used for
    # "include this audience" on ad sets so the full warm pool sees ads.
    good_leads   = [l for l in all_leads if l["category"] in ("good", "unclassified")]
    bad_leads    = [l for l in all_leads if l["category"] == "bad"]
    broker_leads = [l for l in all_leads if l["category"] == "broker"]

    # High-intent seed (lookalike): good leads minus budget-mismatched unclassified.
    # The lookalike trains Meta on who to find MORE of — we only want the signal from
    # leads who have the right intent AND right budget profile.  Low-budget unclassified
    # leads can still see ads (they're in good_leads) but shouldn't seed the model.
    high_intent_seed = [
        l for l in good_leads
        if not (l["category"] == "unclassified" and l.get("is_low_budget"))
    ]

    # Remote buyers: non-Ahmedabad leads from good pool (investors, relocators, NRI).
    # Kept separate so their lookalike targets out-of-city buyers without diluting the
    # Ahmedabad-focused main lookalike.
    remote_leads = [
        l for l in good_leads
        if l.get("city") and l["city"].lower() not in ("ahmedabad", "")
    ]

    base_url = f"https://graph.facebook.com/v20.0/act_{ad_account_id}"
    auth_headers = {"Authorization": f"Bearer {token}"}

    site_visitor_count = sum(1 for l in all_leads if l.get("is_site_visitor"))
    spoken_to_count    = sum(1 for l in all_leads if l.get("is_spoken_to"))
    low_budget_count   = sum(1 for l in all_leads if l.get("is_low_budget"))

    result: dict = {
        "total_leads":           len(all_leads),
        "good_leads_count":      len(good_leads),
        "high_intent_seed_count": len(high_intent_seed),
        "bad_leads_count":       len(bad_leads),
        "broker_leads_count":    len(broker_leads),
        "remote_leads_count":    len(remote_leads),
        "site_visitor_count":    site_visitor_count,
        "spoken_to_count":       spoken_to_count,
        "low_budget_excluded_from_seed": low_budget_count,
        "target_countries":      target_countries,
    }

    # ── Good leads: broad CA for ad delivery ────────────────────────────────
    if len(good_leads) >= 100:
        good_ca_id, good_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Good Leads (Ad Delivery)",
            description="All warm/unclassified leads — include on ad sets so they see Pikorua ads",
            leads=good_leads,
            requests_lib=requests,
        )
        if err:
            result["good_leads_error"] = err
        else:
            result["good_leads_audience_id"] = good_ca_id
            result["good_leads_uploaded"] = good_count
    else:
        result["good_leads_error"] = (
            f"Only {len(good_leads)} good/unclassified leads — Meta requires ≥100 for Custom Audience."
        )

    # ── High-intent seed: tight CA + Lookalike ──────────────────────────────
    # This is what feeds the Lookalike — only leads with the right intent AND budget
    # profile.  Keeps the lookalike signal clean.
    seed_ca_id = None
    if len(high_intent_seed) >= 100:
        seed_ca_id, seed_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — High Intent Seed (Lookalike)",
            description="Warm + site-visitor + budget-matched leads — tight seed for Meta Lookalike",
            leads=high_intent_seed,
            requests_lib=requests,
        )
        if err:
            result["seed_error"] = err
        else:
            result["seed_audience_id"] = seed_ca_id
            result["seed_uploaded"] = seed_count
            lal_name = f"PIKORUA Lookalike — High Intent — {','.join(target_countries)}"
            existing_lal = find_existing_audience(base_url, auth_headers, lal_name, requests)
            if existing_lal:
                result["good_leads_lookalike_id"] = existing_lal
                result["good_lookalike_name"] = lal_name
            else:
                lal_payload = {
                    "name": lal_name,
                    "subtype": "LOOKALIKE",
                    "origin_audience_id": seed_ca_id,
                    "lookalike_spec": {
                        "type": "similarity",
                        "country": target_countries[0],
                        "ratio": 0.01,
                    },
                }
                lal_resp = requests.post(
                    f"{base_url}/customaudiences", json=lal_payload,
                    headers=auth_headers, timeout=30,
                )
                if lal_resp.ok:
                    result["good_leads_lookalike_id"] = lal_resp.json().get("id")
                    result["good_lookalike_name"] = lal_name
                else:
                    try:
                        lal_err = lal_resp.json().get("error", {}).get("message", lal_resp.text)
                    except Exception:
                        lal_err = lal_resp.text
                    result["lookalike_error"] = f"Lookalike creation failed: {lal_err}"
    elif high_intent_seed:
        result["seed_note"] = (
            f"Only {len(high_intent_seed)} high-intent seed leads — below Meta's 100-record minimum. "
            f"({site_visitor_count} site visitors + warm leads are in this pool.)"
        )

    # ── Remote buyers: separate Lookalike for non-Ahmedabad investors ────────
    # Kept separate so the Ahmedabad lookalike isn't diluted by remote-buyer behaviour.
    # Apply to NRI-adjacent campaigns when geo-expanding beyond Gujarat.
    if len(remote_leads) >= 100:
        remote_ca_id, remote_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Remote Buyers",
            description="Non-Ahmedabad warm leads — investors, relocators, out-of-city buyers",
            leads=remote_leads,
            requests_lib=requests,
        )
        if err:
            result["remote_leads_error"] = err
        else:
            result["remote_leads_audience_id"] = remote_ca_id
            result["remote_leads_uploaded"] = remote_count
            # Lookalike targets remote cities specifically.
            remote_lal_name = f"PIKORUA Lookalike — Remote Buyers — {','.join(target_countries)}"
            existing_remote_lal = find_existing_audience(base_url, auth_headers, remote_lal_name, requests)
            if existing_remote_lal:
                result["remote_lookalike_id"] = existing_remote_lal
            else:
                remote_lal_payload = {
                    "name": remote_lal_name,
                    "subtype": "LOOKALIKE",
                    "origin_audience_id": remote_ca_id,
                    "lookalike_spec": {
                        "type": "similarity",
                        "country": target_countries[0],
                        "ratio": 0.01,
                    },
                }
                remote_lal_resp = requests.post(
                    f"{base_url}/customaudiences", json=remote_lal_payload,
                    headers=auth_headers, timeout=30,
                )
                if remote_lal_resp.ok:
                    result["remote_lookalike_id"] = remote_lal_resp.json().get("id")
                else:
                    result["remote_lookalike_note"] = "Remote lookalike creation failed — CA created, create lookalike manually."
    elif remote_leads:
        result["remote_leads_note"] = (
            f"{len(remote_leads)} remote buyers in CRM — below Meta's 100 minimum. "
            "Will be created once more non-Ahmedabad leads are tagged."
        )

    # ── Bad leads: Exclusion Custom Audience ────────────────────────────────
    if len(bad_leads) >= 100:
        bad_ca_id, bad_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Bad Leads (Exclusion)",
            description="Cold/not interested leads — exclude from ad targeting so budget is not wasted",
            leads=bad_leads,
            requests_lib=requests,
        )
        if err:
            result["bad_leads_error"] = err
        else:
            result["bad_leads_audience_id"] = bad_ca_id
            result["bad_leads_uploaded"] = bad_count
            result["bad_custom_audience_name"] = "PIKORUA CRM — Bad Leads (Exclusion)"
    elif bad_leads:
        result["bad_leads_note"] = (
            f"Only {len(bad_leads)} bad leads — below Meta's 100-record minimum. "
            "No exclusion audience created yet."
        )
    else:
        result["bad_leads_note"] = "No bad leads identified."

    # ── Broker leads: Separate Exclusion Custom Audience ────────────────────
    # Brokers are not merged with bad leads — their ad behaviour differs.
    # Bad leads are real buyers who said no; brokers are professionals who research
    # but never buy, distorting Meta's learning signal differently.
    if len(broker_leads) >= 100:
        broker_ca_id, broker_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Brokers (Exclusion)",
            description="Broker contacts — exclude to prevent budget waste and protect Meta learning quality",
            leads=broker_leads,
            requests_lib=requests,
        )
        if err:
            result["broker_leads_error"] = err
        else:
            result["broker_leads_audience_id"] = broker_ca_id
            result["broker_leads_uploaded"] = broker_count
            result["broker_custom_audience_name"] = "PIKORUA CRM — Brokers (Exclusion)"
    elif broker_leads:
        result["broker_leads_note"] = (
            f"Only {len(broker_leads)} broker leads — below Meta's 100-record minimum. "
            "Will be created once count reaches 100."
        )
    else:
        result["broker_leads_note"] = "No broker leads identified in CRM."

    return result
