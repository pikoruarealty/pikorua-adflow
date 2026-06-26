"""
Standalone CRM lead export -- good leads, bad leads, and unclassified as separate Excel files.

Usage:
  1. Set CRM_INPUT below to your CSV file path (or leave blank to use Supabase env vars).
  2. Run:  python export_crm_leads.py
  3. Three files are saved next to this script:
       pikorua_good_leads.xlsx          -- explicitly warm / interested leads
       pikorua_bad_leads.xlsx           -- cold / not-interested / lost leads
       pikorua_unclassified_leads.xlsx  -- no buying or client status set

Requires: openpyxl  (pip install openpyxl)
"""

import pathlib
import sys

# ---- Configure here ---------------------------------------------------------
# Path to your CRM CSV export.  Leave as None to use Supabase (SUPABASE_URL +
# SUPABASE_SERVICE_ROLE_KEY must be set in environment).
CRM_INPUT = None          # e.g. r"C:\Users\you\Desktop\crm_export.csv"

# Where to save the output files (default: same folder as this script)
OUTPUT_DIR = None         # e.g. r"C:\Users\you\Desktop"
# -----------------------------------------------------------------------------

_HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(_HERE / "src"))

import csv
import os

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl not installed.  Run:  pip install openpyxl")
    sys.exit(1)


_CATEGORIES_YAML = _HERE / "project_context" / "lead_categories.yaml"

# Default status sets — overridden by project_context/lead_categories.yaml when present.
_DEFAULT_GOOD_BUYING  = ["exploring","hot","warm","interested","active","qualified",
                         "follow up","postponed","still searching"]
_DEFAULT_BAD_BUYING   = ["not_ready","not ready","cold","not interested","no interest",
                         "dead","lost","spam","duplicate","invalid","not_interested"]
_DEFAULT_GOOD_CLIENT  = ["warm","interested"]
_DEFAULT_BAD_CLIENT   = ["not interested","cold","lost","low budget"]
_DEFAULT_BROKER       = ["broker"]
_DEFAULT_SITE_VISIT   = ["visited","completed","confirmed","visit date confirmed"]


def _load_categories():
    if _CATEGORIES_YAML.exists():
        try:
            import yaml
            data = yaml.safe_load(_CATEGORIES_YAML.read_text(encoding="utf-8")) or {}
            return {
                "good_buying":  frozenset(v.lower() for v in data.get("good_buying_status",   _DEFAULT_GOOD_BUYING)),
                "bad_buying":   frozenset(v.lower() for v in data.get("bad_buying_status",    _DEFAULT_BAD_BUYING)),
                "good_client":  frozenset(v.lower() for v in data.get("good_client_status",   _DEFAULT_GOOD_CLIENT)),
                "bad_client":   frozenset(v.lower() for v in data.get("bad_client_status",    _DEFAULT_BAD_CLIENT)),
                "broker":       frozenset(v.lower() for v in data.get("broker_client_status", _DEFAULT_BROKER)),
                "site_visit":   frozenset(v.lower() for v in data.get("site_visit_confirmed", _DEFAULT_SITE_VISIT)),
            }
        except Exception as exc:
            print(f"Warning: could not load lead_categories.yaml ({exc}) — using built-in defaults.")
    return {
        "good_buying":  frozenset(v.lower() for v in _DEFAULT_GOOD_BUYING),
        "bad_buying":   frozenset(v.lower() for v in _DEFAULT_BAD_BUYING),
        "good_client":  frozenset(v.lower() for v in _DEFAULT_GOOD_CLIENT),
        "bad_client":   frozenset(v.lower() for v in _DEFAULT_BAD_CLIENT),
        "broker":       frozenset(v.lower() for v in _DEFAULT_BROKER),
        "site_visit":   frozenset(v.lower() for v in _DEFAULT_SITE_VISIT),
    }


_CATS = _load_categories()
_GOOD_BUYING   = _CATS["good_buying"]
_BAD_BUYING    = _CATS["bad_buying"]
_GOOD_CLIENT   = _CATS["good_client"]
_BAD_CLIENT    = _CATS["bad_client"]
_BROKER_CLIENT = _CATS["broker"]
_SITE_VISIT_OK = _CATS["site_visit"]


def _categorise(buying, client):
    cs, bs = client.strip().lower(), buying.strip().lower()
    for bk in _BROKER_CLIENT:
        if bk in cs:
            return "broker"
    if cs:
        for g in _GOOD_CLIENT:
            if g in cs: return "good"
        for b in _BAD_CLIENT:
            if b in cs: return "bad"
    if bs:
        for g in _GOOD_BUYING:
            if g in bs: return "good"
        for b in _BAD_BUYING:
            if b in bs: return "bad"
    return "unclassified"


def _load_rows():
    if CRM_INPUT:
        p = pathlib.Path(CRM_INPUT)
        with p.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        print(f"Loaded {len(rows)} rows from {p}.")
        return rows

    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_ANON_KEY", "").strip()
    if url and key:
        try:
            import requests
            _SELECT = (
                "full_name,phone,email,city,campaign_name,source,status,received_at,assigned_to,"
                "lead_crm_details(budget_range,profession,company_name,current_city,current_area,"
                "configuration,call_status,buying_status,client_status,hwc,remarks,site_visit_status)"
            )
            headers = {"apikey": key, "Authorization": f"Bearer {key}"}
            rows, offset, page_size = [], 0, 1000
            while True:
                resp = requests.get(
                    f"{url}/rest/v1/meta_leads",
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
                    if isinstance(crm, list):
                        crm = crm[0] if crm else {}
                    client_status = crm.get("client_status") or r.get("status") or ""
                    rows.append({
                        "Name": r.get("full_name") or "",
                        "Phone": r.get("phone") or "",
                        "Email": r.get("email") or "",
                        "City": r.get("city") or crm.get("current_city") or "",
                        "Campaign": r.get("campaign_name") or "",
                        "Source": r.get("source") or "",
                        "Status": client_status,
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
            if rows:
                print(f"Loaded {len(rows)} rows from Supabase.")
                return rows
        except Exception as exc:
            print(f"Supabase fetch failed ({exc}) -- trying default CSV path.")

    default_csv = _HERE / "project_context" / "crm_export.csv"
    if default_csv.exists():
        with default_csv.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        print(f"Loaded {len(rows)} rows from {default_csv}.")
        return rows

    print("ERROR: No CRM data found. Set CRM_INPUT or configure SUPABASE_URL + key.")
    sys.exit(1)


def _get_raw(row, *names):
    """Fetch a field tolerant of space / underscore / case variants in column names."""
    norm_map = {
        k.strip().lower().replace(" ", "").replace("_", ""): str(v or "").strip()
        for k, v in row.items()
    }
    for name in names:
        if name in row:
            return str(row[name] or "").strip()
        normed = name.strip().lower().replace(" ", "").replace("_", "")
        if normed in norm_map:
            return norm_map[normed]
    return ""


def _categorise_rows(rows):
    result = []
    for row in rows:
        raw_client = (
            _get_raw(row, "Client Status", "ClientStatus", "client_status")
            or _get_raw(row, "Status", "status")
        )
        raw_buying = _get_raw(row, "Buying Status", "BuyingStatus", "buying_status")
        raw_svisit = _get_raw(row, "Site Visit Status", "SiteVisitStatus", "site_visit_status").lower()

        is_sv = any(v in raw_svisit for v in _SITE_VISIT_OK)
        cat = _categorise(raw_buying, raw_client)
        if is_sv and cat not in ("bad", "broker"):
            cat = "good"

        result.append({**row, "Category": cat})
    return result


def _write_excel(rows, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    if not rows:
        ws.append(["No data"])
        wb.save(path)
        return
    cols = list(rows[0].keys())
    ws.append(cols)
    for row in rows:
        ws.append([row.get(c, "") for c in cols])
    wb.save(path)


def main():
    out_dir = pathlib.Path(OUTPUT_DIR) if OUTPUT_DIR else _HERE

    print("Loading CRM rows...")
    raw = _load_rows()
    print(f"Categorising {len(raw)} rows...")
    rows = _categorise_rows(raw)

    good         = [r for r in rows if r["Category"] == "good"]
    bad          = [r for r in rows if r["Category"] == "bad"]
    unclassified = [r for r in rows if r["Category"] == "unclassified"]
    brokers      = [r for r in rows if r["Category"] == "broker"]

    good_path = out_dir / "pikorua_good_leads.xlsx"
    bad_path  = out_dir / "pikorua_bad_leads.xlsx"
    unc_path  = out_dir / "pikorua_unclassified_leads.xlsx"

    _write_excel(good, good_path)
    print(f"Saved {len(good):,} good leads         -> {good_path}")

    _write_excel(bad, bad_path)
    print(f"Saved {len(bad):,} bad leads          -> {bad_path}")

    _write_excel(unclassified, unc_path)
    print(f"Saved {len(unclassified):,} unclassified leads -> {unc_path}")

    if brokers:
        print(f"Note: {len(brokers)} broker leads excluded from all files (use for Meta exclusion only).")


if __name__ == "__main__":
    main()
