"""
Lead arrival timing analysis — day-of-week and hour-of-day patterns.

When enough leads have arrived, their timestamps reveal when buyers are most active.
Scheduling ads to run harder on peak days and lighter on quiet ones stops budget
burning on days with near-zero intent.

Data sources:
  CSV path  → "Received" field is "19 Jun 2026" (date only; day-of-week analysis only)
  Supabase  → "Received" is ISO 8601 with time; enables hour-of-day analysis too
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

# Meta's day numbering: 0=Sunday, 1=Monday, … 6=Saturday
# Python's weekday():  0=Monday, … 6=Sunday
_PYTHON_TO_META = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}
_DAY_NAMES = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
_FULL_DAY_NAMES = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
                   4: "Thursday", 5: "Friday", 6: "Saturday"}  # Meta day index → name

MIN_LEADS = 50          # minimum leads to trust the pattern
MIN_CONCENTRATION = 0.55  # top half of days (3-4) must hold ≥55% of leads to signal


def _parse_date(raw: str) -> datetime | None:
    """Try several common date formats from CRM sources."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw.replace("Z", "+00:00"), fmt)
            return dt
        except ValueError:
            continue
    # ISO 8601 with timezone offset like "2026-06-19T08:32:00+05:30"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def analyse(rows: list[dict]) -> dict:
    """
    Compute the day-of-week (and optionally hour-of-day) distribution of incoming leads.

    Returns:
      has_signal        – bool, True when there's a meaningful, actionable pattern
      sample_size       – int, leads with parseable timestamps
      top_days          – list of Meta day numbers (0-6) that are peak
      quiet_days        – list of Meta day numbers that are low-activity
      top_day_names     – list of human-readable names for top_days
      quiet_day_names   – list of human-readable names for quiet_days
      concentration_pct – float, share of leads arriving on top_days (0–100)
      has_hour_data     – bool, True when hour-level timestamps are available
      peak_hours        – list of ints (0–23) if has_hour_data, else []
      recommendation    – one plain-English sentence
      meta_schedule_days – list[int] to pass to update_adset_schedule (top days, Meta numbering)
    """
    day_counts: Counter = Counter()
    hour_counts: Counter = Counter()
    has_hours = False
    parsed = 0

    for row in rows:
        raw = row.get("Received") or row.get("received_at") or ""
        dt = _parse_date(raw)
        if dt is None:
            continue
        parsed += 1
        # Python weekday 0=Mon → Meta day via _PYTHON_TO_META
        meta_day = _PYTHON_TO_META[dt.weekday()]
        day_counts[meta_day] += 1
        if dt.hour != 0 or "T" in raw or " " in raw.split(".")[-1]:
            # Only count hour when the source string plausibly has time info
            if "T" in raw or (len(raw) > 12 and ":" in raw):
                hour_counts[dt.hour] += 1
                has_hours = True

    no_signal = {
        "has_signal": False, "sample_size": parsed,
        "top_days": [], "quiet_days": [], "top_day_names": [], "quiet_day_names": [],
        "concentration_pct": 0.0, "has_hour_data": has_hours, "peak_hours": [],
        "recommendation": "", "meta_schedule_days": [],
    }

    if parsed < MIN_LEADS:
        return no_signal

    total = sum(day_counts.values())
    if total == 0:
        return no_signal

    # Sort days by lead count descending; take top half (3 or 4 days)
    ordered = sorted(day_counts.items(), key=lambda x: x[1], reverse=True)
    top_n = 4  # run full weight on 4 days; rest get lower priority
    top_days = [day for day, _ in ordered[:top_n]]
    quiet_days = [day for day, _ in ordered[top_n:]]
    concentration = sum(day_counts[d] for d in top_days) / total

    if concentration < MIN_CONCENTRATION:
        return no_signal  # leads are spread too evenly; no actionable pattern

    top_day_names = [_FULL_DAY_NAMES[d] for d in top_days]
    quiet_day_names = [_FULL_DAY_NAMES[d] for d in quiet_days]

    # Hour analysis (only when we have enough hour data)
    peak_hours: list[int] = []
    if has_hours and sum(hour_counts.values()) >= 30:
        hour_total = sum(hour_counts.values())
        hour_ordered = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)
        # Take hours that account for the top 60% of activity
        cumulative = 0.0
        for hr, cnt in hour_ordered:
            cumulative += cnt / hour_total
            peak_hours.append(hr)
            if cumulative >= 0.60:
                break
        peak_hours.sort()

    # Plain-English recommendation
    pct_str = f"{round(concentration * 100)}%"
    top_str = ", ".join(top_day_names[:-1]) + f" and {top_day_names[-1]}" if len(top_day_names) > 1 else top_day_names[0]
    recommendation = (
        f"{pct_str} of your leads arrive on {top_str}. "
        "Focusing ad delivery on those days gets the same reach for less budget."
    )

    return {
        "has_signal": True,
        "sample_size": parsed,
        "top_days": top_days,
        "quiet_days": quiet_days,
        "top_day_names": top_day_names,
        "quiet_day_names": quiet_day_names,
        "concentration_pct": round(concentration * 100, 1),
        "has_hour_data": has_hours,
        "peak_hours": peak_hours,
        "recommendation": recommendation,
        "meta_schedule_days": sorted(top_days),
    }
