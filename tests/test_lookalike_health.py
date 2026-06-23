"""Tests for analytics.lookalike_health — staleness detection."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from datetime import datetime, timedelta, timezone
from pikorua_adflow.analytics.lookalike_health import check_staleness, REFRESH_DAYS, GROWTH_THRESHOLD, MIN_NEW_LEADS


def _built(days_ago: int, seed_size: int = 800) -> list[dict]:
    built_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return [{"id": "123", "role": "lookalike", "subtype": "LOOKALIKE",
             "built_at": built_at, "seed_size": seed_size}]


def test_fresh_not_stale():
    rows = _built(5, seed_size=1000)
    result = check_staleness(rows, 1010)  # tiny growth, recent
    assert not result["stale"]


def test_stale_by_age():
    rows = _built(REFRESH_DAYS + 1, seed_size=900)
    result = check_staleness(rows, 920)  # small growth but old
    assert result["stale"]
    assert result["age_days"] >= REFRESH_DAYS + 1


def test_stale_by_growth():
    # Large growth even if recently built
    seed = 500
    new_count = seed + MIN_NEW_LEADS + 1  # just over the absolute threshold
    growth = (new_count - seed) / seed
    assert growth >= GROWTH_THRESHOLD
    rows = _built(2, seed_size=seed)  # 2 days old — not age-stale
    result = check_staleness(rows, new_count)
    assert result["stale"]
    assert result["growth_pct"] > 0


def test_no_lookalike_in_registry():
    rows = [{"id": "999", "role": "seed", "subtype": "CUSTOM"}]
    result = check_staleness(rows, 1000)
    assert not result["stale"]


def test_empty_registry():
    result = check_staleness([], 500)
    assert not result["stale"]


def test_missing_built_at():
    rows = [{"id": "123", "role": "lookalike", "subtype": "LOOKALIKE", "seed_size": 500}]
    result = check_staleness(rows, 800)
    # No built_at → can't determine age → not stale (conservative)
    assert not result["stale"]
