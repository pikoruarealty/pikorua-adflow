"""Client-named target segment floor: resolve_named_segments() + the union into
build_default_audience(). No network — search_interests/search_behaviours and the
on-disk cache are monkeypatched."""

import pytest

from pikorua_adflow.tools import meta_targeting as mt


@pytest.fixture(autouse=True)
def _no_disk_cache(monkeypatch):
    monkeypatch.setattr(mt, "_load_cache", lambda: {})
    monkeypatch.setattr(mt, "_save_cache", lambda cache: None)


def _fake_search_interests(query, token, limit=8):
    return [{"id": f"i-{query.lower()}", "name": query, "audience_size": 1000}]


def _fake_search_behaviours(query, token, limit=8):
    return [{"id": f"b-{query.lower()}", "name": query, "audience_size": 1000}]


def test_empty_text_returns_nothing(monkeypatch):
    monkeypatch.setattr(mt, "search_interests", _fake_search_interests)
    monkeypatch.setattr(mt, "search_behaviours", _fake_search_behaviours)
    assert mt.resolve_named_segments("", "tok") == {}
    assert mt.resolve_named_segments("   ", "tok") == {}


def test_unrecognised_text_returns_nothing(monkeypatch):
    monkeypatch.setattr(mt, "search_interests", _fake_search_interests)
    monkeypatch.setattr(mt, "search_behaviours", _fake_search_behaviours)
    assert mt.resolve_named_segments("some random words about nothing", "tok") == {}


def test_named_segments_resolve_interests_behaviours_positions(monkeypatch):
    monkeypatch.setattr(mt, "search_interests", _fake_search_interests)
    monkeypatch.setattr(mt, "search_behaviours", _fake_search_behaviours)
    result = mt.resolve_named_segments(
        "target NRI investors, doctors, and IT professionals", "tok"
    )
    # NRI -> NRI industry interests + the nri_diaspora behaviour
    assert result["interests"]
    assert result["behaviours"]
    # "doctors" -> Doctor work position via the alias table
    wp_names = {w["name"] for w in result["work_positions"]}
    assert "Doctor" in wp_names


def test_build_default_audience_unions_named_segments_without_dropping_profile(monkeypatch):
    monkeypatch.setattr(mt, "search_interests", _fake_search_interests)
    monkeypatch.setattr(mt, "search_behaviours", _fake_search_behaviours)
    monkeypatch.setattr(mt, "_best_city", lambda name, token, country, cache: None)

    audience = mt.build_default_audience(
        "", "tok",
        clientele_type="hni_nri",
        must_include_text="doctors and NRI investors",
    )
    wp_names = {w["name"] for w in audience["work_positions"]}
    assert "Doctor" in wp_names
    # The static clientele profile's own interests/behaviours must still be present
    # alongside the guaranteed floor — union, not replacement.
    assert len(audience["interests"]) > 0
    assert audience["target_clienteles"] == "doctors and NRI investors"


def test_build_default_audience_no_must_include_is_unchanged(monkeypatch):
    monkeypatch.setattr(mt, "search_interests", _fake_search_interests)
    monkeypatch.setattr(mt, "search_behaviours", _fake_search_behaviours)
    monkeypatch.setattr(mt, "_best_city", lambda name, token, country, cache: None)

    audience = mt.build_default_audience("", "tok", clientele_type="hni_nri")
    assert audience["target_clienteles"] == ""
