"""
Meta Custom Audience + Lookalike upload tool.

Reads all rows from the CRM export, hashes contact data (Meta requirement:
SHA-256, lowercase, trimmed), and uploads to Meta Ads API.

Good leads (hot/warm BuyingStatus) → Custom Audience seed → Lookalike Audience
Bad leads  (cold/not interested)   → Custom Audience for ad EXCLUSION targeting

Gated: requires META_ACCESS_TOKEN in environment.
Phase 3 only — not called during dry-run pipeline.
"""
import hashlib
import os
import pathlib

_CRM_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "project_context" / "crm_export.csv"

# BuyingStatus values observed in the live Supabase CRM (queried 2026-06-11):
#   'exploring'  — actively looking at options → good lookalike seed
#   'not_ready'  — explicitly not ready to buy → exclude from targeting
#   ''           — no status set yet           → unclassified → goes to good pool
# Case-insensitive substring match so partial/combined values also resolve correctly.
_GOOD_STATUSES = frozenset(["exploring", "hot", "warm", "interested", "active", "qualified", "follow up"])
_BAD_STATUSES  = frozenset(["not_ready", "not ready", "cold", "not interested", "no interest",
                            "dead", "lost", "spam", "duplicate", "invalid", "not_interested"])


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _categorise(buying_status: str) -> str:
    """
    Return 'good', 'bad', or 'unclassified' based on BuyingStatus text.
    Substring match so 'follow up (warm)' → good, 'cold lead' → bad.
    """
    s = buying_status.strip().lower()
    if not s:
        return "unclassified"
    for g in _GOOD_STATUSES:
        if g in s:
            return "good"
    for b in _BAD_STATUSES:
        if b in s:
            return "bad"
    return "unclassified"


def _load_leads(crm_path: pathlib.Path) -> list[dict]:
    """
    Load all CRM rows (Supabase if configured, else CSV) that have at least a
    phone or email. Returns list of dicts with keys: phone, email (SHA-256 hashed),
    category ('good' | 'bad' | 'unclassified'), buying_status (raw).
    """
    from pikorua_adflow.utils import crm_source

    rows, _source = crm_source.fetch_rows(crm_path)
    if not rows:
        return []

    headers = [h.strip().lower() for h in rows[0].keys()]
    phone_key = next((h for h in headers if h in ("phone", "mobile", "contact")), None)
    email_key = next((h for h in headers if h in ("email", "email address", "email_address")), None)
    # BuyingStatus key as normalised to lowercase
    bstatus_key = next((h for h in headers if h in ("buyingstatus", "buying_status", "buying status")), None)

    def _actual(row: dict, target: str | None) -> str | None:
        if not target:
            return None
        return next((k for k in row if k.strip().lower() == target), None)

    leads = []
    for row in rows:
        entry = {}
        pk = _actual(row, phone_key)
        ek = _actual(row, email_key)
        bk = _actual(row, bstatus_key)
        if pk and str(row.get(pk, "")).strip():
            entry["phone"] = _sha256(str(row[pk]))
        if ek and str(row.get(ek, "")).strip():
            entry["email"] = _sha256(str(row[ek]))
        if entry:
            raw_status = str(row.get(bk, "")).strip() if bk else ""
            entry["buying_status"] = raw_status
            entry["category"] = _categorise(raw_status)
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
    Split CRM leads into good (hot/warm) and bad (cold/not interested) categories, then:
    - Good leads  → Custom Audience + Lookalike (show more ads to similar people)
    - Bad leads   → Custom Audience for EXCLUSION (don't show ads to these people)
    - Unclassified → included in good leads pool (conservative: don't penalise unknowns)

    Returns dict with counts and audience IDs for both groups.
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

    # Split into categories
    good_leads = [l for l in all_leads if l["category"] in ("good", "unclassified")]
    bad_leads  = [l for l in all_leads if l["category"] == "bad"]

    base_url = f"https://graph.facebook.com/v20.0/act_{ad_account_id}"
    auth_headers = {"Authorization": f"Bearer {token}"}

    result: dict = {
        "total_leads": len(all_leads),
        "good_leads_count": len(good_leads),
        "bad_leads_count": len(bad_leads),
        "target_countries": target_countries,
    }

    # ── Good leads: Custom Audience + Lookalike ──────────────────────────────
    if len(good_leads) >= 100:
        good_ca_id, good_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Good Leads (Hot/Warm)",
            description="Hot and warm leads — use as Lookalike seed to find similar high-intent buyers",
            leads=good_leads,
            requests_lib=requests,
        )
        if err:
            result["good_leads_error"] = err
        else:
            result["good_leads_audience_id"] = good_ca_id
            result["good_leads_uploaded"] = good_count
            # Create Lookalike from good leads (reuse if one with this name exists).
            lal_name = f"PIKORUA Lookalike — Good Leads — {','.join(target_countries)}"
            existing_lal = find_existing_audience(base_url, auth_headers, lal_name, requests)
            if existing_lal:
                result["good_leads_lookalike_id"] = existing_lal
                result["good_lookalike_name"] = lal_name
                lal_resp = None
            else:
                lal_payload = {
                    "name": lal_name,
                    "subtype": "LOOKALIKE",
                    "origin_audience_id": good_ca_id,
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
            if lal_resp is None:
                pass
            elif lal_resp.ok:
                result["good_leads_lookalike_id"] = lal_resp.json().get("id")
                result["good_lookalike_name"] = lal_name
            else:
                try:
                    lal_err = lal_resp.json().get("error", {}).get("message", lal_resp.text)
                except Exception:
                    lal_err = lal_resp.text
                result["lookalike_error"] = f"Lookalike creation failed: {lal_err}"
    else:
        result["good_leads_error"] = (
            f"Only {len(good_leads)} good/unclassified leads — Meta requires ≥100 for Custom Audience. "
            "Update BuyingStatus for more leads in the CRM."
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
            f"Only {len(bad_leads)} bad leads — below Meta's 100-record minimum for a Custom Audience. "
            "No exclusion audience created yet; add more bad leads to the CRM."
        )
    else:
        result["bad_leads_note"] = "No bad leads identified — all leads are good/unclassified."

    return result
