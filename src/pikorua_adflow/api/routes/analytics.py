"""CRM analytics + strategic-insights routes (data only; pages live in pages.py)."""

from __future__ import annotations

from fastapi import APIRouter

from ..services import crm_service

router = APIRouter()


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
