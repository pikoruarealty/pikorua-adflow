"""Improve-targeting suggestions: property-type fit + name-level dedup.
No network — the CRM ADD path is left off (no token) so we test the pure rules."""

from pikorua_adflow.analytics import targeting_intelligence as ti


def test_apartment_campaign_flags_bungalow_for_removal():
    cur = {"interests": [{"id": "6003011972081", "name": "Bungalow"}],
           "behaviours": [], "work_positions": []}
    r = ti.suggest_targeting_changes(cur, clientele_type="premium_apartment",
                                     property_type="luxury apartment")
    assert any(x["name"] == "Bungalow" and x["field"] == "interests" for x in r["remove"])


def test_house_campaign_keeps_bungalow():
    cur = {"interests": [{"id": "6003011972081", "name": "Bungalow"}],
           "behaviours": [], "work_positions": []}
    r = ti.suggest_targeting_changes(cur, clientele_type="luxury_bungalow",
                                     property_type="4 BHK bungalow")
    assert not any(x["name"] == "Bungalow" for x in r["remove"])


def test_profile_workpos_not_resuggested_when_present_by_name():
    # Owner and CEO already selected — must not be re-suggested even with a diff id.
    cur = {"interests": [], "behaviours": [],
           "work_positions": [{"id": "different-id-000", "name": "Owner and CEO"}]}
    r = ti.suggest_targeting_changes(cur, clientele_type="hni")
    assert not any(s["name"] == "Owner and CEO" for s in r["add"])


def test_affluence_proxy_not_resuggested_by_name():
    proxy = ti._mt.AFFLUENCE_PROXY_BEHAVIOUR
    cur = {"interests": [], "work_positions": [],
           "behaviours": [{"id": "other-id", "name": proxy["name"]}]}
    r = ti.suggest_targeting_changes(cur, clientele_type="hni")
    assert not any(s["name"] == proxy["name"] for s in r["add"])


def test_property_kind_classifier():
    assert ti._property_kind("3 BHK flat") == "apartment"
    assert ti._property_kind("penthouse") == "apartment"
    assert ti._property_kind("villa") == "house"
    assert ti._property_kind("") == ""
