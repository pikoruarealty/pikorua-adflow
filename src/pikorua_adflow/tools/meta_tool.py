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
    adset_id: str = "",
) -> dict[str, Any]:
    """
    Create ad objects for one variant under a Meta OUTCOME_LEADS campaign.
    Steps: upload image → (campaign if no campaign_id) → (adset if no adset_id)
           → creative → ad (all PAUSED).
    Pass campaign_id to reuse an existing campaign (multi-variant single-campaign flow).
    Pass adset_id to inject into an existing ad set — skips both campaign and adset creation.

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
                "campaign": campaign_id or (f"(existing) {campaign_name}" if adset_id else f"(would create) {campaign_name}"),
                "adset": adset_id or {
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
                } if not adset_id else adset_id,
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
    # campaign_id / adset_id passed in → caller-owned; don't roll back here.
    _shared_campaign = bool(campaign_id) or bool(adset_id)
    created: dict[str, str | None] = {"campaign": None, "adset": None,
                                      "creative": None, "ad": None}
    dropped_locations: list[str] = []
    try:
        # Step 2 — create campaign (skipped when adset_id or campaign_id provided)
        if adset_id:
            # Inject into an existing ad set — we own nothing at campaign/adset level.
            created["campaign"] = campaign_id or "(existing)"
            created["adset"] = adset_id
        elif campaign_id:
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

        # Step 3 — create ad set (skipped when adset_id was passed in)
        if not adset_id:
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
                "destination_type": "ON_AD",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                "daily_budget": daily_budget_inr * 100,
                "promoted_object": {"page_id": page_id},
                "targeting": adset_targeting,
                "status": "PAUSED",
            }
            if _regional_cats:
                adset_payload["regional_regulated_categories"] = _regional_cats
            if end_time:
                adset_payload["end_time"] = end_time

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
                if (subcode == 3858550 or "singapore_universal" in err_msg) and not _compliance_retried:
                    cats = adset_payload.get("regional_regulated_categories", [])
                    if "SINGAPORE_UNIVERSAL" not in cats:
                        adset_payload["regional_regulated_categories"] = cats + ["SINGAPORE_UNIVERSAL"]
                    _compliance_retried = True
                    continue
                countries = adset_targeting.get("geo_locations", {}).get("countries", [])
                iso = _regulated_country_to_drop(adset, countries)
                if not iso:
                    err_detail = json.dumps(err)
                    raise RuntimeError(f"POST act_{ad_account_id}/adsets failed: {err_detail}")
                countries.remove(iso)
                dropped_locations.append(iso)
                if countries:
                    adset_targeting["geo_locations"]["countries"] = countries
                else:
                    adset_targeting["geo_locations"].pop("countries", None)
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


def fetch_insights_by_region(campaign_id: str, token: str,
                             date_preset: str = "last_7d") -> list[dict]:
    """Spend + leads breakdown by region (city/state) for a campaign.
    Uses Graph API insights with breakdowns=['region'] so each row carries
    the region_name, spend, impressions, and lead-action count.
    Returns [{region_name, spend_inr, impressions, leads}]. [] on any failure.

    Used by geo_intelligence to show '₹X wasted on [City] → 0 quality leads'
    on trim cards, making human geo decisions concrete rather than abstract.
    """
    try:
        data = _get(
            f"{campaign_id}/insights", token,
            {
                "fields": "spend,impressions,actions",
                "date_preset": date_preset,
                "breakdowns": "region",
                "level": "campaign",
                "limit": "500",
            },
        )
        out: list[dict] = []
        for row in data.get("data", []):
            leads = 0
            for act in (row.get("actions") or []):
                if act.get("action_type") in ("lead", "onsite_conversion.lead_grouped"):
                    leads += int(float(act.get("value", 0)))
            out.append({
                "region_name": (row.get("region") or "").strip(),
                "spend_inr": round(float(row.get("spend") or 0), 2),
                "impressions": int(float(row.get("impressions") or 0)),
                "leads": leads,
            })
        return out
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


def _sanitize_targeting_for_write(spec: dict) -> dict:
    """Strip fields Meta returns in GET responses but rejects in PATCH requests.

    Sending these back causes errors like 'invalid broad categories' or generic
    validation failures even when the targeting itself is valid.
    """
    if not spec:
        return spec
    out = dict(spec)

    # age_range is a computed field (min–max pair). Meta returns it for display but
    # does not accept it as an input — use age_min / age_max instead.
    out.pop("age_range", None)

    # brand_safety_content_filter_levels is readable at the adset level but the
    # writable path is the campaign object. Sending it on a targeting PATCH is rejected.
    out.pop("brand_safety_content_filter_levels", None)

    # targeting_automation.individual_setting is a read-only decomposition Meta
    # returns to show which dimensions Advantage+ has taken over. Writing it back
    # causes a validation error; only advantage_audience is writable.
    ta = out.get("targeting_automation")
    if isinstance(ta, dict):
        out["targeting_automation"] = {"advantage_audience": ta.get("advantage_audience", 0)}

    # Geo location entries include server-side metadata keys (primary_city_id,
    # region_id, country, latitude, longitude) that Meta returns for display but
    # rejects when sent back in a write call.
    geo = out.get("geo_locations")
    if isinstance(geo, dict):
        geo = dict(geo)
        for geo_type in ("cities", "zips", "regions"):
            entries = geo.get(geo_type)
            if entries:
                geo[geo_type] = [{k: v for k, v in e.items()
                                  if k not in ("primary_city_id", "region_id",
                                               "country", "latitude", "longitude")}
                                 for e in entries]
        # places entries keep radius + distance_unit but drop the metadata
        places = geo.get("places")
        if places:
            geo["places"] = [{k: v for k, v in p.items()
                              if k not in ("primary_city_id", "region_id",
                                           "country", "latitude", "longitude")}
                             for p in places]
        out["geo_locations"] = geo

    return out


def update_adset_targeting(adset_id: str, targeting_spec: dict, token: str) -> bool:
    """PATCH the ad set's targeting. Retries once with SINGAPORE_UNIVERSAL if compliance error."""
    targeting_spec = _sanitize_targeting_for_write(targeting_spec)
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


def create_ad_in_adset(adset_id: str, ad_account_id: str,
                       object_story_spec: dict, ad_name: str,
                       token: str) -> dict:
    """Create a fresh ad under an existing ad set, inheriting its targeting and
    budget. Used by the A/B safe-swap refresh flow (B2): the challenger runs
    alongside the control until one clearly wins, then the loser is paused.

    Returns {ad_id, creative_id}. Raises on any API failure (caller handles).
    """
    acct = ad_account_id.replace("act_", "")
    creative = _post(
        f"act_{acct}/adcreatives",
        {"name": f"{ad_name} — challenger creative",
         "object_story_spec": object_story_spec},
        token,
    )
    creative_id = creative["id"]
    ad = _post(
        f"act_{acct}/ads",
        {"name": ad_name, "adset_id": adset_id,
         "creative": {"creative_id": creative_id}, "status": "ACTIVE"},
        token,
    )
    return {"ad_id": ad["id"], "creative_id": creative_id}


# ---- Autopilot: account-wide reads + geo edits ---------------------------- #

def fetch_active_campaigns(ad_account_id: str, token: str) -> list[dict]:
    """All ACTIVE campaigns on the account: [{id, name, daily_budget, objective}].
    daily_budget is in paise (Meta's unit). Never raises — [] on failure."""
    acct = ad_account_id.replace("act_", "")
    try:
        data = _get(
            f"act_{acct}/campaigns", token,
            {"effective_status": json.dumps(["ACTIVE"]),
             "fields": "id,name,daily_budget,lifetime_budget,objective,effective_status",
             "limit": "100"},
        )
        return data.get("data", [])
    except Exception:
        return []


def fetch_campaign_adsets(campaign_id: str, token: str) -> list[dict]:
    """Ad sets under a campaign with their live targeting + budget + status.
    Returns [] on failure. daily_budget is paise."""
    try:
        data = _get(
            f"{campaign_id}/adsets", token,
            {"fields": ("id,name,status,effective_status,daily_budget,"
                        "optimization_goal,targeting"),
             "limit": "100"},
        )
        return data.get("data", [])
    except Exception:
        return []


def fetch_ads_with_age(campaign_id: str, token: str) -> list[dict]:
    """Ads under a campaign with creation time (for creative-staleness checks).
    Returns [{id, name, status, created_time}]. [] on failure."""
    try:
        data = _get(
            f"{campaign_id}/ads", token,
            {"fields": "id,name,status,effective_status,created_time", "limit": "100"},
        )
        return data.get("data", [])
    except Exception:
        return []


def add_geo_countries(adset_id: str, iso_codes: list[str], token: str) -> bool:
    """Union extra countries (e.g. NRI geo) into an ad set's existing geo_locations.
    Reads the live targeting, merges, and PATCHes. Raises on hard failure."""
    live = _get(adset_id, token, {"fields": "targeting"}).get("targeting", {}) or {}
    geo = dict(live.get("geo_locations", {}) or {})
    geo["countries"] = list(dict.fromkeys((geo.get("countries", []) or []) + list(iso_codes)))
    new_targeting = dict(live)
    new_targeting["geo_locations"] = geo
    return update_adset_targeting(adset_id, new_targeting, token)


def add_geo_city(adset_id: str, city_name: str, token: str, *,
                 country: str = "IN", radius_km: int = 25) -> bool:
    """Resolve a city by name (via the Meta targeting-search taxonomy) and union it
    into the ad set's geo_locations.cities. Used by autopilot's geo-opportunity 'add'
    decision. Raises if the city can't be resolved or the PATCH fails."""
    from pikorua_adflow.tools import meta_targeting as _mt
    cache = _mt._load_cache()
    city = _mt._best_city(city_name, token, country, cache)
    _mt._save_cache(cache)
    if not city:
        raise RuntimeError(f"Could not resolve city '{city_name}' on Meta.")
    live = _get(adset_id, token, {"fields": "targeting"}).get("targeting", {}) or {}
    geo = dict(live.get("geo_locations", {}) or {})
    cities = list(geo.get("cities") or [])
    if any(str(c.get("key")) == str(city["key"]) for c in cities):
        return True  # already targeted
    cities.append({"key": str(city["key"]), "radius": int(radius_km),
                   "distance_unit": "kilometer"})
    geo["cities"] = cities
    new_targeting = dict(live)
    new_targeting["geo_locations"] = geo
    return update_adset_targeting(adset_id, new_targeting, token)


def remove_geo_locations(adset_id: str, token: str, *, keep_city_keys: list[str] | None = None,
                         keep_countries: list[str] | None = None) -> dict:
    """
    Strip wrong-market geo from an ad set, keeping only the allowed city keys /
    countries. Returns {removed_cities: [...], removed_countries: [...], applied: bool}.
    Used by autopilot rung 1 (e.g. an Ahmedabad property whose ad set still carries
    Mumbai/Gurgaon pincodes). Raises on hard PATCH failure.
    """
    keep_city_keys = set(str(k) for k in (keep_city_keys or []))
    keep_countries = set(str(c).upper() for c in (keep_countries or []))
    live = _get(adset_id, token, {"fields": "targeting"}).get("targeting", {}) or {}
    geo = dict(live.get("geo_locations", {}) or {})

    removed_cities, removed_countries = [], []
    if "cities" in geo:
        kept = [c for c in geo["cities"] if str(c.get("key")) in keep_city_keys]
        removed_cities = [c.get("name") or c.get("key") for c in geo["cities"]
                          if str(c.get("key")) not in keep_city_keys]
        if kept:
            geo["cities"] = kept
        else:
            geo.pop("cities", None)
    if "countries" in geo and keep_countries:
        kept_c = [c for c in geo["countries"] if str(c).upper() in keep_countries]
        removed_countries = [c for c in geo["countries"] if str(c).upper() not in keep_countries]
        if kept_c:
            geo["countries"] = kept_c
        else:
            geo.pop("countries", None)

    if not (removed_cities or removed_countries):
        return {"removed_cities": [], "removed_countries": [], "applied": False}
    new_targeting = dict(live)
    new_targeting["geo_locations"] = geo
    update_adset_targeting(adset_id, new_targeting, token)
    return {"removed_cities": removed_cities, "removed_countries": removed_countries, "applied": True}


def add_custom_audiences(adset_id: str, token: str, *, include_ids: list[str] | None = None,
                         exclude_ids: list[str] | None = None) -> bool:
    """Union custom-audience include/exclude ids into an ad set's live targeting.
    Used by autopilot rung 2 (exclusion) + rung 3 (CRM lookalike). Raises on failure."""
    live = _get(adset_id, token, {"fields": "targeting"}).get("targeting", {}) or {}
    new_targeting = dict(live)
    if include_ids:
        cur = {a.get("id") for a in (new_targeting.get("custom_audiences") or [])}
        new_targeting["custom_audiences"] = (
            (new_targeting.get("custom_audiences") or [])
            + [{"id": str(i)} for i in include_ids if str(i) not in cur]
        )
    if exclude_ids:
        cur = {a.get("id") for a in (new_targeting.get("excluded_custom_audiences") or [])}
        new_targeting["excluded_custom_audiences"] = (
            (new_targeting.get("excluded_custom_audiences") or [])
            + [{"id": str(i)} for i in exclude_ids if str(i) not in cur]
        )
    return update_adset_targeting(adset_id, new_targeting, token)


# ---- Phase 2: Meta Recommendations ---------------------------------------- #

def fetch_recommendations(
    ad_account_id: str,
    token: str,
    ad_set_ids: list[str] | None = None,
) -> list[dict]:
    """Fetch Meta Ads Manager recommendations for the ad account, optionally
    filtered to ad sets belonging to a specific campaign. Never raises."""
    try:
        data = _get(
            f"act_{ad_account_id}/recommendations",
            token,
            {
                "fields": (
                    "recommendation_type,title,message,importance,confidence,"
                    "custom_audiences_specs,ad_set_ids"
                ),
                "limit": "50",
            },
        )
        recs: list[dict] = data.get("data", [])
        if ad_set_ids:
            ids = {str(a) for a in ad_set_ids}
            recs = [
                r for r in recs
                if not r.get("ad_set_ids")
                or ids.intersection(str(x) for x in r.get("ad_set_ids", []))
            ]
        return recs
    except Exception:
        return []


def apply_recommendation(recommendation_id: str, token: str) -> tuple[bool, dict]:
    """Apply a Meta recommendation by its ID. Returns (ok, response_dict)."""
    return _do_post(f"{recommendation_id}/apply", {}, token)


def toggle_advantage_audience(adset_id: str, enable: bool, token: str) -> bool:
    """Toggle Advantage+ Audience on a single ad set (targeting_automation field)."""
    ok, _ = _do_patch(
        adset_id,
        {"targeting_automation": {"advantage_audience": 1 if enable else 0}},
        token,
    )
    return ok


def toggle_cbo(campaign_id: str, enable: bool, token: str) -> bool:
    """Toggle Campaign Budget Optimisation on the campaign."""
    ok, _ = _do_patch(
        campaign_id,
        {"is_adset_budget_sharing_enabled": enable},
        token,
    )
    return ok


def update_adset_schedule(adset_id: str, peak_days: list[int], token: str) -> bool:
    """
    Enable Meta day-parting on this ad set, running full-weight only on peak_days.
    peak_days: Meta day numbers (0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat).
    Meta will only spend the daily budget on the selected days; it prorates automatically.
    """
    ok, _ = _do_patch(adset_id, {
        "pacing_type": ["day_parting"],
        "adset_schedule": [{
            "start_minute": 0,
            "end_minute": 1440,
            "days": sorted(peak_days),
            "timezone_type": "ADVERTISER",
        }],
    }, token)
    return ok


def remove_adset_schedule(adset_id: str, token: str) -> bool:
    """Remove day-parting and restore standard even-pacing."""
    ok, _ = _do_patch(adset_id, {"pacing_type": ["standard"], "adset_schedule": []}, token)
    return ok
