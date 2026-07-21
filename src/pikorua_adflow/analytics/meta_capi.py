"""
Meta Conversions API (CAPI) — server-side qualified-lead feedback.

When a CRM lead becomes interested/warm/hot, we send a server-side QualifiedLead
event back to Meta via CAPI. This teaches Advantage+ which form-fillers actually
convert, tightening targeting quality over time without changing CPL targets.

We send BOTH directions of signal so Meta can learn lead quality:
  • QualifiedLead    — a lead the sales team marked good/warm/hot/interested.
  • DisqualifiedLead — a lead marked bad/junk/lost/broker. This negative signal
                       teaches Advantage+ which form-fillers to AVOID, which the
                       positive signal alone cannot do.

Two-stage flow:
  1. Meta fires a webhook when a lead submits a form → the webhook handler calls
     store_leadgen_id(), which writes to outputs/leadgen_mapping.json keyed by
     hashed phone/email (no PII stored in plaintext).
  2. The daily autooptimiser pass calls fire_pending_lead_events() which scans
     CRM leads, classifies each via the shared lead_rules engine, matches it to a
     leadgen_id via the mapping, and sends the matching CAPI event once.

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

# Standard Meta event names. Map BOTH of these to funnel stages in Events Manager
# (Conversions API for CRM → "Conversion leads") for the signal to affect delivery.
_EVENT_QUALIFIED = "QualifiedLead"
_EVENT_DISQUALIFIED = "DisqualifiedLead"


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


def _load_sent() -> dict[str, set]:
    """
    Return already-sent leadgen_ids per event type:
        {"qualified": {...}, "disqualified": {...}}
    Backward-compatible: a legacy flat-list file loads as the qualified set, so a
    lead already told "good" won't be re-sent after this upgrade.
    """
    try:
        raw = json.loads(_SENT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"qualified": set(), "disqualified": set()}
    if isinstance(raw, list):  # legacy format
        return {"qualified": set(raw), "disqualified": set()}
    return {
        "qualified": set(raw.get("qualified", [])),
        "disqualified": set(raw.get("disqualified", [])),
    }


def _save_sent(sent: dict[str, set]) -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {k: sorted(v) for k, v in sent.items()}
    _SENT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


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


def _send_lead_event(leadgen_id: str, event_name: str,
                     event_time_unix: int | None = None,
                     phone: str = "", email: str = "") -> dict:
    """
    Send one server-side lead event (QualifiedLead / DisqualifiedLead) to Meta.
    Payload shape matches Meta's own CRM-integration example exactly (action_source
    "system_generated", hashed em/ph in user_data alongside lead_id) for best match quality.
    Returns {ok, leadgen_id, meta_response} or {error, leadgen_id}.
    """
    creds = _creds()
    if not creds:
        return {"error": "META_CAPI_TOKEN or META_CAPI_DATASET_ID not set.", "leadgen_id": leadgen_id}
    token, dataset_id = creds

    lead_id_value: int | str = int(leadgen_id) if str(leadgen_id).isdigit() else str(leadgen_id)
    user_data: dict = {"lead_id": lead_id_value}
    if phone:
        norm = _norm_phone(phone)
        if norm:
            user_data["ph"] = [_sha256(norm)]
    if email:
        user_data["em"] = [_sha256(email)]

    payload = {
        "data": [{
            "event_name": event_name,
            "event_time": event_time_unix or int(time.time()),
            "action_source": "system_generated",
            "user_data": user_data,
            "custom_data": {
                "event_source": "crm",
                "lead_event_source": "Pikorua CRM",
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


def send_qualified_lead_event(leadgen_id: str, event_time_unix: int | None = None,
                              phone: str = "", email: str = "") -> dict:
    """Send a QualifiedLead server-side event to Meta via CAPI."""
    return _send_lead_event(leadgen_id, _EVENT_QUALIFIED, event_time_unix, phone=phone, email=email)


def send_disqualified_lead_event(leadgen_id: str, event_time_unix: int | None = None,
                                 phone: str = "", email: str = "") -> dict:
    """Send a DisqualifiedLead (negative signal) server-side event to Meta via CAPI."""
    return _send_lead_event(leadgen_id, _EVENT_DISQUALIFIED, event_time_unix, phone=phone, email=email)


def _classify_lead(lead: dict) -> str:
    """
    Classify a raw CRM lead as 'good' | 'bad' | 'broker' | 'unclassified' using the
    SAME editable rule engine the rest of the app uses (analytics.lead_rules), after
    normalising the row's field names. Reusing it keeps CAPI's good/bad judgement
    consistent with Lead Insights and the lookalike seed — and correctly ignores the
    meta_leads.status assignment field, which is routing, not a disposition.
    """
    try:
        from pikorua_adflow.analytics import crm_analytics, lead_rules
        norm = crm_analytics._normalize([lead])
        if not norm:
            return "unclassified"
        return lead_rules.classify(norm[0])
    except Exception:
        return "unclassified"


def fire_pending_lead_events(crm_leads: list[dict]) -> dict:
    """
    Scan CRM leads, classify each, and fire the matching CAPI event once:
      good           → QualifiedLead
      bad / broker   → DisqualifiedLead
      unclassified   → skipped (no signal either way)

    Called during the daily autooptimiser pass. Safe to call repeatedly — already-sent
    leadgen_ids are tracked per event type in outputs/capi_sent.json and skipped.
    Each fire is written to the activity log. Returns a per-direction summary.
    """
    from pikorua_adflow.analytics import activity_log

    if not _creds():
        return {"qualified": {"fired": [], "already_sent": 0},
                "disqualified": {"fired": [], "already_sent": 0},
                "skipped_no_mapping": 0, "skipped_unclassified": 0,
                "skipped_no_creds": True}

    sent = _load_sent()
    results = {
        "qualified": {"fired": [], "already_sent": 0},
        "disqualified": {"fired": [], "already_sent": 0},
        "skipped_no_mapping": 0,
        "skipped_unclassified": 0,
        "skipped_no_creds": False,
    }

    for lead in crm_leads:
        category = _classify_lead(lead)
        if category == "good":
            bucket, sender, event_kind = "qualified", send_qualified_lead_event, "capi_qualified"
        elif category in ("bad", "broker"):
            bucket, sender, event_kind = "disqualified", send_disqualified_lead_event, "capi_disqualified"
        else:
            results["skipped_unclassified"] += 1
            continue

        phone = lead.get("Phone") or ""
        email = lead.get("Email") or ""
        leadgen_id = _lookup_leadgen_id(phone, email)
        if not leadgen_id:
            results["skipped_no_mapping"] += 1
            continue
        if leadgen_id in sent[bucket]:
            results[bucket]["already_sent"] += 1
            continue

        result = sender(leadgen_id, phone=phone, email=email)
        ok = bool(result.get("ok"))
        if ok:
            sent[bucket].add(leadgen_id)
        phone_tail = phone[-4:] if len(phone) >= 4 else ""
        results[bucket]["fired"].append({
            "leadgen_id": leadgen_id, "phone_tail": phone_tail,
            "ok": ok, "error": result.get("error"),
        })
        label = "Good lead" if bucket == "qualified" else "Bad lead"
        verb = "sent to Meta" if bucket == "qualified" else "flagged to Meta"
        activity_log.log_event(
            event_kind,
            f"{label} {verb} (CAPI): •••{phone_tail}" if phone_tail
            else f"{label} {verb} (CAPI)",
            detail=result.get("error") or "",
            status="ok" if ok else "error",
            meta={"leadgen_id": leadgen_id, "category": category},
        )

    if any(f.get("ok") for f in results["qualified"]["fired"]) or \
       any(f.get("ok") for f in results["disqualified"]["fired"]):
        _save_sent(sent)

    return results


# Backward-compat alias — older callers import this name.
def fire_pending_qualified_leads(crm_leads: list[dict]) -> dict:
    """Deprecated: use fire_pending_lead_events (now sends good AND bad signals)."""
    return fire_pending_lead_events(crm_leads)
