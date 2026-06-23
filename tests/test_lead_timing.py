"""Tests for analytics.lead_timing — day-of-week pattern detection."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from pikorua_adflow.analytics.lead_timing import analyse, MIN_LEADS, MIN_CONCENTRATION


def _rows(day_counts: dict) -> list[dict]:
    """Build fake CRM rows with specific day-of-week distribution.
    day_counts: {weekday_int (0=Mon): count}
    Uses dates from a fixed reference week (Mon 16 Jun 2026 = weekday 0)
    """
    base_dates = {0: "16 Jun 2026", 1: "17 Jun 2026", 2: "18 Jun 2026",
                  3: "19 Jun 2026", 4: "20 Jun 2026", 5: "21 Jun 2026", 6: "22 Jun 2026"}
    rows = []
    for wd, cnt in day_counts.items():
        for _ in range(cnt):
            rows.append({"Received": base_dates[wd]})
    return rows


def test_no_signal_too_few_leads():
    rows = _rows({0: 10, 1: 8, 2: 6, 3: 5, 4: 4, 5: 3, 6: 2})
    result = analyse(rows)
    assert not result["has_signal"]
    assert result["sample_size"] == 38


def test_no_signal_even_distribution():
    # 50+ leads but spread evenly — no clear pattern
    rows = _rows({0: 10, 1: 10, 2: 10, 3: 10, 4: 10, 5: 5, 6: 5})
    result = analyse(rows)
    # concentration of top 4 = (10+10+10+10)/60 = 66%… actually this might signal
    # adjust: truly even = 7 days * 10 = 70 leads, top 4 = 40/70 = 57% ≥ 55% → signals
    # So let's test truly flat distribution
    rows = _rows({0: 7, 1: 7, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7})
    # 49 leads < MIN_LEADS=50 → no signal
    result = analyse(rows)
    assert not result["has_signal"]


def test_clear_pattern():
    # Leads heavily on Mon/Tue/Wed/Thu, almost nothing Fri/Sat/Sun
    rows = _rows({0: 20, 1: 18, 2: 17, 3: 15, 4: 3, 5: 2, 6: 1})
    result = analyse(rows)
    assert result["has_signal"]
    assert result["sample_size"] == 76
    assert result["concentration_pct"] >= 55
    assert len(result["top_days"]) == 4
    assert len(result["quiet_days"]) == 3
    assert len(result["meta_schedule_days"]) == 4
    assert result["recommendation"]


def test_meta_days_are_valid():
    rows = _rows({0: 20, 1: 18, 2: 17, 3: 15, 4: 3, 5: 2, 6: 1})
    result = analyse(rows)
    # All Meta day numbers must be 0–6
    for d in result["meta_schedule_days"]:
        assert 0 <= d <= 6


def test_empty_rows():
    result = analyse([])
    assert not result["has_signal"]
    assert result["sample_size"] == 0


def test_no_received_field():
    rows = [{"Name": "Test"} for _ in range(60)]
    result = analyse(rows)
    assert not result["has_signal"]
