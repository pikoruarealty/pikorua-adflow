"""
Activity routes — the client-facing automation timeline.

GET /activity-data?limit=&kind=  → the recent activity events (JSON), newest first.

The page itself is served by pages.py (GET /activity); this module owns the data
endpoint it fetches. Events are produced across the app via
analytics.activity_log.log_event().
"""

from __future__ import annotations

from fastapi import APIRouter

from pikorua_adflow.analytics import activity_log

router = APIRouter()

# Coarse buckets the page's filter chips map onto, so the client filters by concept
# ("Leads", "CAPI") rather than raw event kinds.
_GROUPS: dict[str, list[str]] = {
    "optimise": ["optimise_auto", "optimise_manual", "retarget"],
    "leads": ["webhook_lead", "crm_fetch"],
    "capi": ["capi_qualified", "capi_disqualified"],
    "scheduler": ["scheduler_run", "scheduler_error"],
}


@router.get("/activity-data")
def activity_data(limit: int = 200, group: str = ""):
    """Recent automation events. Optional ?group=optimise|leads|capi|scheduler filter."""
    limit = max(1, min(limit, 1000))
    kinds = _GROUPS.get(group) if group else None
    events = activity_log.read_events(limit=limit, kinds=kinds)
    return {"events": events, "count": len(events), "group": group or "all"}
