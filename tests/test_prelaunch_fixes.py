"""
Regression tests for the pre-launch audit fixes:
  T2  — E.164 phone normalisation before hashing
  A5  — LLM strategist safe-action allowlist
  A1  — winner/loser rung sizes the boost from the live ad-set budget (pause-only
        when the budget can't be read) — never fabricates a default that cuts winners
  A4  — reduce_budget rung reads the live ad-set budget (fires under ABO)
  CR3 — webhook X-Hub-Signature-256 verification
"""
from __future__ import annotations

import pytest


# ── T2: phone normalisation ───────────────────────────────────────────────────
def test_phone_normalisation_variants():
    from pikorua_adflow.tools.meta_audience_tool import _normalize_phone
    assert _normalize_phone("+91 98765-43210") == "919876543210"
    assert _normalize_phone("098765 43210") == "919876543210"
    assert _normalize_phone("9876543210") == "919876543210"
    assert _normalize_phone("00919876543210") == "919876543210"
    assert _normalize_phone("919876543210") == "919876543210"
    assert _normalize_phone("12345") == ""          # too short → dropped
    assert _normalize_phone("") == ""


# ── A5: LLM safe-action allowlist ─────────────────────────────────────────────
def test_llm_safe_actions_exclude_budget_and_pause():
    from pikorua_adflow.api.services.autooptimiser import _LLM_SAFE_ACTIONS
    for danger in ("winner_loser", "reduce_budget", "pause", "add_nri"):
        assert danger not in _LLM_SAFE_ACTIONS
    for safe in ("add_exclusion", "add_lookalike", "add_interests"):
        assert safe in _LLM_SAFE_ACTIONS


# ── CR3: webhook signature verification ───────────────────────────────────────
def test_webhook_signature_verification():
    import hashlib
    import hmac
    from pikorua_adflow.api.routes.webhook import _verify_signature
    secret = "app_secret_123"
    body = b'{"entry":[]}'
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert _verify_signature(body, good, secret) is True
    assert _verify_signature(body, good, "wrong_secret") is False
    assert _verify_signature(body, "sha256=deadbeef", secret) is False
    assert _verify_signature(body, "", secret) is False


# ── A1 / A4: ladder reads the LIVE ad-set budget ──────────────────────────────
def _base_targeting():
    return {"geo_locations": {"cities": [{"key": "1", "radius": 20}], "countries": ["IN"]},
            "flexible_spec": [{"interests": [1, 2, 3, 4, 5, 6]}]}


def _patch_geo(monkeypatch):
    from pikorua_adflow.analytics import geo_intelligence as gi
    monkeypatch.setattr(gi, "geo_recommendations",
                        lambda *a, **k: {"trim": [], "add": [], "expand": []})


def test_reduce_budget_uses_live_adset_budget(monkeypatch):
    import pikorua_adflow.api.services.autooptimiser as ao
    from pikorua_adflow.api.services import deploy_service as ds
    _patch_geo(monkeypatch)
    monkeypatch.setattr(ds, "live_adset_budget_inr", lambda adset_id, token: 5000)

    campaign = {"id": "c1", "name": "Test", "daily_budget": None}   # ABO → no campaign budget
    adsets = [{"id": "as1", "effective_status": "ACTIVE", "targeting": _base_targeting()}]
    ads = [{"id": "ad1", "effective_status": "ACTIVE"}]             # single ad → no rung 0
    metrics = {"d7": {"frequency": 4.0, "cpl": 800, "ctr": 1.5, "spend": 1000,
                      "leads": 2, "impressions": 1000},
               "d30": {"cpl": 600}, "cpl_rising": True}
    fixes = ao._ladder(campaign, adsets, ads, metrics, {"clientele_type": ""}, {},
                       {"cooldowns": {}, "applied": []}, crm_leads=[], run_id=None)
    rb = [f for f in fixes if f["fix_type"] == "reduce_budget"]
    assert rb, "reduce_budget must fire under ABO when the live ad-set budget is readable"
    assert rb[0]["params"]["base_budget"] == 5000
    assert rb[0]["params"]["daily_budget_inr"] == 3500   # 5000 * 0.7


def test_winner_loser_pause_only_when_budget_unreadable(monkeypatch):
    import pikorua_adflow.api.services.autooptimiser as ao
    from pikorua_adflow.analytics import creative_performance as cp
    from pikorua_adflow.api.services import deploy_service as ds
    _patch_geo(monkeypatch)
    monkeypatch.setattr(cp, "compare_variants", lambda ads, token: {
        "has_winner_loser": True, "winner_cpl": 100, "loser_cpl": 400,
        "loser_ad_id": "adL", "winner_adset_id": "asW"})
    # Budget can't be read (CBO / API hiccup) → must NOT fabricate a value.
    monkeypatch.setattr(ds, "live_adset_budget_inr", lambda adset_id, token: None)

    campaign = {"id": "c1", "name": "Test", "daily_budget": None}
    adsets = [{"id": "as1", "effective_status": "ACTIVE",
               "targeting": {"geo_locations": {"countries": ["IN"]}}}]
    ads = [{"id": "ad1", "effective_status": "ACTIVE"},
           {"id": "ad2", "effective_status": "ACTIVE"}]
    metrics = {"d7": {"frequency": 2.0, "cpl": 300, "ctr": 1.5, "spend": 100,
                      "leads": 1, "impressions": 500},
               "d30": {"cpl": 300}, "cpl_rising": False}
    fixes = ao._ladder(campaign, adsets, ads, metrics, {}, {},
                       {"cooldowns": {}, "applied": []}, crm_leads=[], run_id=None)
    wl = [f for f in fixes if f["fix_type"] == "winner_loser"]
    assert wl, "winner_loser should still surface (to pause the loser)"
    assert wl[0]["params"]["new_budget_inr"] is None   # pause-only, no budget write
