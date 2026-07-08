"""
Tests for analytics.lead_rules — the ordered categorisation engine that replaced
the substring-before-negation bug, plus the E.164 phone normalisation fix.
"""
from __future__ import annotations

import pytest

from pikorua_adflow.analytics import lead_rules as lr
from pikorua_adflow.analytics import crm_analytics as ca


def _classify_default(**canon):
    """Classify a normalised row against the DEFAULT rules (no persisted file)."""
    return lr.classify(canon, rules=[dict(r) for r in lr.DEFAULT_RULES])


# ── T1 regression: "not interested" must be BAD, never GOOD ────────────────────
def test_not_interested_client_is_bad():
    assert _classify_default(clientstatus="Not Interested") == "bad"


def test_not_interested_buying_is_bad():
    assert _classify_default(clientstatus="", buyingstatus="Not Interested") == "bad"


def test_interested_client_is_good():
    assert _classify_default(clientstatus="Interested") == "good"


def test_warm_buyer_compound_is_good():
    # Compound status must still match the substring "warm".
    assert _classify_default(buyingstatus="Follow up (warm)") == "good"


def test_client_status_priority_over_buying():
    # Explicit sales disposition (warm) wins over a stale inbound buying field (cold).
    assert _classify_default(clientstatus="Warm", buyingstatus="cold") == "good"


def test_broker_beats_everything():
    assert _classify_default(clientstatus="broker", buyingstatus="warm") == "broker"


def test_hot_hwc_is_good():
    # The HWC = Hot default rule the user configured in the editor.
    assert _classify_default(hwc="Hot") == "good"


def test_hot_hwc_does_not_override_not_interested():
    assert _classify_default(clientstatus="Not Interested", hwc="Hot") == "bad"


def test_site_visit_is_good():
    assert _classify_default(sitevisitstatus="Completed") == "good"


def test_site_visit_does_not_override_bad():
    assert _classify_default(buyingstatus="cold", sitevisitstatus="Completed") == "bad"


def test_unclassified_fallback():
    assert _classify_default(clientstatus="", buyingstatus="") == "unclassified"


def test_is_good_matches_classify():
    row = {"clientstatus": "warm"}
    assert lr.is_good(row, rules=[dict(r) for r in lr.DEFAULT_RULES]) is True
    row2 = {"clientstatus": "not interested"}
    assert lr.is_good(row2, rules=[dict(r) for r in lr.DEFAULT_RULES]) is False


# ── crm_analytics._is_quality now routes through the engine ───────────────────
def test_crm_quality_not_interested_is_not_quality():
    row = ca._normalize([{"Client Status": "Not Interested"}])[0]
    assert ca._is_quality(row) is False


def test_crm_quality_warm_is_quality():
    row = ca._normalize([{"Client Status": "Warm"}])[0]
    assert ca._is_quality(row) is True


# ── validate_rules / persistence ──────────────────────────────────────────────
def test_validate_rejects_bad_category():
    with pytest.raises(ValueError):
        lr.validate_rules([{"category": "nonsense", "conditions": [{"field": "hwc", "values": ["hot"]}]}])


def test_validate_rejects_bad_field():
    with pytest.raises(ValueError):
        lr.validate_rules([{"category": "good", "conditions": [{"field": "made_up", "values": ["x"]}]}])


def test_validate_rejects_empty_conditions():
    with pytest.raises(ValueError):
        lr.validate_rules([{"category": "good", "conditions": []}])


def test_validate_cleans_and_defaults_op():
    clean = lr.validate_rules([{"category": "good",
                                "conditions": [{"field": "hwc", "values": [" Hot ", ""]}]}])
    assert clean[0]["conditions"][0]["op"] == "contains_any"
    assert clean[0]["conditions"][0]["values"] == ["Hot"]
    assert clean[0]["id"]


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "_RULES_PATH", tmp_path / "rules.json")
    rules = [{"category": "bad", "conditions": [{"field": "clientstatus", "values": ["spam"]}]}]
    saved = lr.save_rules(rules)
    assert lr.is_using_defaults() is False
    loaded = lr.load_rules()
    assert loaded[0]["category"] == "bad"
    assert loaded == saved


def test_and_chaining_requires_all_conditions():
    rules = [{"category": "good", "conditions": [
        {"field": "clientstatus", "op": "contains_any", "values": ["warm"]},
        {"field": "hwc", "op": "contains_any", "values": ["hot"]},
    ]}]
    assert lr.classify({"clientstatus": "warm", "hwc": "hot"}, rules) == "good"
    assert lr.classify({"clientstatus": "warm", "hwc": "cold"}, rules) == "unclassified"


def test_editor_metadata_shape():
    meta = lr.editor_metadata()
    assert any(f["key"] == "clientstatus" for f in meta["fields"])
    assert any(c["key"] == "good" for c in meta["categories"])
