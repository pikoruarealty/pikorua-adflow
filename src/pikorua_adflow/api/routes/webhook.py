"""
Meta webhook receiver — lead gen form submissions.

GET  /meta-lead-webhook  — verification handshake (responds with hub.challenge)
POST /meta-lead-webhook  — lead notification → fetch field_data → store leadgen_id

Flow:
  1. Meta POSTs a notification containing the leadgen_id (no PII — field values
     are not included in the webhook body).
  2. We immediately fetch field_data from the Graph API using the ads token to
     retrieve the lead's phone / email.
  3. We call meta_capi.store_leadgen_id() which writes a hashed mapping to
     outputs/leadgen_mapping.json so the daily CAPI pass can look it up later.

Env vars required:
  META_WEBHOOK_VERIFY_TOKEN  — the string you entered in Meta → App → Webhooks
                               (defaults to "pikorua_webhook_2026" if unset)
  META_ACCESS_TOKEN          — existing ads system-user token (reused for field fetch)
"""

from __future__ import annotations

import json
import os
import urllib.request

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

router = APIRouter()

_GRAPH = "https://graph.facebook.com/v21.0"


def _fetch_lead_field_data(leadgen_id: str, token: str) -> dict[str, str]:
    """
    Fetch phone/email from a leadgen_id via the Graph API.
    Returns {"phone": ..., "email": ..., "name": ...} (any may be blank).
    """
    url = f"{_GRAPH}/{leadgen_id}?fields=field_data&access_token={token}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        result: dict[str, str] = {}
        for field in data.get("field_data", []):
            name = (field.get("name") or "").lower()
            values = field.get("values") or []
            val = str(values[0]) if values else ""
            if "phone" in name:
                result["phone"] = val
            elif "email" in name:
                result["email"] = val
            elif "name" in name and "phone" not in name and "email" not in name:
                result["name"] = val
        return result
    except Exception:
        return {}


@router.get("/meta-lead-webhook", response_class=PlainTextResponse)
def webhook_verify(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> str:
    """Meta webhook verification handshake — responds with hub.challenge."""
    expected = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "pikorua_webhook_2026")
    if hub_mode == "subscribe" and hub_verify_token == expected:
        return hub_challenge
    raise HTTPException(status_code=403, detail="Webhook verification failed.")


@router.post("/meta-lead-webhook")
async def webhook_receive(request: Request) -> dict:
    """
    Receive a lead gen notification from Meta.
    Fetches field_data (phone/email) and stores the leadgen_id → hashed mapping.
    Always returns 200 OK — Meta retries on non-200 which causes duplicate noise.
    """
    from pikorua_adflow.analytics import meta_capi as capi

    token = os.getenv("META_ACCESS_TOKEN", "")

    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "stored": 0, "error": "Invalid JSON body."}

    stored = 0
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            value = change.get("value") or {}
            leadgen_id = str(value.get("leadgen_id", ""))
            created_time = int(value.get("created_time") or 0) or None
            if not leadgen_id:
                continue

            fields = _fetch_lead_field_data(leadgen_id, token) if token else {}
            capi.store_leadgen_id(
                leadgen_id=leadgen_id,
                phone=fields.get("phone", ""),
                email=fields.get("email", ""),
                created_time=created_time,
            )
            stored += 1

    return {"ok": True, "stored": stored}
