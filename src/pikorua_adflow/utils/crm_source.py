"""
Single source of truth for CRM lead rows.

Both the CRM analyser (crm_analyser.py) and the Meta lookalike uploader
(meta_audience_tool.py) pull their lead rows from here, so there is one place
that decides where lead data comes from.

Priority:
1. Supabase  — if SUPABASE_URL and a key (SERVICE_ROLE preferred, else ANON) are
   in the environment, fetch live leads from the `meta_leads` table joined with
   `lead_crm_details`. This is the production source.
2. CSV       — fall back to project_context/crm_export.csv if Supabase env vars
   are absent or the fetch fails. Keeps local/offline dev working.

Rows are returned as a list of dicts with CSV-style header keys (Name, Phone,
Email, City, Campaign, Source, Status, Budget, Profession, Company, Received) so
the existing column-alias resolution in crm_analyser works unchanged regardless
of source.

Uses the PostgREST REST endpoint via `requests` — no extra dependency, no
supabase client to maintain (constraint C7: maintainable by a non-original dev).
"""
import csv
import os
import pathlib

_CSV_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "project_context" / "crm_export.csv"

# PostgREST embed: meta_leads holds the contact/campaign columns; lead_crm_details
# (FK lead_id -> meta_leads.id) holds budget/profession/company. One joined query.
# lead_crm_details has no client_status column (verified against the live schema) —
# meta_leads.status is assignment routing only (assigned/unassigned/cold_pool), not
# disposition. meta_leads.client_id is the FK into `clients`, which holds one row
# per enquiry/lead for that client — status there (warm/hot/cold/broker/lost/...)
# is the real per-lead disposition and is joined in separately below.
_SELECT = (
    "full_name,phone,email,city,campaign_name,source,status,received_at,assigned_to,client_id,"
    "lead_crm_details(budget_range,profession,company_name,current_city,current_area,"
    "configuration,call_status,buying_status,hwc,remarks,site_visit_status)"
)


def _supabase_creds() -> tuple[str, str] | None:
    """Return (base_url, key) if Supabase is configured, else None."""
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )
    if url and key:
        return url, key
    return None


def _fetch_clients_by_id(base_url: str, headers: dict) -> dict[str, dict]:
    """
    `clients` holds one row per enquiry/lead for a given person — status there
    (warm/hot/cold/broker/lost/postponed/...) is the real per-lead disposition,
    keyed by clients.id which meta_leads.client_id references directly.
    """
    import requests

    lookup: dict[str, dict] = {}
    page_size = 1000
    offset = 0
    while True:
        resp = requests.get(
            f"{base_url}/rest/v1/clients",
            params={"select": "id,status,status_note", "deleted_at": "is.null"},
            headers={**headers, "Range-Unit": "items", "Range": f"{offset}-{offset + page_size - 1}"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for c in batch:
            lookup[c["id"]] = c
        if len(batch) < page_size:
            break
        offset += page_size
    return lookup


def _fetch_supabase(base_url: str, key: str) -> list[dict]:
    """Fetch all CRM leads from Supabase and normalise to CSV-style header keys."""
    import requests

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    clients_by_id = _fetch_clients_by_id(base_url, headers)
    rows: list[dict] = []
    page_size = 1000
    offset = 0
    # PostgREST caps each response (default 1000). Page until a short page returns.
    while True:
        resp = requests.get(
            f"{base_url}/rest/v1/meta_leads",
            params={"select": _SELECT, "deleted_at": "is.null"},
            headers={**headers, "Range-Unit": "items", "Range": f"{offset}-{offset + page_size - 1}"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for r in batch:
            crm = r.get("lead_crm_details") or {}
            if isinstance(crm, list):  # PostgREST may return a list for the embed
                crm = crm[0] if crm else {}
            client = clients_by_id.get(r.get("client_id") or "", {})
            rows.append({
                "Name": r.get("full_name") or "",
                "Phone": r.get("phone") or "",
                "Email": r.get("email") or "",
                "City": r.get("city") or crm.get("current_city") or "",
                "Campaign": r.get("campaign_name") or "",
                "Source": r.get("source") or "",
                "Status": r.get("status") or "",
                "Client Status": client.get("status") or "",
                "Client Status Note": client.get("status_note") or "",
                "Received": r.get("received_at") or "",
                "Budget": crm.get("budget_range") or "",
                "Profession": crm.get("profession") or "",
                "Company": crm.get("company_name") or "",
                "CurrentCity": crm.get("current_city") or "",
                "CurrentArea": crm.get("current_area") or "",
                "Configuration": crm.get("configuration") or "",
                "CallStatus": crm.get("call_status") or "",
                "BuyingStatus": crm.get("buying_status") or "",
                "SiteVisitStatus": crm.get("site_visit_status") or "",
                "HWC": crm.get("hwc") or "",
                "AssignedTo": r.get("assigned_to") or "",
                "Remarks": crm.get("remarks") or "",
            })
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _fetch_csv(csv_path: pathlib.Path) -> list[dict]:
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def fetch_rows(csv_path: pathlib.Path = _CSV_PATH) -> tuple[list[dict], str]:
    """
    Return (rows, source_label).

    source_label is a human-readable description of where the data came from,
    for surfacing in the insights report. Never raises — on any Supabase error
    it falls back to the CSV, and if that is also missing returns an empty list.
    """
    creds = _supabase_creds()
    if creds:
        try:
            rows = _fetch_supabase(*creds)
            if rows:
                return _normalise(rows), f"Supabase (meta_leads + lead_crm_details, {len(rows)} leads)"
            # Empty result is suspicious — fall through to CSV rather than report 0.
        except Exception as exc:
            print(f"[crm_source] Supabase fetch failed ({exc}) — falling back to CSV.")

    if csv_path.exists():
        try:
            rows = _fetch_csv(csv_path)
            return _normalise(rows), f"CSV ({csv_path.name}, {len(rows)} leads)"
        except Exception as exc:
            print(f"[crm_source] CSV read failed ({exc}).")

    return [], "no CRM source available"


def _normalise(rows: list[dict]) -> list[dict]:
    """Apply city + profession normalisation. Imported lazily to avoid circular deps."""
    try:
        from pikorua_adflow.analytics.crm_normalise import normalise_rows
        return normalise_rows(rows)
    except Exception as exc:
        print(f"[crm_source] normalisation skipped ({exc}).")
        return rows
