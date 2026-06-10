"""
Meta Custom Audience + Lookalike upload tool.

Reads CRM export, filters to leads at or beyond min_stage, hashes contact data
(Meta requirement: SHA-256, lowercase, trimmed), and uploads to Meta Ads API.

Gated: requires META_ACCESS_TOKEN in environment. Returns error dict if missing.
Phase 3 only — not called during dry-run pipeline.
"""
import csv
import hashlib
import os
import pathlib

_CRM_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "project_context" / "crm_export.csv"

_STAGE_ORDER = {
    "contacted": 1,
    "follow_up": 2,
    "site_visit": 3,
    "negotiating": 4,
    "converted": 5,
    "dead": 0,
    "lost": 0,
}

_MIN_STAGE_DEFAULT = "site_visit"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _stage_rank(stage: str) -> int:
    return _STAGE_ORDER.get(stage.strip().lower().replace(" ", "_"), 0)


def _load_qualified_leads(crm_path: pathlib.Path, min_stage: str) -> list[dict]:
    min_rank = _stage_rank(min_stage)
    qualified = []
    with crm_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]

        phone_col = next((h for h in headers if h in ("phone", "mobile", "contact")), None)
        email_col = next((h for h in headers if h in ("email", "email_address")), None)
        stage_col = next((h for h in headers if h in ("lead_stage", "stage", "status")), None)

        if not stage_col:
            raise ValueError("CRM CSV missing lead_stage column")

        for row in reader:
            stage = row.get(stage_col, "")
            if _stage_rank(stage) >= min_rank:
                entry = {}
                if phone_col and row.get(phone_col, "").strip():
                    entry["phone"] = _sha256(row[phone_col])
                if email_col and row.get(email_col, "").strip():
                    entry["email"] = _sha256(row[email_col])
                if entry:
                    qualified.append(entry)

    return qualified


def upload_crm_lookalike(
    ad_account_id: str,
    crm_path: pathlib.Path = _CRM_PATH,
    min_stage: str = _MIN_STAGE_DEFAULT,
    target_countries: list[str] | None = None,
) -> dict:
    """
    Upload qualified CRM leads as a Meta Custom Audience and create a Lookalike.
    target_countries: ISO-2 list for the lookalike (default ["IN"]; use ["AE","US","SG"] for NRI).
    Returns dict with custom_audience_id, lookalike_audience_id, or error.
    """
    if target_countries is None:
        target_countries = ["IN"]
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        return {"error": "META_ACCESS_TOKEN not set — Phase 3 prerequisite missing."}

    if not crm_path.exists():
        return {"error": "crm_export.csv not found in project_context/."}

    try:
        import requests
    except ImportError:
        return {"error": "requests library not installed — run: pip install requests"}

    try:
        leads = _load_qualified_leads(crm_path, min_stage)
    except Exception as exc:
        return {"error": f"CRM parse error: {exc}"}

    if len(leads) < 100:
        return {
            "error": f"Only {len(leads)} qualified leads (min_stage='{min_stage}'). "
                     "Meta requires at least 100 records for a Custom Audience. "
                     "Lower min_stage or add more leads."
        }

    base_url = f"https://graph.facebook.com/v20.0/act_{ad_account_id}"
    headers = {"Authorization": f"Bearer {token}"}

    # Step 1 — create Custom Audience
    ca_payload = {
        "name": f"Pikorua CRM — {min_stage}+ leads",
        "subtype": "CUSTOM",
        "customer_file_source": "USER_PROVIDED_ONLY",
        "description": f"Hashed CRM leads at or beyond stage: {min_stage}",
    }
    ca_resp = requests.post(f"{base_url}/customaudiences", json=ca_payload, headers=headers, timeout=30)
    if not ca_resp.ok:
        return {"error": f"Custom Audience creation failed: {ca_resp.text}"}

    ca_id = ca_resp.json().get("id")

    # Step 2 — upload hashed users
    # Build one row per lead containing only the fields that lead actually has.
    # Meta requires consistent schema across all rows, so use EXTERN_ID as a
    # stable anchor when a lead is missing email or phone — this prevents empty
    # string misalignment when the schema has both EMAIL_SHA256 and PHONE_SHA256.
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
        # Skip rows that would be entirely empty (lead had neither email nor phone)
        if any(v for v in row):
            data_entries.append(row)

    upload_payload = {
        "payload": {
            "schema": schema,
            "data": data_entries,
        }
    }
    up_resp = requests.post(
        f"https://graph.facebook.com/v20.0/{ca_id}/users",
        json=upload_payload, headers=headers, timeout=60,
    )
    if not up_resp.ok:
        return {"error": f"Audience user upload failed: {up_resp.text}", "custom_audience_id": ca_id}

    # Step 3 — create Lookalike Audience
    lal_payload = {
        "name": f"Pikorua Lookalike — {min_stage}+ leads — {','.join(target_countries)}",
        "subtype": "LOOKALIKE",
        "origin_audience_id": ca_id,
        "lookalike_spec": {
            "type": "similarity",
            "country": target_countries[0],  # primary market
            "ratio": 0.01,  # top 1% similarity
        },
    }
    lal_resp = requests.post(f"{base_url}/customaudiences", json=lal_payload, headers=headers, timeout=30)
    if not lal_resp.ok:
        return {
            "error": f"Lookalike creation failed: {lal_resp.text}",
            "custom_audience_id": ca_id,
            "leads_uploaded": len(data_entries),
        }

    lal_id = lal_resp.json().get("id")
    return {
        "custom_audience_id": ca_id,
        "lookalike_audience_id": lal_id,
        "leads_uploaded": len(data_entries),
        "min_stage": min_stage,
        "target_countries": target_countries,
    }
