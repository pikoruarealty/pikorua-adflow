"""
Lookalike audience freshness check.

The CRM-backed lookalike audience is only as good as the data it was seeded from.
As new leads arrive, the seed becomes stale. This module tracks whether a refresh
is warranted and gives the human a clear, concrete reason to act.
"""

from __future__ import annotations

from datetime import datetime, timezone

REFRESH_DAYS = 30       # refresh if the seed is older than this
GROWTH_THRESHOLD = 0.15  # refresh if CRM has grown by ≥15% since last build
MIN_NEW_LEADS = 100      # minimum absolute new leads to trigger a growth refresh


def check_staleness(registry_rows: list[dict], current_crm_count: int) -> dict:
    """
    Examine the audience registry and current CRM size to decide whether the lookalike
    seed needs refreshing.

    Returns a dict with:
      stale      – bool, True if a refresh is recommended
      reason     – plain-English sentence explaining why (empty if not stale)
      age_days   – int, days since last build (0 if unknown)
      seed_size  – int, CRM size at last build (0 if unknown)
      growth_pct – float, percentage growth since last build (0 if unknown)
      action_detail – one-line for the card body
    """
    # Find the most recently built lookalike entry
    lookalike_entry = None
    for row in (registry_rows or []):
        if row.get("role") == "lookalike" and row.get("built_at"):
            if lookalike_entry is None or row["built_at"] > lookalike_entry["built_at"]:
                lookalike_entry = row

    # If no lookalike exists at all, don't surface a refresh — user needs to build first
    if not lookalike_entry:
        return {"stale": False, "age_days": 0, "seed_size": 0, "growth_pct": 0.0, "reason": ""}

    seed_size = int(lookalike_entry.get("seed_size") or 0)
    built_at_raw = lookalike_entry.get("built_at", "")

    age_days = 0
    if built_at_raw:
        try:
            built_at = datetime.fromisoformat(built_at_raw.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - built_at).days
        except Exception:
            age_days = 0

    growth_pct = 0.0
    new_leads = 0
    if seed_size > 0 and current_crm_count > seed_size:
        growth_pct = (current_crm_count - seed_size) / seed_size
        new_leads = current_crm_count - seed_size

    stale = False
    reason = ""
    action_detail = ""

    if age_days >= REFRESH_DAYS and new_leads >= 10:
        stale = True
        reason = (
            f"Your buyer audience was last rebuilt {age_days} days ago"
            + (f" — {new_leads:,} new contacts have joined the CRM since then." if new_leads else ".")
        )
        action_detail = (
            f"Refreshing re-seeds Meta with the latest {current_crm_count:,} leads so it finds "
            "buyers who look like your most recent enquiries, not last month's."
        )
    elif growth_pct >= GROWTH_THRESHOLD and new_leads >= MIN_NEW_LEADS:
        stale = True
        reason = (
            f"The CRM has grown by {int(growth_pct * 100)}% ({new_leads:,} new contacts) "
            f"since the lookalike was built. Meta is still targeting buyers who resemble "
            f"your older, smaller lead pool."
        )
        action_detail = (
            f"Refreshing updates the seed to all {current_crm_count:,} contacts, "
            "giving Meta a richer picture of who your buyers are."
        )

    return {
        "stale": stale,
        "reason": reason,
        "action_detail": action_detail,
        "age_days": age_days,
        "seed_size": seed_size,
        "current_crm_count": current_crm_count,
        "growth_pct": round(growth_pct * 100, 1),
    }
