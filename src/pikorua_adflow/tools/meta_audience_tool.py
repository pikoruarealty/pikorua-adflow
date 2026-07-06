"""
Meta Custom Audience + Lookalike upload tool.

Reads all rows from the CRM export, hashes contact data (Meta requirement:
SHA-256, lowercase, trimmed), and uploads to Meta Ads API.

Good leads  (warm buying/client status)  → Custom Audience seed → Lookalike
Bad leads   (cold/not interested)        → Custom Audience for ad EXCLUSION
Broker leads                             → Separate Custom Audience for EXCLUSION
                                           (different use case: wastes budget + distorts
                                           Meta learning even more than a plain rejection)

Gated: requires META_ACCESS_TOKEN in environment.
Phase 3 only — not called during dry-run pipeline.
"""
import hashlib
import os
import pathlib

_CRM_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "project_context" / "crm_export.csv"
_CATEGORIES_YAML = pathlib.Path(__file__).parent.parent.parent.parent / "project_context" / "lead_categories.yaml"

# ── Default status sets ───────────────────────────────────────────────────────
# These are used when project_context/lead_categories.yaml is absent.
# Edit the YAML file instead of these — the YAML always takes precedence.

_DEFAULT_GOOD_BUYING  = [
    "exploring", "hot", "warm", "interested", "active", "qualified",
    "follow up", "postponed", "still searching",
]
_DEFAULT_BAD_BUYING   = [
    "not_ready", "not ready", "cold", "not interested", "no interest",
    "dead", "lost", "spam", "duplicate", "invalid", "not_interested",
]
_DEFAULT_GOOD_CLIENT  = ["warm", "interested"]
_DEFAULT_BAD_CLIENT   = ["not interested", "cold", "lost", "low budget"]
_DEFAULT_BROKER       = ["broker"]
_DEFAULT_SITE_VISIT   = ["visited", "completed", "confirmed", "visit date confirmed"]


def _load_categories() -> dict:
    """Load lead category rules from YAML; fall back to defaults if absent."""
    if _CATEGORIES_YAML.exists():
        try:
            import yaml
            data = yaml.safe_load(_CATEGORIES_YAML.read_text(encoding="utf-8")) or {}
            return {
                "good_buying":   frozenset(v.lower() for v in data.get("good_buying_status", _DEFAULT_GOOD_BUYING)),
                "bad_buying":    frozenset(v.lower() for v in data.get("bad_buying_status",  _DEFAULT_BAD_BUYING)),
                "good_client":   frozenset(v.lower() for v in data.get("good_client_status", _DEFAULT_GOOD_CLIENT)),
                "bad_client":    frozenset(v.lower() for v in data.get("bad_client_status",  _DEFAULT_BAD_CLIENT)),
                "broker":        frozenset(v.lower() for v in data.get("broker_client_status", _DEFAULT_BROKER)),
                "site_visit":    frozenset(v.lower() for v in data.get("site_visit_confirmed", _DEFAULT_SITE_VISIT)),
            }
        except Exception as exc:
            print(f"[meta_audience_tool] lead_categories.yaml load failed ({exc}) — using defaults.")
    return {
        "good_buying":  frozenset(v.lower() for v in _DEFAULT_GOOD_BUYING),
        "bad_buying":   frozenset(v.lower() for v in _DEFAULT_BAD_BUYING),
        "good_client":  frozenset(v.lower() for v in _DEFAULT_GOOD_CLIENT),
        "bad_client":   frozenset(v.lower() for v in _DEFAULT_BAD_CLIENT),
        "broker":       frozenset(v.lower() for v in _DEFAULT_BROKER),
        "site_visit":   frozenset(v.lower() for v in _DEFAULT_SITE_VISIT),
    }


# Loaded once at import; restart server or re-run script to pick up YAML changes.
_CATS = _load_categories()

_GOOD_BUYING_STATUSES  = _CATS["good_buying"]
_BAD_BUYING_STATUSES   = _CATS["bad_buying"]
_GOOD_CLIENT_STATUSES  = _CATS["good_client"]
_BAD_CLIENT_STATUSES   = _CATS["bad_client"]
_BROKER_CLIENT_STATUSES = _CATS["broker"]
_SITE_VISIT_CONFIRMED  = _CATS["site_visit"]

# Budget ceiling below which an unclassified lead is excluded from the lookalike seed.
_LOOKALIKE_BUDGET_MIN_CR = 3.5

# Combined convenience sets
_GOOD_STATUSES = _GOOD_BUYING_STATUSES | _GOOD_CLIENT_STATUSES
_BAD_STATUSES  = _BAD_BUYING_STATUSES  | _BAD_CLIENT_STATUSES


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _get_raw(row: dict, *names: str) -> str:
    """
    Fetch a field from a CRM row, tolerant of space / underscore / case variants.

    "Buying Status", "buying_status", and "BuyingStatus" all resolve to the same
    value.  Tries exact key first (fast path for Supabase rows), then falls back
    to a normalised comparison.
    """
    norm_map: dict[str, str] = {
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


def _categorise(buying_status: str, client_status: str = "") -> str:
    """
    Return 'good', 'bad', 'broker', or 'unclassified'.

    client_status (meta_leads.status) is checked first — it represents an
    explicit human judgement and therefore takes priority.  buying_status
    (lead_crm_details.buying_status) is the fallback.  Substring match so
    'follow up (warm)' → good, 'cold lead' → bad.
    """
    cs = client_status.strip().lower()
    bs = buying_status.strip().lower()

    # Broker is a special category — checked before good/bad
    for bk in _BROKER_CLIENT_STATUSES:
        if bk in cs:
            return "broker"

    # client_status takes priority when set
    if cs:
        for g in _GOOD_CLIENT_STATUSES:
            if g in cs:
                return "good"
        for b in _BAD_CLIENT_STATUSES:
            if b in cs:
                return "bad"

    # Fall through to buying_status
    if bs:
        for g in _GOOD_BUYING_STATUSES:
            if g in bs:
                return "good"
        for b in _BAD_BUYING_STATUSES:
            if b in bs:
                return "bad"

    return "unclassified"


def _load_leads(crm_path: pathlib.Path) -> list[dict]:
    """
    Load all CRM rows with at least a phone or email.

    Returns list of dicts with:
      phone, email       — SHA-256 hashed
      category           — 'good' | 'bad' | 'broker' | 'unclassified'
      is_site_visitor    — True when SiteVisitStatus confirms a physical/confirmed visit
                           (site visitors are always 'good', overriding other statuses)
      is_low_budget      — True when budget is below _LOOKALIKE_BUDGET_MIN_CR AND the
                           lead has no explicit positive client_status (unclassified only)
      is_spoken_to       — True when CallStatus == 'spoken' AND no negative client_status
                           (secondary quality tier — better signal than untouched leads)
      city               — normalised city name (after crm_normalise, used for geo split)
    """
    from pikorua_adflow.utils import crm_source
    from pikorua_adflow.analytics.crm_analytics import parse_budget_cr

    rows, _source = crm_source.fetch_rows(crm_path)
    if not rows:
        return []

    leads = []
    for row in rows:
        entry: dict = {}

        raw_phone = _get_raw(row, "Phone", "phone", "Mobile", "mobile", "Contact", "contact")
        raw_email = _get_raw(row, "Email", "email", "Email Address", "email_address")
        if raw_phone:
            entry["phone"] = _sha256(raw_phone)
        if raw_email:
            entry["email"] = _sha256(raw_email)
        if not entry:
            continue

        # Prefer "Client Status" over plain "Status" — it's the sales-team disposition
        # column and takes precedence over the raw inbound status.
        raw_client  = _get_raw(row, "Client Status", "ClientStatus", "client_status") \
                      or _get_raw(row, "Status", "status")
        raw_buying  = _get_raw(row, "Buying Status", "BuyingStatus", "buying_status")
        raw_svisit  = _get_raw(row, "Site Visit Status", "SiteVisitStatus", "site_visit_status").lower()
        raw_budget  = _get_raw(row, "Budget", "budget", "Budget Range", "budget_range")
        raw_call    = _get_raw(row, "Call Status", "CallStatus", "call_status").lower()
        raw_city    = _get_raw(row, "City", "city", "Current City", "CurrentCity", "current_city")

        # Site visitors override all other signals — they confirmed intent physically.
        is_site_visitor = any(v in raw_svisit for v in _SITE_VISIT_CONFIRMED)

        category = _categorise(raw_buying, raw_client)
        if is_site_visitor and category not in ("bad", "broker"):
            category = "good"

        # Budget signal: low-budget unclassified leads are kept in ad-delivery pool
        # but excluded from the tight lookalike seed (Item 9).
        # Only applies to unclassified leads — explicit warm/interested overrides.
        is_low_budget = False
        if category == "unclassified" and raw_budget:
            cr = parse_budget_cr(raw_budget)
            if cr is not None and cr < _LOOKALIKE_BUDGET_MIN_CR:
                is_low_budget = True

        # Spoken-to leads: reachable + not rejected → better than untouched unclassified.
        # Tag only; no CA behavior change (Item 10).
        is_spoken_to = (
            raw_call == "spoken"
            and category not in ("bad", "broker")
        )

        entry["buying_status"]   = raw_buying
        entry["client_status"]   = raw_client
        entry["category"]        = category
        entry["is_site_visitor"] = is_site_visitor
        entry["is_low_budget"]   = is_low_budget
        entry["is_spoken_to"]    = is_spoken_to
        entry["city"]            = raw_city
        leads.append(entry)

    return leads


def _registry_lookup(name: str) -> str | None:
    """Check the local audience registry (outputs/meta_audiences_registry.json)
    for a name match before hitting the Meta API — saves a round-trip and catches
    entries a paginated live search might miss. Returns None on any failure."""
    try:
        import json
        registry_path = pathlib.Path(__file__).parent.parent.parent.parent / "outputs" / "meta_audiences_registry.json"
        if not registry_path.exists():
            return None
        rows = json.loads(registry_path.read_text(encoding="utf-8"))
        for row in rows:
            if (row.get("name") or "").strip().lower() == name.strip().lower():
                return str(row["id"])
    except Exception:
        pass
    return None


def find_existing_audience(base_url: str, headers: dict, name: str, requests_lib) -> str | None:
    """
    Return the id of an existing Custom Audience with this exact name, or None.

    Dedup guard: the tool used to create a brand-new Custom Audience on every CRM
    upload, leaving the account with 4× duplicate "Good Leads" / "Bad Leads" CAs and
    spreading the CRM data thin. Callers reuse the existing CA (and just refresh its
    members) instead of minting another. Never raises — returns None on any failure.

    Checks the local registry first (fast, no API round-trip); falls back to a
    live Meta lookup since the registry can drift from Meta's actual state (e.g.
    an audience deleted directly in Ads Manager). If the registry has an entry
    the live lookup doesn't confirm, that's logged as a possible deletion rather
    than silently trusted.
    """
    registry_id = _registry_lookup(name)

    try:
        resp = requests_lib.get(
            f"{base_url}/customaudiences",
            params={"fields": "id,name", "limit": 200},
            headers=headers, timeout=30,
        )
        if not resp.ok:
            return registry_id
        live_id = None
        for row in resp.json().get("data", []):
            if (row.get("name") or "").strip().lower() == name.strip().lower():
                live_id = str(row["id"])
                break
        if registry_id and live_id and registry_id != live_id:
            print(f"[meta_audience_tool] Warning: registry id {registry_id} for "
                  f"{name!r} does not match live Meta id {live_id} — using live id.")
        if registry_id and not live_id:
            print(f"[meta_audience_tool] Warning: registry has {name!r} as {registry_id} "
                  f"but Meta's live list doesn't — it may have been deleted on Meta's side.")
        return live_id or registry_id
    except Exception:
        return registry_id


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

    lal_name = f"PIKORUA Lookalike — All Contacts — {','.join(target_countries)}"
    existing_lal = find_existing_audience(base_url, headers, lal_name, requests)
    if existing_lal:
        return {
            "custom_audience_id": ca_id,
            "lookalike_audience_id": existing_lal,
            "leads_uploaded": count,
            "target_countries": target_countries,
        }

    lal_payload = {
        "name": lal_name,
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
    Split CRM leads into three categories, then:
    - Good leads     → Custom Audience + Lookalike (warm buyers + unclassified)
    - Bad leads      → Custom Audience for EXCLUSION (cold/not interested/lost)
    - Broker leads   → Separate Custom Audience for EXCLUSION (brokers distort
                       Meta's learning and waste budget — kept separate from bad
                       leads because their ad behaviour is fundamentally different)
    - Unclassified   → included in good leads pool (conservative: don't penalise unknowns)

    Returns dict with counts and audience IDs for all groups.
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

    # ── Split pools ─────────────────────────────────────────────────────────────
    # Unclassified = no buying status, no client status, no site visit.
    # Not warm enough to seed a lookalike; not bad enough to exclude.
    # Kept in their own CA so they can still see ads via the unclassified audience
    # without polluting the quality signal of the good seed.
    good_leads        = [l for l in all_leads if l["category"] == "good"]
    unclassified_leads = [l for l in all_leads if l["category"] == "unclassified"]
    bad_leads         = [l for l in all_leads if l["category"] == "bad"]
    broker_leads      = [l for l in all_leads if l["category"] == "broker"]

    # High-intent seed (lookalike): only explicitly good leads — no unclassified.
    # The lookalike trains Meta on who to find MORE of — we want clean signal only.
    high_intent_seed = [l for l in good_leads if not l.get("is_low_budget")]

    # Remote buyers: non-Ahmedabad leads from good pool (investors, relocators, NRI).
    remote_leads = [
        l for l in good_leads
        if l.get("city") and l["city"].lower() not in ("ahmedabad", "")
    ]

    base_url = f"https://graph.facebook.com/v20.0/act_{ad_account_id}"
    auth_headers = {"Authorization": f"Bearer {token}"}

    site_visitor_count = sum(1 for l in all_leads if l.get("is_site_visitor"))
    spoken_to_count    = sum(1 for l in all_leads if l.get("is_spoken_to"))
    low_budget_count   = sum(1 for l in all_leads if l.get("is_low_budget"))

    result: dict = {
        "total_leads":              len(all_leads),
        "good_leads_count":         len(good_leads),
        "unclassified_leads_count": len(unclassified_leads),
        "high_intent_seed_count":   len(high_intent_seed),
        "bad_leads_count":          len(bad_leads),
        "broker_leads_count":       len(broker_leads),
        "remote_leads_count":       len(remote_leads),
        "site_visitor_count":       site_visitor_count,
        "spoken_to_count":          spoken_to_count,
        "low_budget_excluded_from_seed": low_budget_count,
        "target_countries":         target_countries,
    }

    # ── Good leads: broad CA for ad delivery ────────────────────────────────
    if len(good_leads) >= 100:
        good_ca_id, good_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Good Leads (Ad Delivery)",
            description="Explicitly warm/interested leads — include on ad sets for targeted delivery",
            leads=good_leads,
            requests_lib=requests,
        )
        if err:
            result["good_leads_error"] = err
        else:
            result["good_leads_audience_id"] = good_ca_id
            result["good_leads_uploaded"] = good_count
    else:
        result["good_leads_error"] = (
            f"Only {len(good_leads)} good leads — Meta requires ≥100 for Custom Audience."
        )

    # ── Unclassified leads: separate CA (no buying/client signal) ───────────
    # Not warm enough to include in the good seed, not bad enough to exclude.
    # Kept separate so they can still receive ads without diluting lookalike quality.
    if len(unclassified_leads) >= 100:
        unc_ca_id, unc_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Unclassified Leads",
            description="Leads with no buying/client status — neutral pool, neither warm nor cold",
            leads=unclassified_leads,
            requests_lib=requests,
        )
        if err:
            result["unclassified_leads_error"] = err
        else:
            result["unclassified_leads_audience_id"] = unc_ca_id
            result["unclassified_leads_uploaded"] = unc_count
    elif unclassified_leads:
        result["unclassified_leads_note"] = (
            f"Only {len(unclassified_leads)} unclassified leads — below Meta's 100-record minimum."
        )

    # ── High-intent seed: tight CA + Lookalike ──────────────────────────────
    # This is what feeds the Lookalike — only leads with the right intent AND budget
    # profile.  Keeps the lookalike signal clean.
    seed_ca_id = None
    if len(high_intent_seed) >= 100:
        seed_ca_id, seed_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — High Intent Seed (Lookalike)",
            description="Warm + site-visitor + budget-matched leads — tight seed for Meta Lookalike",
            leads=high_intent_seed,
            requests_lib=requests,
        )
        if err:
            result["seed_error"] = err
        else:
            result["seed_audience_id"] = seed_ca_id
            result["seed_uploaded"] = seed_count
            lal_name = f"PIKORUA Lookalike — High Intent — {','.join(target_countries)}"
            existing_lal = find_existing_audience(base_url, auth_headers, lal_name, requests)
            if existing_lal:
                result["good_leads_lookalike_id"] = existing_lal
                result["good_lookalike_name"] = lal_name
            else:
                lal_payload = {
                    "name": lal_name,
                    "subtype": "LOOKALIKE",
                    "origin_audience_id": seed_ca_id,
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
                if lal_resp.ok:
                    result["good_leads_lookalike_id"] = lal_resp.json().get("id")
                    result["good_lookalike_name"] = lal_name
                else:
                    try:
                        lal_err = lal_resp.json().get("error", {}).get("message", lal_resp.text)
                    except Exception:
                        lal_err = lal_resp.text
                    result["lookalike_error"] = f"Lookalike creation failed: {lal_err}"
    elif high_intent_seed:
        result["seed_note"] = (
            f"Only {len(high_intent_seed)} high-intent seed leads — below Meta's 100-record minimum. "
            f"({site_visitor_count} site visitors + warm leads are in this pool.)"
        )

    # ── Remote buyers: separate Lookalike for non-Ahmedabad investors ────────
    # Kept separate so the Ahmedabad lookalike isn't diluted by remote-buyer behaviour.
    # Apply to NRI-adjacent campaigns when geo-expanding beyond Gujarat.
    if len(remote_leads) >= 100:
        remote_ca_id, remote_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Remote Buyers",
            description="Non-Ahmedabad warm leads — investors, relocators, out-of-city buyers",
            leads=remote_leads,
            requests_lib=requests,
        )
        if err:
            result["remote_leads_error"] = err
        else:
            result["remote_leads_audience_id"] = remote_ca_id
            result["remote_leads_uploaded"] = remote_count
            # Lookalike targets remote cities specifically.
            remote_lal_name = f"PIKORUA Lookalike — Remote Buyers — {','.join(target_countries)}"
            existing_remote_lal = find_existing_audience(base_url, auth_headers, remote_lal_name, requests)
            if existing_remote_lal:
                result["remote_lookalike_id"] = existing_remote_lal
            else:
                remote_lal_payload = {
                    "name": remote_lal_name,
                    "subtype": "LOOKALIKE",
                    "origin_audience_id": remote_ca_id,
                    "lookalike_spec": {
                        "type": "similarity",
                        "country": target_countries[0],
                        "ratio": 0.01,
                    },
                }
                remote_lal_resp = requests.post(
                    f"{base_url}/customaudiences", json=remote_lal_payload,
                    headers=auth_headers, timeout=30,
                )
                if remote_lal_resp.ok:
                    result["remote_lookalike_id"] = remote_lal_resp.json().get("id")
                else:
                    result["remote_lookalike_note"] = "Remote lookalike creation failed — CA created, create lookalike manually."
    elif remote_leads:
        result["remote_leads_note"] = (
            f"{len(remote_leads)} remote buyers in CRM — below Meta's 100 minimum. "
            "Will be created once more non-Ahmedabad leads are tagged."
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
            f"Only {len(bad_leads)} bad leads — below Meta's 100-record minimum. "
            "No exclusion audience created yet."
        )
    else:
        result["bad_leads_note"] = "No bad leads identified."

    # ── Broker leads: Separate Exclusion Custom Audience ────────────────────
    # Brokers are not merged with bad leads — their ad behaviour differs.
    # Bad leads are real buyers who said no; brokers are professionals who research
    # but never buy, distorting Meta's learning signal differently.
    if len(broker_leads) >= 100:
        broker_ca_id, broker_count, err = _create_custom_audience(
            base_url, auth_headers,
            name="PIKORUA CRM — Brokers (Exclusion)",
            description="Broker contacts — exclude to prevent budget waste and protect Meta learning quality",
            leads=broker_leads,
            requests_lib=requests,
        )
        if err:
            result["broker_leads_error"] = err
        else:
            result["broker_leads_audience_id"] = broker_ca_id
            result["broker_leads_uploaded"] = broker_count
            result["broker_custom_audience_name"] = "PIKORUA CRM — Brokers (Exclusion)"
    elif broker_leads:
        result["broker_leads_note"] = (
            f"Only {len(broker_leads)} broker leads — below Meta's 100-record minimum. "
            "Will be created once count reaches 100."
        )
    else:
        result["broker_leads_note"] = "No broker leads identified in CRM."

    return result
