"""
Meta Ads deploy tool — Phase 3 (Task 3.3).

Creates a full OUTCOME_LEADS campaign (image upload → campaign → ad set →
creative → ad) using Meta Instant Form (Lead Gen form). No Pixel needed.
All objects created in PAUSED state so the team can review before going live.

DRY_RUN=true (default in .env): skips all API calls and returns a dict
showing exactly what would have been sent. Set DRY_RUN=false to deploy live.

Required .env keys:
  META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_PAGE_ID, META_LEAD_FORM_ID
"""
import json
import os
import pathlib
import urllib.error
import urllib.request
from typing import Any


_BASE = "https://graph.facebook.com/v20.0"


def _post(path: str, payload: dict, token: str) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_BASE}/{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"POST {path} failed [{e.code}]: {body}") from e


def _upload_image(ad_account_id: str, image_path: pathlib.Path, token: str) -> str:
    """Upload image via multipart POST to /adimages, return image_hash."""
    img_bytes = image_path.read_bytes()
    # Detect image type from magic bytes
    if img_bytes[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif img_bytes[:4] == b"\x89PNG":
        mime = "image/png"
    else:
        mime = "image/png"  # fallback

    boundary = "PikoruaAdFlowBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="filename"; filename="{image_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + img_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{_BASE}/act_{ad_account_id}/adimages",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode(errors="replace")
        raise RuntimeError(f"Image upload failed [{e.code}]: {body_err}") from e

    # Response shape: {"images": {"<filename>": {"hash": "...", ...}}}
    for _fname, info in result.get("images", {}).items():
        return info["hash"]
    raise RuntimeError(f"Image upload: no hash in response: {result}")


def deploy_ad(
    *,
    variant: int,
    headline: str,
    body: str,
    image_path: pathlib.Path | None = None,
    campaign_name: str,
    city: str = "India",
    age_min: int = 28,
    age_max: int = 65,
    landing_page_url: str = "https://pikorua.in/",
    daily_budget_inr: int = 1000,
    cta: str = "GET_QUOTE",
) -> dict[str, Any]:
    """
    Create a Meta OUTCOME_LEADS campaign for one ad variant.
    Steps: upload image → campaign → ad set → creative → ad (all PAUSED).

    DRY_RUN=true: returns a preview dict without calling the API.
    On failure: raises RuntimeError with API error details.
    """
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    token = os.getenv("META_ACCESS_TOKEN", "")
    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    page_id = os.getenv("META_PAGE_ID", "")
    lead_form_id = os.getenv("META_LEAD_FORM_ID", "")

    if dry_run:
        return {
            "dry_run": True,
            "variant": variant,
            "would_create": {
                "image": str(image_path) if image_path else None,
                "campaign": {
                    "name": f"{campaign_name} — V{variant}",
                    "objective": "OUTCOME_LEADS",
                    "special_ad_categories": [],
                    "status": "PAUSED",
                },
                "adset": {
                    "optimization_goal": "LEAD_GENERATION",
                    "billing_event": "IMPRESSIONS",
                    "daily_budget_inr": daily_budget_inr,
                    "daily_budget_paise": daily_budget_inr * 100,
                    "geo": "India (country-level)",
                    "age_min": age_min,
                    "age_max": age_max,
                },
                "creative": {
                    "headline": headline,
                    "body": body,
                    "cta": cta,
                    "lead_gen_form_id": lead_form_id or "(META_LEAD_FORM_ID not set)",
                    "thank_you_url": landing_page_url,
                },
                "ad": {"status": "PAUSED"},
            },
        }

    # Pre-flight checks
    missing = [k for k, v in [
        ("META_ACCESS_TOKEN", token),
        ("META_AD_ACCOUNT_ID", ad_account_id),
        ("META_PAGE_ID", page_id),
        ("META_LEAD_FORM_ID", lead_form_id),
    ] if not v]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    # Step 1 — upload image (optional — creative works without one)
    image_hash = None
    if image_path and image_path.exists():
        image_hash = _upload_image(ad_account_id, image_path, token)

    # Step 2 — create campaign
    campaign = _post(
        f"act_{ad_account_id}/campaigns",
        {
            "name": f"{campaign_name} — V{variant}",
            "objective": "OUTCOME_LEADS",
            "special_ad_categories": [],  # HOUSING restriction is US/EU only — not applicable in India
            # Budget lives at the ad-set level (daily_budget below), not the campaign.
            # Meta now requires this flag to be explicit when there's no campaign budget.
            "is_adset_budget_sharing_enabled": False,
            "status": "PAUSED",
        },
        token,
    )
    campaign_id = campaign["id"]

    # Step 3 — create ad set
    # Country-level targeting for India. City-level targeting requires Meta's
    # geo search API to resolve city keys — add in a future iteration.
    adset_targeting: dict[str, Any] = {
        "geo_locations": {"countries": ["IN"]},
        "age_min": age_min,
        "age_max": age_max,
        # Newer API versions require an explicit choice on Advantage Audience.
        # 0 = off (honour the exact targeting above); 1 = let Meta expand it.
        "targeting_automation": {"advantage_audience": 0},
    }
    adset = _post(
        f"act_{ad_account_id}/adsets",
        {
            "name": f"{campaign_name} — V{variant} — Ad Set",
            "campaign_id": campaign_id,
            "optimization_goal": "LEAD_GENERATION",
            "billing_event": "IMPRESSIONS",
            # Instant Form leads open inside the ad itself. A lead-form creative is only
            # valid with an ON_AD destination — without it the ad step fails (subcode 1892040).
            "destination_type": "ON_AD",
            # Automatic bidding — "highest volume" for the budget. Without an explicit
            # bid_strategy, Meta defaults to a capped strategy that requires a bid_amount.
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "daily_budget": daily_budget_inr * 100,  # Meta expects paise (1 INR = 100 paise)
            # For LEAD_GENERATION the ad set must declare what's being promoted — the Page
            # that owns the Lead Gen form. Without this, the ad step fails (subcode 1885154).
            "promoted_object": {"page_id": page_id},
            "targeting": adset_targeting,
            "status": "PAUSED",
        },
        token,
    )
    adset_id = adset["id"]

    # Step 4 — create ad creative
    # `link` is required by Meta even for Lead Gen creatives; the form opens on
    # click, and this URL is also the Thank-You-screen destination.
    link_data: dict[str, Any] = {
        "link": landing_page_url,
        "message": body,
        "name": headline,
        "call_to_action": {
            "type": cta,
            "value": {"lead_gen_form_id": lead_form_id},
        },
    }
    if image_hash:
        link_data["image_hash"] = image_hash

    creative = _post(
        f"act_{ad_account_id}/adcreatives",
        {
            "name": f"{campaign_name} — V{variant} — Creative",
            "object_story_spec": {
                "page_id": page_id,
                "link_data": link_data,
            },
        },
        token,
    )
    creative_id = creative["id"]

    # Step 5 — create ad
    ad = _post(
        f"act_{ad_account_id}/ads",
        {
            "name": f"{campaign_name} — V{variant} — Ad",
            "adset_id": adset_id,
            "creative": {"creative_id": creative_id},
            "status": "PAUSED",
        },
        token,
    )
    ad_id = ad["id"]

    return {
        "variant": variant,
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "creative_id": creative_id,
        "ad_id": ad_id,
        "image_hash": image_hash,
        "dry_run": False,
    }
