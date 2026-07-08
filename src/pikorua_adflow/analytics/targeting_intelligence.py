"""
Smart-retarget analyzer — suggest ADD / REMOVE targeting changes, never a wholesale wipe.

The existing `/retarget-campaign` (meta_tool.retarget_campaign_adsets) REPLACES an
ad set's whole flexible_spec with a clientele profile. That's too blunt when the user
mostly likes their targeting and just wants it sharpened. This module instead looks at
the *current* audience and proposes:

  - ADD:  segments proven by the CRM (top-converting industries → Meta interests) plus
          profile-baseline work positions that aren't present yet.
  - REMOVE: segments known to hurt luxury-real-estate CPL (IT/tech C-suite titles, the
          ₹967 CPL GODREJ benchmark) and exact duplicates.

Everything is a *suggestion* the user approves one by one; nothing is auto-applied, and
geo / custom audiences / exclusions are never touched (same guarantees as retarget).
Reuses meta_targeting's resolvers/constants and crm_analytics' converting-profile logic
so there is one source of truth for what a good luxury-RE audience looks like.
"""

from __future__ import annotations

from typing import Any

from ..tools import meta_targeting as _mt


def _ids(entries: list[dict]) -> set[str]:
    return {str(e.get("id")) for e in (entries or []) if e.get("id")}


def _suggestion(field: str, entry: dict, reason: str, source: str) -> dict:
    return {
        "field": field,
        "id": str(entry.get("id", "")),
        "name": entry.get("name", ""),
        "reason": reason,
        "source": source,  # "crm" | "profile" | "rule"
    }


def suggest_targeting_changes(
    current: dict,
    clientele_type: str = "",
    crm_leads: list[dict] | None = None,
    token: str = "",
) -> dict[str, Any]:
    """
    Compare a current audience against CRM evidence + the clientele profile and return
    add/remove suggestions.

    `current` is the audience dict (interests/behaviours/work_positions/industries lists
    of {id, name}). Returns:
        {"add": [suggestion, ...], "remove": [suggestion, ...],
         "kept": <count of current interest+behaviour+work_position entries>}
    Never raises — resolution failures just yield fewer suggestions.
    """
    current = current or {}
    cur_interest_ids = _ids(current.get("interests"))
    cur_behaviour_ids = _ids(current.get("behaviours"))
    cur_wp_ids = _ids(current.get("work_positions"))

    add: list[dict] = []
    remove: list[dict] = []

    # ── ADD 1: CRM-proven interests (top-converting industries → Meta interests) ──
    if crm_leads and token:
        try:
            from . import crm_analytics as _ca
            top_profiles = _ca.top_converting_profiles(crm_leads, top_n=5, min_count=2)
            crm_interests = _mt.interests_from_crm_profiles(top_profiles, token, limit=6)
            for it in crm_interests:
                if str(it.get("id")) not in cur_interest_ids:
                    add.append(_suggestion(
                        "interests", it,
                        "Buyers in this segment convert in your CRM — add the matching Meta interest.",
                        "crm",
                    ))
        except Exception:
            pass

    # ── ADD 2: profile-baseline work positions not present yet ──
    try:
        profile = _mt.clientele_profile(clientele_type)
        for wp in (profile.get("work_positions") or []):
            if str(wp.get("id")) not in cur_wp_ids and not any(
                s["field"] == "work_positions" and s["id"] == str(wp.get("id")) for s in add
            ):
                add.append(_suggestion(
                    "work_positions", wp,
                    "Proven job title for this buyer profile that isn't in your targeting yet.",
                    "profile",
                ))
    except Exception:
        pass

    # ── ADD 3: affluence-proxy behaviour (the writable stand-in for income Top 10%) ──
    proxy = _mt.AFFLUENCE_PROXY_BEHAVIOUR
    if str(proxy.get("id")) not in cur_behaviour_ids:
        add.append(_suggestion(
            "behaviours", proxy,
            "High-value-goods affluence signal present in every top-performing campaign.",
            "profile",
        ))

    # ── REMOVE 1: IT/tech C-suite titles (documented wrong for luxury RE) ──
    avoid_ids = _ids(_mt._WORK_POSITIONS_IT_CSUITE_AVOID)
    for wp in (current.get("work_positions") or []):
        if str(wp.get("id")) in avoid_ids:
            remove.append(_suggestion(
                "work_positions", wp,
                "IT/tech C-suite titles underperform for luxury real estate (₹967 CPL benchmark).",
                "rule",
            ))

    # ── REMOVE 2: exact duplicates within a list ──
    for field in ("interests", "behaviours", "work_positions", "industries"):
        seen: set[str] = set()
        for entry in (current.get(field) or []):
            eid = str(entry.get("id", ""))
            if eid and eid in seen and not any(
                r["field"] == field and r["id"] == eid for r in remove
            ):
                remove.append(_suggestion(
                    field, entry, "Duplicate — the same segment is listed twice.", "rule"
                ))
            seen.add(eid)

    kept = len(cur_interest_ids) + len(cur_behaviour_ids) + len(cur_wp_ids)
    return {"add": add, "remove": remove, "kept": kept}


def apply_suggestion(audience: dict, field: str, entry_id: str, name: str, op: str) -> dict:
    """Apply one add/remove suggestion to an audience dict in place and return it.

    `op` is "add" or "remove"; `field` is interests/behaviours/work_positions/industries.
    Only these flexible_spec fields are ever mutated — geo, custom audiences, exclusions,
    age and radius are left untouched.
    """
    if field not in ("interests", "behaviours", "work_positions", "industries"):
        raise ValueError(f"Cannot retarget field '{field}'.")
    lst = list(audience.get(field) or [])
    entry_id = str(entry_id)
    if op == "add":
        if not any(str(e.get("id")) == entry_id for e in lst):
            lst.append({"id": entry_id, "name": name})
    elif op == "remove":
        lst = [e for e in lst if str(e.get("id")) != entry_id]
    else:
        raise ValueError(f"Unknown op '{op}' (expected add/remove).")
    audience[field] = lst
    return audience
