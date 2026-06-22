"""
creative_performance.py — shared variant comparison helper.

Extracted from routes/deploy.py::meta_performance() so the same comparative
logic can be used by:
  • routes/deploy.py  (the per-run performance tab — cosmetic, human-readable)
  • services/autopilot.py Rung 0 (creative winner/loser auto-management)
  • services/autopilot.py A/B resolve pass (B2)

Design constraints:
  - Never makes a winner/loser call without enough data (MIN_IMPR_TO_JUDGE
    impressions AND MIN_LEADS_TO_JUDGE leads on the candidate loser).
  - Returns raw numbers + labels; the caller decides what to DO with them.
  - Never raises — [] / empty dict on any failure so callers stay resilient.
"""

from __future__ import annotations

# Minimum data before we trust a winner/loser call.
MIN_IMPR_TO_JUDGE = 1_000   # impressions on the candidate loser
MIN_LEADS_TO_JUDGE = 3       # leads on the candidate loser
LOSER_CPL_RATIO = 2.0        # loser CPL must be ≥ this × avg to be flagged
WINNER_CPL_RATIO = 0.65      # winner CPL must be ≤ this × avg to be confirmed
AB_WINNER_CPL_RATIO = 0.80   # A/B-specific: challenger wins if CPL ≤ 0.8× control
AB_LOSER_CPL_RATIO = 1.50    # A/B-specific: control wins if challenger CPL ≥ 1.5× control


def compare_variants(ads: list[dict], token: str) -> dict:
    """
    Fetch 7-day insights for a list of ad records and compute comparative
    performance across variants within a single campaign.

    ads: [{ad_id, variant, adset_id, ...}]  (same shape as meta_ads in RUNS)

    Returns:
      {
        "variants": [
          {variant, ad_id, adset_id, metrics, rank_label, has_enough_data}
        ],
        "avg_cpl":         float | None,
        "avg_ctr":         float | None,
        "best_cpl_v":      int | None,   # variant number with lowest CPL
        "worst_cpl_v":     int | None,   # variant number with highest CPL
        "best_ctr_v":      int | None,
        "has_winner_loser": bool,        # True ↔ a clear pair exists w/ enough data
        "winner_ad_id":    str | None,
        "winner_adset_id": str | None,
        "loser_ad_id":     str | None,
        "loser_adset_id":  str | None,
        "winner_cpl":      float | None,
        "loser_cpl":       float | None,
      }
    """
    from pikorua_adflow.tools.meta_tool import fetch_insights
    from pikorua_adflow.api.services import deploy_service as ds

    raw: list[dict] = []
    for a in ads:
        try:
            insights = fetch_insights(a["ad_id"], token)
            metrics = ds.metrics_from_insight(insights[0]) if insights else {}
        except Exception:
            metrics = {}
        raw.append({
            "variant": a.get("variant"),
            "ad_id": a["ad_id"],
            "adset_id": a.get("adset_id", ""),
            "metrics": metrics,
            "rank_label": None,
            "has_enough_data": False,
        })

    # Only consider variants that have actually spent (impressions > 0).
    with_spend = [r for r in raw if (r["metrics"].get("impressions") or 0) > 0]

    avg_cpl = avg_ctr = None
    best_cpl_v = worst_cpl_v = best_ctr_v = None

    if len(with_spend) >= 2:
        cpl_pairs = [
            (r["variant"], r["ad_id"], r["adset_id"], r["metrics"]["cpl"])
            for r in with_spend
            if r["metrics"].get("cpl") is not None
        ]
        ctr_pairs = [
            (r["variant"], float(r["metrics"].get("ctr") or 0))
            for r in with_spend
        ]
        if cpl_pairs:
            avg_cpl = sum(c[3] for c in cpl_pairs) / len(cpl_pairs)
            best_cpl_v = min(cpl_pairs, key=lambda x: x[3])[0]
            worst_cpl_v = max(cpl_pairs, key=lambda x: x[3])[0]
        if ctr_pairs:
            avg_ctr = sum(c[1] for c in ctr_pairs) / len(ctr_pairs)
            best_ctr_v = max(ctr_pairs, key=lambda x: x[1])[0]

    # Assign rank labels and data-sufficiency flags.
    for r in raw:
        vnum = r["variant"]
        metrics = r["metrics"]
        cpl = metrics.get("cpl")
        ctr = float(metrics.get("ctr") or 0)
        impr = int(metrics.get("impressions") or 0)
        leads = int(metrics.get("leads") or 0)

        has_enough = impr >= MIN_IMPR_TO_JUDGE and leads >= MIN_LEADS_TO_JUDGE
        r["has_enough_data"] = has_enough

        if not has_enough or avg_cpl is None or cpl is None:
            continue

        if cpl > LOSER_CPL_RATIO * avg_cpl and vnum == worst_cpl_v:
            r["rank_label"] = "Underperforming"
        elif cpl < WINNER_CPL_RATIO * avg_cpl and vnum == best_cpl_v:
            r["rank_label"] = "Top performer"

    # Determine winner/loser pair for Rung 0 auto-management.
    winner_ad_id = winner_adset_id = None
    loser_ad_id = loser_adset_id = None
    winner_cpl = loser_cpl = None
    has_winner_loser = False

    if avg_cpl is not None:
        winners = [r for r in raw if r["rank_label"] == "Top performer" and r["has_enough_data"]]
        losers  = [r for r in raw if r["rank_label"] == "Underperforming" and r["has_enough_data"]]
        if winners and losers:
            w = winners[0]
            l = losers[0]
            has_winner_loser = True
            winner_ad_id = w["ad_id"]
            winner_adset_id = w["adset_id"]
            loser_ad_id = l["ad_id"]
            loser_adset_id = l["adset_id"]
            winner_cpl = w["metrics"].get("cpl")
            loser_cpl = l["metrics"].get("cpl")

    return {
        "variants": raw,
        "avg_cpl": avg_cpl,
        "avg_ctr": avg_ctr,
        "best_cpl_v": best_cpl_v,
        "worst_cpl_v": worst_cpl_v,
        "best_ctr_v": best_ctr_v,
        "has_winner_loser": has_winner_loser,
        "winner_ad_id": winner_ad_id,
        "winner_adset_id": winner_adset_id,
        "loser_ad_id": loser_ad_id,
        "loser_adset_id": loser_adset_id,
        "winner_cpl": winner_cpl,
        "loser_cpl": loser_cpl,
    }


def compare_ab_pair(control_ad_id: str, challenger_ad_id: str,
                    control_adset_id: str, challenger_adset_id: str,
                    token: str) -> dict:
    """
    Compare a specific A/B pair (control vs challenger) after the window.

    Returns:
      {
        "verdict":         "challenger_wins" | "control_wins" | "inconclusive",
        "control_cpl":     float | None,
        "challenger_cpl":  float | None,
        "has_enough_data": bool,
        "reason":          str,
      }
    """
    ads = [
        {"ad_id": control_ad_id, "variant": 0, "adset_id": control_adset_id},
        {"ad_id": challenger_ad_id, "variant": 1, "adset_id": challenger_adset_id},
    ]
    result = compare_variants(ads, token)
    variants = {r["variant"]: r for r in result["variants"]}
    ctrl = variants.get(0, {})
    chal = variants.get(1, {})

    ctrl_cpl = (ctrl.get("metrics") or {}).get("cpl")
    chal_cpl = (chal.get("metrics") or {}).get("cpl")
    ctrl_enough = ctrl.get("has_enough_data", False)
    chal_enough = chal.get("has_enough_data", False)
    has_enough = ctrl_enough and chal_enough

    if not has_enough or ctrl_cpl is None or chal_cpl is None:
        return {
            "verdict": "inconclusive",
            "control_cpl": ctrl_cpl,
            "challenger_cpl": chal_cpl,
            "has_enough_data": has_enough,
            "reason": "Not enough data yet to declare a winner.",
        }

    if chal_cpl <= AB_WINNER_CPL_RATIO * ctrl_cpl:
        return {
            "verdict": "challenger_wins",
            "control_cpl": ctrl_cpl,
            "challenger_cpl": chal_cpl,
            "has_enough_data": True,
            "reason": (f"Fresh creative costs \u20b9{round(chal_cpl):,}/enquiry vs "
                       f"\u20b9{round(ctrl_cpl):,} for the original — "
                       f"{round(ctrl_cpl / chal_cpl, 1)}\u00d7 better. Pausing the original."),
        }

    if chal_cpl >= AB_LOSER_CPL_RATIO * ctrl_cpl:
        return {
            "verdict": "control_wins",
            "control_cpl": ctrl_cpl,
            "challenger_cpl": chal_cpl,
            "has_enough_data": True,
            "reason": (f"Original creative costs \u20b9{round(ctrl_cpl):,}/enquiry vs "
                       f"\u20b9{round(chal_cpl):,} for the fresh one. Keeping the original."),
        }

    return {
        "verdict": "inconclusive",
        "control_cpl": ctrl_cpl,
        "challenger_cpl": chal_cpl,
        "has_enough_data": True,
        "reason": (f"Results too close to call (\u20b9{round(ctrl_cpl):,} vs \u20b9{round(chal_cpl):,}). "
                   "Extending the window by 2 days."),
    }
