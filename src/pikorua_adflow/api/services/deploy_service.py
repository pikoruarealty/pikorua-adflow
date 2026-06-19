"""
Meta deploy helpers: identifying live ads, reading insights, and the targeting-
change heuristics used by the optimise endpoints.

NOTE ON CURRENCY: the Pikorua Meta ad account is INR-denominated, so the `spend`
and derived `cpl` returned by the Insights API are already in INR. They are NOT
multiplied by USD_TO_INR — doing so would 84× real values and break the ₹500/₹300
CPL thresholds the optimisation rules depend on. `config.USD_TO_INR` exists for any
genuinely USD-denominated source, but Meta insights here are not one.
"""

from __future__ import annotations


def real_meta_ads(run: dict) -> list[dict]:
    """Variants really published (have a live ad_id, not a dry-run preview)."""
    return [a for a in run.get("meta_ads", [])
            if not a.get("dry_run") and a.get("ad_id")]


def variant_lookup(run: dict) -> dict[int, dict]:
    return {a.get("variant"): a for a in real_meta_ads(run)}


def reach_status(mau: int) -> tuple[str, str]:
    """Map an audience size to a (label, colour) signal."""
    if not mau:
        return "Unknown", "muted"
    if mau < 100_000:
        return "Too narrow", "red"
    if mau <= 3_000_000:
        return "Good", "green"
    return "Broad", "amber"


def metrics_from_insight(row: dict) -> dict:
    """Pull headline metrics (and CPL) out of an insights row. Spend is INR already."""
    def f(key: str) -> float:
        try:
            return float(row.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    leads = 0.0
    for act in row.get("actions", []) or []:
        if act.get("action_type") in ("lead", "onsite_conversion.lead_grouped",
                                       "leadgen.other", "lead_grouped"):
            try:
                leads += float(act.get("value", 0) or 0)
            except (TypeError, ValueError):
                pass
    spend = f("spend")
    cpl = round(spend / leads, 1) if leads else None
    return {
        "impressions": int(f("impressions")), "reach": int(f("reach")),
        "frequency": round(f("frequency"), 2), "spend": round(spend, 1),
        "ctr": round(f("ctr"), 2), "leads": int(leads), "cpl": cpl,
    }


def spec_radius(spec: dict):
    cities = (spec.get("geo_locations", {}) or {}).get("cities", []) or []
    return cities[0].get("radius") if cities else None


def spec_countries(spec: dict) -> set:
    return set((spec.get("geo_locations", {}) or {}).get("countries", []) or [])


def targeting_basis(before_spec: dict, after_spec: dict) -> tuple[str, float]:
    """Classify a targeting change into a heuristic family + a raw reach multiplier."""
    before_c, after_c = spec_countries(before_spec), spec_countries(after_spec)
    if after_c - before_c:
        return "add_countries", 2.0
    rb, ra = spec_radius(before_spec), spec_radius(after_spec)
    if rb and ra and rb != ra:
        return "radius_scale", (ra / rb) ** 2
    before_custom = {a.get("id") for a in (before_spec.get("custom_audiences") or [])}
    after_custom = {a.get("id") for a in (after_spec.get("custom_audiences") or [])}
    if after_custom - before_custom:
        return "add_custom_audience", 1.05
    return "targeting_other", 1.0


def live_adset_targeting(adset_id: str, token: str) -> dict:
    """Fetch an ad set's current live targeting spec. {} on failure."""
    from pikorua_adflow.tools.meta_tool import _get
    try:
        return _get(adset_id, token, {"fields": "targeting"}).get("targeting", {}) or {}
    except Exception:
        return {}


def live_adset_budget_inr(adset_id: str, token: str) -> int | None:
    """Fetch the ad set's current daily_budget from Meta and convert paise → INR.
    Returns None if the call fails or the field is absent (e.g. CBO campaign)."""
    from pikorua_adflow.tools.meta_tool import _get
    try:
        data = _get(adset_id, token, {"fields": "daily_budget"})
        paise = data.get("daily_budget")
        return int(int(paise) / 100) if paise else None
    except Exception:
        return None
