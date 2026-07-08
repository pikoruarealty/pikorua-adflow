"""CRM analytics + strategic-insights routes (data only; pages live in pages.py)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import crm_service

router = APIRouter()


# ── Categorisation rules editor ───────────────────────────────────────────────
def _category_counts(rules: list[dict]) -> dict:
    """Classify every current CRM lead under `rules` so the editor can show impact."""
    from pikorua_adflow.analytics import crm_analytics as ca
    from pikorua_adflow.analytics import lead_rules as lr
    leads, _src = ca.get_leads()
    counts = {"good": 0, "bad": 0, "broker": 0, "unclassified": 0, "total": 0}
    for norm in ca._normalize(leads):
        counts[lr.classify(norm, rules)] += 1
        counts["total"] += 1
    return counts


def _observed_field_values(max_per_field: int = 40) -> dict:
    """Distinct observed values per categorisation field, to populate the editor's
    value dropdowns (like the screenshot). Best-effort; {} if the CRM is unavailable."""
    from pikorua_adflow.analytics import crm_analytics as ca
    from pikorua_adflow.analytics import lead_rules as lr
    try:
        leads, _src = ca.get_leads()
        norm = ca._normalize(leads)
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    for field in lr.FIELD_LABELS:
        seen: dict[str, str] = {}
        for row in norm:
            val = str(row.get(field, "") or "").strip()
            if val and val.lower() not in seen:
                seen[val.lower()] = val
            if len(seen) >= max_per_field:
                break
        out[field] = sorted(seen.values(), key=str.lower)
    return out


@router.get("/categorization-rules")
def categorization_rules_get():
    """Current categorisation rules + editor metadata + observed values + live counts."""
    from pikorua_adflow.analytics import lead_rules as lr
    rules = lr.load_rules()
    return {
        "rules": rules,
        "using_defaults": lr.is_using_defaults(),
        "default_rules": [dict(r) for r in lr.DEFAULT_RULES],
        "metadata": lr.editor_metadata(),
        "field_values": _observed_field_values(),
        "counts": _category_counts(rules),
    }


class CategorizationRulesReq(BaseModel):
    rules: list


@router.post("/categorization-rules")
def categorization_rules_save(req: CategorizationRulesReq):
    """Validate + persist rules, then bust the CRM caches so they take effect.
    Returns the cleaned rules + the new live category counts. 400 on invalid input
    (nothing is persisted unless it validates)."""
    from pikorua_adflow.analytics import lead_rules as lr
    try:
        clean = lr.save_rules(req.rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # New rules change quality scoring — refresh the derived caches.
    try:
        crm_service.delete_insights_cache()
        crm_service.crm_report(force=True)
    except Exception:
        pass
    return {"ok": True, "rules": clean, "counts": _category_counts(clean)}


@router.post("/categorization-rules/reset")
def categorization_rules_reset():
    """Delete the persisted rules file so the built-in DEFAULT_RULES apply again."""
    from pikorua_adflow.analytics import lead_rules as lr
    try:
        lr._RULES_PATH.unlink(missing_ok=True)
        crm_service.delete_insights_cache()
        crm_service.crm_report(force=True)
    except Exception:
        pass
    return {"ok": True, "rules": [dict(r) for r in lr.DEFAULT_RULES],
            "counts": _category_counts([dict(r) for r in lr.DEFAULT_RULES])}


@router.get("/crm-strategic-insights")
def crm_strategic_insights(force: bool = False, run_id: str = ""):
    """5–8 visionary CRM insights from Claude via OpenRouter, cached 4h to disk.

    Pass run_id to get insights scoped to a specific campaign's brief.
    """
    from ..state import RUNS
    brief = None
    if run_id and run_id in RUNS:
        brief = RUNS[run_id].get("brief")
    return crm_service.strategic_insights(force=force, run_id=run_id,
                                           campaign_brief=brief)


@router.get("/crm-analytics/summary")
def crm_analytics_summary():
    return crm_service.crm_report()


@router.get("/crm-analytics/refresh")
def crm_analytics_refresh():
    """Re-fetch Supabase, bust the cache + insights, return fresh report."""
    crm_service.delete_insights_cache()
    return crm_service.crm_report(force=True)


@router.get("/crm-analytics/geography")
def crm_analytics_geography():
    from pikorua_adflow.analytics import crm_analytics
    leads, _ = crm_analytics.get_leads()
    return crm_analytics.geographic_distribution(leads)


@router.get("/crm-analytics/budget-segments")
def crm_analytics_budget():
    from pikorua_adflow.analytics import crm_analytics
    leads, _ = crm_analytics.get_leads()
    return crm_analytics.budget_segments(leads)


@router.get("/crm-analytics/professions")
def crm_analytics_professions():
    from pikorua_adflow.analytics import crm_analytics
    leads, _ = crm_analytics.get_leads()
    return crm_analytics.profession_industry_breakdown(leads)


@router.get("/crm-analytics/lead-quality")
def crm_analytics_lead_quality():
    from pikorua_adflow.analytics import crm_analytics
    leads, _ = crm_analytics.get_leads()
    return crm_analytics.lead_quality_funnel(leads)


@router.get("/crm-analytics/attribution")
def crm_analytics_attribution():
    from pikorua_adflow.analytics import crm_analytics
    leads, _ = crm_analytics.get_leads()
    return crm_analytics.campaign_source_attribution(leads)


@router.get("/crm-analytics/project/{name}")
def crm_analytics_project(name: str):
    from pikorua_adflow.analytics import crm_analytics
    leads, _ = crm_analytics.get_leads()
    return crm_analytics.project_analytics(leads, name)


@router.get("/crm-analytics/top-profiles")
def crm_analytics_top_profiles():
    from pikorua_adflow.analytics import crm_analytics
    leads, _ = crm_analytics.get_leads()
    return crm_analytics.top_converting_profiles(leads)
