"""Geo targeting: pincode proximity, the map-pin (custom_locations) round-trip,
and city-coverage — all pure logic, no network."""

from pikorua_adflow.tools import meta_targeting as mt
from pikorua_adflow.analytics import geo_intelligence as gi


def test_proximity_sort_ranks_nearest_pincode_first():
    rows = [{"name": "380054"}, {"name": "380015"}, {"name": "380001"}, {"name": "no-pin"}]
    ranked = [r["name"] for r in mt._proximity_sort(rows, "380015")]
    assert ranked[0] == "380015"          # exact match first
    assert ranked[1] == "380001"          # |15-1|=14 closer than |54-15|=39
    assert ranked[-1] == "no-pin"         # unparseable pincode sinks to the bottom


def test_proximity_sort_no_home_is_noop():
    rows = [{"name": "380054"}, {"name": "380001"}]
    assert mt._proximity_sort(rows, "") == rows


def test_map_mode_emits_custom_locations():
    aud = {"geo_mode": "map", "city_key": "12345",
           "map_point": {"lat": 23.03, "lng": 72.57, "radius_km": 6, "city_key": "12345"},
           "interests": [], "behaviours": []}
    geo = mt.build_targeting_spec(aud)["geo_locations"]
    assert "custom_locations" in geo
    loc = geo["custom_locations"][0]
    assert loc["latitude"] == 23.03 and loc["longitude"] == 72.57
    assert loc["radius"] == 6 and loc["distance_unit"] == "kilometer"
    assert loc["primary_city_id"] == "12345"


def test_map_radius_clamped_to_meta_bounds():
    aud = {"geo_mode": "map", "city_key": "1",
           "map_point": {"lat": 1.0, "lng": 2.0, "radius_km": 999},
           "interests": [], "behaviours": []}
    assert mt.build_targeting_spec(aud)["geo_locations"]["custom_locations"][0]["radius"] == mt._RADIUS_MAX_KM


def test_map_point_round_trips_through_spec():
    aud = {"geo_mode": "map", "city_key": "12345", "city": "Ahmedabad",
           "map_point": {"lat": 23.03, "lng": 72.57, "radius_km": 6, "city_key": "12345"},
           "interests": [], "behaviours": []}
    spec = mt.build_targeting_spec(aud)
    back = mt.audience_from_targeting_spec(spec, {"city_key": "12345", "city": "Ahmedabad"})
    assert back["geo_mode"] == "map"
    assert back["map_point"]["lat"] == 23.03 and back["map_point"]["radius_km"] == 6


def test_covered_city_keys_counts_map_point():
    spec = {"geo_locations": {}}
    saved = {"map_point": {"city_key": "12345"}}
    assert "12345" in gi.covered_city_keys(spec, saved)


def test_radius_and_areas_modes_unchanged():
    # radius mode still emits cities[key,radius]
    r = mt.build_targeting_spec({"geo_mode": "radius", "city_key": "9", "radius_km": 25,
                                 "interests": [], "behaviours": []})["geo_locations"]
    assert r["cities"][0]["key"] == "9"
    # areas mode still emits neighborhoods/zips
    a = mt.build_targeting_spec({"geo_mode": "areas", "city_key": "9",
                                 "neighborhoods": [{"key": "n1"}], "zips": [{"key": "z1"}],
                                 "interests": [], "behaviours": []})["geo_locations"]
    assert a["neighborhoods"] == [{"key": "n1"}] and a["zips"] == [{"key": "z1"}]
    assert "custom_locations" not in a
