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


# ISO-2 → display name for the countries we may target (used to identify which
# location a regional-compliance error refers to, and to word the warning).
_ISO_NAMES: dict[str, str] = {
    "IN": "India", "SG": "Singapore", "AE": "United Arab Emirates",
    "US": "United States", "GB": "United Kingdom", "CA": "Canada",
    "QA": "Qatar", "BH": "Bahrain", "KW": "Kuwait", "OM": "Oman",
    "DE": "Germany", "FR": "France", "NL": "Netherlands", "CH": "Switzerland",
    "AU": "Australia", "NZ": "New Zealand", "HK": "Hong Kong", "JP": "Japan",
    "KE": "Kenya", "ZA": "South Africa", "TW": "Taiwan",
}


def _do_post(path: str, payload: dict, token: str) -> tuple[bool, dict]:
    """POST to the Graph API. Returns (ok, data). On HTTP error, data is the parsed
    error JSON (so callers can inspect subcodes) rather than raising."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_BASE}/{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return False, json.loads(body)
        except ValueError:
            return False, {"error": {"message": body, "code": e.code}}


def _post(path: str, payload: dict, token: str) -> dict:
    ok, data = _do_post(path, payload, token)
    if ok:
        return data
    raise RuntimeError(f"POST {path} failed: {json.dumps(data.get('error', data))}")


def _get(path: str, token: str, params: dict | None = None) -> dict:
    """GET from the Graph API. Raises on HTTP error."""
    import urllib.parse as _uparse
    qs = _uparse.urlencode({"access_token": token, **(params or {})})
    req = urllib.request.Request(f"{_BASE}/{path}?{qs}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"GET {path} failed [{e.code}]: {body}") from e


def _do_patch(path: str, payload: dict, token: str) -> tuple[bool, dict]:
    """POST with an update payload (Graph API has no true PATCH — updates are POSTs
    to the object id). Named _do_patch to make optimisation intent explicit.
    Returns (ok, data); on HTTP error data is the parsed error JSON."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_BASE}/{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return False, json.loads(body)
        except ValueError:
            return False, {"error": {"message": body, "code": e.code}}


def _patch(path: str, payload: dict, token: str) -> dict:
    ok, data = _do_patch(path, payload, token)
    if ok:
        return data
    raise RuntimeError(f"UPDATE {path} failed: {json.dumps(data.get('error', data))}")


def _fetch_instagram_actor_id(page_id: str, token: str) -> str:
    """Return the Instagram Business Account ID linked to this Facebook Page.
    This is the value Meta requires as instagram_actor_id on adcreatives.
    Tries connected_instagram_account first (Business pages), then falls back to
    instagram_accounts (personal/legacy). Returns "" if nothing is linked."""
    try:
        data = _get(page_id, token, {"fields": "connected_instagram_account"})
        cia = data.get("connected_instagram_account", {})
        if cia.get("id"):
            return str(cia["id"])
    except Exception:
        pass
    try:
        data = _get(f"{page_id}/instagram_accounts", token, {"fields": "id"})
        accounts = data.get("data", [])
        if accounts:
            return str(accounts[0]["id"])
    except Exception:
        pass
    return ""


def _regulated_country_to_drop(error: dict, targeted_countries: list[str]) -> str | None:
    """
    Some locations (e.g. Singapore) require a regional regulated-categories
    declaration that we must NOT auto-make on the advertiser's behalf. When Meta
    rejects the ad set for that reason (subcode 3858550), return the ISO-2 code of
    the targeted country to drop so the rest of the campaign can still deploy.
    Returns None if the error is something else we can't safely auto-fix.
    """
    err = error.get("error", error)
    msg = f"{err.get('error_user_title', '')} {err.get('error_user_msg', '')} {err.get('message', '')}".lower()
    is_compliance = (
        err.get("error_subcode") == 3858550
        or "regional regulated categories" in msg
        or "universal ads declaration" in msg
    )
    if not is_compliance:
        return None
    # Identify which of OUR targeted countries the error names.
    for iso in targeted_countries:
        name = _ISO_NAMES.get(iso, "").lower()
        if name and name in msg:
            return iso
    return None


def _delete(object_id: str, token: str) -> bool:
    """Best-effort DELETE of a Graph API object. Never raises — used for cleanup."""
    req = urllib.request.Request(
        f"{_BASE}/{object_id}",
        headers={"Authorization": f"Bearer {token}"},
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception:
        return False


def _rollback(created: dict, token: str, skip_campaign: bool = False) -> list[str]:
    """
    Delete partially-created objects after a failed deploy so the ad account isn't
    left with orphaned half-built campaigns. Deletes in reverse dependency order
    (ad → creative → ad set → campaign). Best-effort: returns the IDs removed.
    skip_campaign=True when the campaign is shared across variants — the caller
    is responsible for cleaning it up after the full loop finishes.
    """
    removed = []
    keys = ("ad", "creative", "adset") if skip_campaign else ("ad", "creative", "adset", "campaign")
    for key in keys:
        oid = created.get(key)
        if oid and _delete(oid, token):
            removed.append(oid)
    return removed


def create_campaign(*, campaign_name: str, token: str, ad_account_id: str) -> str:
    """Create one PAUSED OUTCOME_LEADS campaign and return its ID."""
    campaign = _post(
        f"act_{ad_account_id}/campaigns",
        {
            "name": campaign_name,
            "objective": "OUTCOME_LEADS",
            "special_ad_categories": [],
            "is_adset_budget_sharing_enabled": False,
            "status": "PAUSED",
        },
        token,
    )
    return campaign["id"]


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
    targeting_spec: dict[str, Any] | None = None,
    audience_label: str = "",
    instagram_actor_id: str = "",
    end_time: str = "",
    campaign_id: str = "",
) -> dict[str, Any]:
    """
    Create ad objects for one variant under a Meta OUTCOME_LEADS campaign.
    Steps: upload image → (campaign if no campaign_id) → ad set → creative → ad (all PAUSED).
    Pass campaign_id to reuse an existing campaign (multi-variant single-campaign flow).

    DRY_RUN=true: returns a preview dict without calling the API.
    On failure: raises RuntimeError with API error details.
    """
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    token = os.getenv("META_ACCESS_TOKEN", "")
    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    page_id = os.getenv("META_PAGE_ID", "")
    lead_form_id = os.getenv("META_LEAD_FORM_ID", "")
    # Instagram actor lets the ad run on Instagram placements under the brand's
    # handle. Without it Meta shows "Please add Instagram account" and the ad is
    # Facebook-only. Param overrides env; env is the always-on default.
    # If neither is set, auto-discover from the linked page via Graph API.
    instagram_actor_id = instagram_actor_id or os.getenv("META_INSTAGRAM_ACTOR_ID", "")
    if not instagram_actor_id and page_id and token and not dry_run:
        instagram_actor_id = _fetch_instagram_actor_id(page_id, token)

    if dry_run:
        return {
            "dry_run": True,
            "variant": variant,
            "would_create": {
                "image": str(image_path) if image_path else None,
                "campaign": campaign_id or f"(would create) {campaign_name}",
                "adset": {
                    "optimization_goal": "LEAD_GENERATION",
                    "billing_event": "IMPRESSIONS",
                    "daily_budget_inr": daily_budget_inr,
                    "daily_budget_paise": daily_budget_inr * 100,
                    "geo": audience_label or "India (country-level)",
                    "age_min": (targeting_spec or {}).get("age_min", age_min),
                    "age_max": (targeting_spec or {}).get("age_max", age_max),
                    "targeting": targeting_spec,
                    "instagram_actor_id": instagram_actor_id or "(none)",
                    "end_time": end_time or "(no end date)",
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

    # Steps 2–5 create real objects. If any step fails partway, roll back what was
    # created so the ad account isn't left with half-built campaigns. Cleanup is
    # best-effort; the original error is always re-raised.
    # campaign_id passed in → shared campaign owned by caller; don't roll it back here.
    _shared_campaign = bool(campaign_id)
    created: dict[str, str | None] = {"campaign": None, "adset": None,
                                      "creative": None, "ad": None}
    dropped_locations: list[str] = []
    try:
        # Step 2 — create campaign (skipped when caller already created a shared one)
        if campaign_id:
            created["campaign"] = campaign_id
        else:
            campaign = _post(
                f"act_{ad_account_id}/campaigns",
                {
                    "name": campaign_name,
                    "objective": "OUTCOME_LEADS",
                    "special_ad_categories": [],  # HOUSING restriction is US/EU only — not applicable in India
                    # Budget lives at the ad-set level; Meta needs this flag explicit when absent.
                    "is_adset_budget_sharing_enabled": False,
                    "status": "PAUSED",
                },
                token,
            )
            created["campaign"] = campaign["id"]

        # Step 3 — create ad set
        # Targeting: a resolved spec (city geo + interests/behaviours from the audience
        # panel) is used when provided. Without one we fall back to country-level India.
        if targeting_spec:
            adset_targeting = dict(targeting_spec)
            adset_targeting.setdefault("targeting_automation", {"advantage_audience": 0})
        else:
            adset_targeting = {
                "geo_locations": {"countries": ["IN"]},
                "age_min": age_min,
                "age_max": age_max,
                "targeting_automation": {"advantage_audience": 0},
            }

        # Proactively add compliance declarations required by the targeted locations.
        # Singapore requires SINGAPORE_UNIVERSAL for ALL ad types — not a special-category
        # declaration, just a mandatory compliance checkbox. Safe to auto-add.
        _geo = adset_targeting.get("geo_locations", {})
        _sg_via_country = "SG" in _geo.get("countries", [])
        _sg_via_city = any(c.get("country") == "SG" for c in _geo.get("cities", []))
        _regional_cats: list[str] = []
        if _sg_via_country or _sg_via_city:
            _regional_cats.append("SINGAPORE_UNIVERSAL")

        adset_payload: dict[str, Any] = {
            "name": f"{campaign_name} — V{variant} — Ad Set",
            "campaign_id": created["campaign"],
            "optimization_goal": "LEAD_GENERATION",
            "billing_event": "IMPRESSIONS",
            # Lead-form creatives need ON_AD (form opens in the ad) — subcode 1892040.
            "destination_type": "ON_AD",
            # Explicit auto-bid; without it Meta defaults to a capped strategy needing bid_amount.
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "daily_budget": daily_budget_inr * 100,  # Meta expects paise (1 INR = 100 paise)
            # LEAD_GENERATION ad sets must declare what's promoted — subcode 1885154.
            "promoted_object": {"page_id": page_id},
            "targeting": adset_targeting,
            "status": "PAUSED",
        }
        if _regional_cats:
            adset_payload["regional_regulated_categories"] = _regional_cats
        # Optional campaign end date (ISO 8601, e.g. "2026-07-31T23:59:00+0530").
        if end_time:
            adset_payload["end_time"] = end_time

        # Create the ad set. On first attempt the compliance list is already populated
        # for known locations (Singapore above). If Meta still returns a compliance
        # error (e.g. an unknown city triggered it), add SINGAPORE_UNIVERSAL and retry
        # once. For any other regional issue, drop the offending country and retry.
        _compliance_retried = False
        while True:
            ok, adset = _do_post(f"act_{ad_account_id}/adsets", adset_payload, token)
            if ok:
                break
            err = adset.get("error", adset)
            err_msg = (
                f"{err.get('error_user_title', '')} "
                f"{err.get('error_user_msg', '')} "
                f"{err.get('message', '')}".lower()
            )
            subcode = err.get("error_subcode")
            # Singapore universal ads declaration — add it and retry once
            if (subcode == 3858550 or "singapore_universal" in err_msg) and not _compliance_retried:
                cats = adset_payload.get("regional_regulated_categories", [])
                if "SINGAPORE_UNIVERSAL" not in cats:
                    adset_payload["regional_regulated_categories"] = cats + ["SINGAPORE_UNIVERSAL"]
                _compliance_retried = True
                continue
            # Other regional compliance issues — drop the offending country and retry
            countries = adset_targeting.get("geo_locations", {}).get("countries", [])
            iso = _regulated_country_to_drop(adset, countries)
            if not iso:
                err_detail = json.dumps(err)
                raise RuntimeError(
                    f"POST act_{ad_account_id}/adsets failed: {err_detail}"
                )
            countries.remove(iso)
            dropped_locations.append(iso)
            if countries:
                adset_targeting["geo_locations"]["countries"] = countries
            else:
                adset_targeting["geo_locations"].pop("countries", None)
            # If stripping the country left no geo at all, we can't deploy this variant.
            if not adset_targeting["geo_locations"]:
                raise RuntimeError(
                    "All targeted locations require regulatory declarations that must be "
                    "made in Meta Ads Manager. Nothing left to target after removing: "
                    + ", ".join(_ISO_NAMES.get(c, c) for c in dropped_locations)
                )
        created["adset"] = adset["id"]

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

        object_story_spec: dict[str, Any] = {
            "page_id": page_id,
            "link_data": link_data,
        }
        # Instagram actor makes the ad eligible for Instagram placements under the
        # brand's handle (clears "Please add Instagram account").
        if instagram_actor_id:
            object_story_spec["instagram_actor_id"] = instagram_actor_id

        creative = _post(
            f"act_{ad_account_id}/adcreatives",
            {
                "name": f"{campaign_name} — V{variant} — Creative",
                "object_story_spec": object_story_spec,
            },
            token,
        )
        created["creative"] = creative["id"]

        # Step 5 — create ad
        ad = _post(
            f"act_{ad_account_id}/ads",
            {
                "name": f"{campaign_name} — V{variant} — Ad",
                "adset_id": created["adset"],
                "creative": {"creative_id": created["creative"]},
                "status": "PAUSED",
            },
            token,
        )
        created["ad"] = ad["id"]
    except Exception:
        # Remove any objects created before the failure so Ads Manager stays clean.
        _rollback(created, token, skip_campaign=_shared_campaign)
        raise

    return {
        "variant": variant,
        "campaign_id": created["campaign"],
        "adset_id": created["adset"],
        "creative_id": created["creative"],
        "ad_id": created["ad"],
        "image_hash": image_hash,
        "dropped_locations": [_ISO_NAMES.get(c, c) for c in dropped_locations],
        "dry_run": False,
    }


# =========================================================================== #
# Post-deploy intelligence: previews, signals, performance, optimisation
# =========================================================================== #

# Placement formats we render as live previews in the Deploy tab.
PREVIEW_FORMATS = [
    "MOBILE_FEED_STANDARD",
    "INSTAGRAM_STANDARD",
    "INSTAGRAM_STORY",
    "DESKTOP_FEED_STANDARD",
]


def fetch_ad_previews(ad_id: str, token: str,
                      formats: list[str] | None = None) -> dict[str, str]:
    """Return {format: iframe_html} for an ad. Previews are cosmetic, so any
    per-format failure yields "" rather than raising."""
    out: dict[str, str] = {}
    for fmt in (formats or PREVIEW_FORMATS):
        try:
            data = _get(f"{ad_id}/previews", token, {"ad_format": fmt})
            body = data.get("data", [])
            out[fmt] = body[0].get("body", "") if body else ""
        except Exception:
            out[fmt] = ""
    return out


def fetch_reach_estimate(ad_account_id: str, targeting_spec: dict, token: str) -> dict:
    """Audience size estimate for a targeting spec. Returns {} on failure."""
    acct = ad_account_id.replace("act_", "")
    try:
        data = _get(
            f"act_{acct}/reachestimate", token,
            {"targeting_spec": json.dumps(targeting_spec)},
        )
        d = data.get("data", data) or {}
        # reachestimate returns users_lower_bound/users_upper_bound; older/other
        # endpoints use estimate_mau* or a flat `users`. Cover them all.
        mau = (d.get("estimate_mau")
               or d.get("estimate_mau_upper_bound")
               or d.get("users_upper_bound")
               or d.get("users_lower_bound")
               or d.get("users") or 0)
        dau = (d.get("estimate_dau")
               or d.get("estimate_dau_upper_bound") or 0)
        return {"estimate_mau": int(mau or 0), "estimate_dau": int(dau or 0),
                "estimate_ready": bool(d.get("estimate_ready", True))}
    except Exception:
        return {}


def fetch_delivery_estimate(adset_id: str, token: str,
                            optimization_goal: str = "LEAD_GENERATION") -> dict:
    """Daily delivery estimate for an ad set. Returns {} on failure."""
    try:
        data = _get(
            f"{adset_id}/delivery_estimate", token,
            {"optimization_goal": optimization_goal},
        )
        body = data.get("data", [])
        return body[0] if body else {}
    except Exception:
        return {}


def fetch_insights(object_id: str, token: str, date_preset: str = "last_7d") -> list[dict]:
    """Performance insights for a campaign/adset/ad. Returns [] on failure."""
    fields = ("impressions,reach,frequency,spend,clicks,ctr,cpc,cpm,"
              "actions,cost_per_action_type")
    try:
        data = _get(f"{object_id}/insights", token,
                    {"fields": fields, "date_preset": date_preset, "level": "ad"})
        return data.get("data", [])
    except Exception:
        return []


def fetch_relevance_diagnostics(ad_ids: list[str], token: str) -> dict[str, dict]:
    """Per-ad relevance rankings (quality/engagement/conversion). {} entries on failure."""
    out: dict[str, dict] = {}
    fields = "quality_ranking,engagement_rate_ranking,conversion_rate_ranking"
    for ad_id in ad_ids:
        try:
            data = _get(f"{ad_id}/insights", token,
                        {"fields": fields, "date_preset": "last_7d"})
            body = data.get("data", [])
            out[ad_id] = body[0] if body else {}
        except Exception:
            out[ad_id] = {}
    return out


# ---- Optimisation actions (each returns bool / dict; raise on hard failure) -- #
def pause_variant(ad_id: str, token: str) -> bool:
    _patch(ad_id, {"status": "PAUSED"}, token)
    return True


def resume_variant(ad_id: str, token: str) -> bool:
    _patch(ad_id, {"status": "ACTIVE"}, token)
    return True


def update_adset_budget(adset_id: str, daily_budget_inr: int, token: str) -> bool:
    # Meta stores budget in paise (1 INR = 100 paise).
    _patch(adset_id, {"daily_budget": int(daily_budget_inr) * 100}, token)
    return True


def update_adset_targeting(adset_id: str, targeting_spec: dict, token: str) -> bool:
    """PATCH the ad set's targeting. Retries once with SINGAPORE_UNIVERSAL if compliance error."""
    payload: dict = {"targeting": targeting_spec}
    ok, data = _do_patch(adset_id, payload, token)
    if ok:
        return True
    err = data.get("error", data)
    err_msg = (
        f"{err.get('error_user_title', '')} "
        f"{err.get('error_user_msg', '')} "
        f"{err.get('message', '')}".lower()
    )
    subcode = err.get("error_subcode")
    is_sg = (
        subcode == 3858550
        or "singapore_universal" in err_msg
        or "universal ads declaration" in err_msg
        or "regional regulated categories" in err_msg
    )
    if is_sg:
        payload2 = {"targeting": targeting_spec,
                    "regional_regulated_categories": ["SINGAPORE_UNIVERSAL"]}
        ok2, data2 = _do_patch(adset_id, payload2, token)
        if ok2:
            return True
        raise RuntimeError(f"UPDATE {adset_id} failed: {json.dumps(data2.get('error', data2))}")
    raise RuntimeError(f"UPDATE {adset_id} failed: {json.dumps(err)}")


def swap_ad_creative(ad_id: str, ad_account_id: str,
                     object_story_spec: dict, token: str) -> dict:
    """Create a fresh creative and point the ad at it. Returns {creative_id, ad_id}."""
    acct = ad_account_id.replace("act_", "")
    creative = _post(
        f"act_{acct}/adcreatives",
        {"name": f"Optimised creative for ad {ad_id}",
         "object_story_spec": object_story_spec},
        token,
    )
    new_creative_id = creative["id"]
    _patch(ad_id, {"creative": {"creative_id": new_creative_id}}, token)
    return {"creative_id": new_creative_id, "ad_id": ad_id}
