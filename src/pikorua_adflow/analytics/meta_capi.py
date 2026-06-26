"""
Meta Conversions API (CAPI) — server-side qualified-lead feedback.

When a CRM lead becomes interested/warm/hot, we send a server-side QualifiedLead
event back to Meta via CAPI. This teaches Advantage+ which form-fillers actually
convert, tightening targeting quality over time without changing CPL targets.

Two-stage flow:
  1. Meta fires a webhook when a lead submits a form → webhook.py calls
     store_leadgen_id(), which writes to outputs/leadgen_mapping.json keyed by
     hashed phone/email (no PII stored in plaintext).
  2. The daily autooptimiser pass calls fire_pending_qualified_leads() which
     scans CRM leads for newly-qualified ones, matches them via the mapping,
     and sends a CAPI QualifiedLead event for each new match.

Requires env vars (both must be set; either missing = no-op, no crash):
  META_CAPI_TOKEN      — CAPI-specific access token from Events Manager
  META_CAPI_DATASET_ID — dataset/pixel ID from Events Manager
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

_REPO_ROOT    = Path(__file__).resolve().parents[3]
_OUTPUT_DIR   = _REPO_ROOT / "outputs"
_MAPPING_PATH = _OUTPUT_DIR / "leadgen_mapping.json"
_SENT_PATH    = _OUTPUT_DIR / "capi_sent.json"

_CAPI_URL = "https://graph.facebook.com/v21.0/{dataset_id}/events"

_QUALITY_BUYING = {"exploring", "warm", "hot", "interested"}
_QUALITY_CLIENT = {"hot", "warm", "qualified", "site_visit", "site visit"}


# ── Credentials ───────────────────────────────────────────────────────────────

def _creds() -> tuple[str, str] | None:
    token      = os.getenv("META_CAPI_TOKEN", "").strip()
    dataset_id = os.getenv("META_CAPI_DATASET_ID", "").strip()
    return (token, dataset_id) if token and dataset_id else None


# ── Phone / hash helpers ──────────────────────────────────────────────────────

def _norm_phone(phone: str) -> str:
    """Digits only, strip leading +91 / 91 / 0."""
    d = "".join(c for c in phone if c.isdigit())
    if d.startswith("91") and len(d) == 12:
        d = d[2:]
    elif d.startswith("0") and len(d) == 11:
        d = d[1:]
    return d


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


# ── Mapping persistence ───────────────────────────────────────────────────────

def _load_mapping() -> dict:
    try:
        return json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_mapping(m: dict) -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _MAPPING_PATH.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_sent() -> set:
    try:
        return set(json.loads(_SENT_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_sent(s: set) -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _SENT_PATH.write_text(json.dumps(sorted(s), indent=2, ensure_ascii=False), encoding="utf-8")


# ── Public API ────────────────────────────────────────────────────────────────

def store_leadgen_id(leadgen_id: str, phone: str = "", email: str = "",
                     created_time: int | None = None) -> None:
    """
    Called by the webhook handler when a new lead form submission arrives.
    Keys the mapping by SHA-256(normalised_phone) and SHA-256(email) — no PII stored.
    """
    if not leadgen_id:
        return
    mapping = _load_mapping()
    entry = {"leadgen_id": leadgen_id, "created_time": created_time or int(time.time())}
    if phone:
        norm = _norm_phone(phone)
        if norm:
            mapping[f"ph:{_sha256(norm)}"] = entry
    if email:
        mapping[f"em:{_sha256(email)}"] = entry
    _save_mapping(mapping)


def _lookup_leadgen_id(phone: str, email: str) -> str | None:
    mapping = _load_mapping()
    if phone:
        hit = mapping.get(f"ph:{_sha256(_norm_phone(phone))}")
        if hit:
            return hit["leadgen_id"]
    if email:
        hit = mapping.get(f"em:{_sha256(email)}")
        if hit:
            return hit["leadgen_id"]
    return None


def send_qualified_lead_event(leadgen_id: str, event_time_unix: int | None = None) -> dict:
    """
    Send a QualifiedLead server-side event to Meta via CAPI.
    Returns {ok, leadgen_id} or {error, leadgen_id}.
    """
    creds = _creds()
    if not creds:
        return {"error": "META_CAPI_TOKEN or META_CAPI_DATASET_ID not set.", "leadgen_id": leadgen_id}
    token, dataset_id = creds

    payload = {
        "data": [{
            "event_name": "QualifiedLead",
            "event_time": event_time_unix or int(time.time()),
            "action_source": "crm",
            "user_data": {"lead_id": str(leadgen_id)},
            "custom_data": {
                "lead_event_source": "CRM",
                "event_source": "crm_qualification",
            },
        }],
        "access_token": token,
    }
    url = _CAPI_URL.format(dataset_id=dataset_id)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        return {"ok": True, "leadgen_id": leadgen_id, "meta_response": result}
    except urllib.error.HTTPError as exc:
        body_err = exc.read().decode(errors="replace")
        return {"error": f"HTTP {exc.code}: {body_err}", "leadgen_id": leadgen_id}
    except Exception as exc:
        return {"error": str(exc), "leadgen_id": leadgen_id}


def fire_pending_qualified_leads(crm_leads: list[dict]) -> dict:
    """
    Scan CRM leads for newly-qualified ones and fire CAPI QualifiedLead events.
    Called during the daily autooptimiser pass. Safe to call repeatedly — already-sent
    leadgen_ids are tracked in outputs/capi_sent.json and skipped.

    Returns {fired, skipped_no_mapping, already_sent, skipped_no_creds}.
    """
    if not _creds():
        return {"fired": [], "skipped_no_mapping": 0, "already_sent": 0, "skipped_no_creds": True}

    sent = _load_sent()
    fired: list[dict] = []
    skipped_no_mapping = 0
    already_sent = 0

    for lead in crm_leads:
        buying = (lead.get("BuyingStatus") or "").lower().strip()
        client = (lead.get("Status") or lead.get("ClientStatus") or "").lower().strip()
        if buying not in _QUALITY_BUYING and client not in _QUALITY_CLIENT:
            continue

        phone = lead.get("Phone") or ""
        email = lead.get("Email") or ""
        leadgen_id = _lookup_leadgen_id(phone, email)
        if not leadgen_id:
            skipped_no_mapping += 1
            continue
        if leadgen_id in sent:
            already_sent += 1
            continue

        result = send_qualified_lead_event(leadgen_id)
        if result.get("ok"):
            sent.add(leadgen_id)
        fired.append({"leadgen_id": leadgen_id,
                      "phone_tail": phone[-4:] if len(phone) >= 4 else "",
                      "ok": result.get("ok", False),
                      "error": result.get("error")})

    if any(f.get("ok") for f in fired):
        _save_sent(sent)

    return {
        "fired": fired,
        "skipped_no_mapping": skipped_no_mapping,
        "already_sent": already_sent,
        "skipped_no_creds": False,
    }
