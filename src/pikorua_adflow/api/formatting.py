"""
Currency (INR) and timezone (IST) formatting helpers.

Used by route handlers to convert Meta's USD figures to INR and to render every
timestamp in IST before it reaches the client. The frontend has matching JS
(`static/app.js`) for values it formats itself, but anything derived server-side
(e.g. Meta spend/CPL) is converted here so the rate lives in one place.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .config import IST, USD_TO_INR


def to_inr(usd: float | int | None) -> float:
    """Convert a USD amount (as returned by the Meta API) to INR."""
    if usd is None:
        return 0.0
    try:
        return float(usd) * USD_TO_INR
    except (TypeError, ValueError):
        return 0.0


def fmt_inr(amount: float | int | None, *, decimals: int = 0) -> str:
    """Format a number as INR with Indian digit grouping, e.g. ₹1,23,456."""
    if amount is None:
        return "₹0"
    try:
        n = float(amount)
    except (TypeError, ValueError):
        return "₹0"
    neg = n < 0
    n = abs(n)
    whole = int(n)
    frac = f"{round(n - whole, decimals):.{decimals}f}".split(".")[1] if decimals else ""
    # Indian grouping: last 3 digits, then groups of 2.
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        import re
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = f"{head},{tail}"
    out = s + (f".{frac}" if decimals else "")
    return f"{'-' if neg else ''}₹{out}"


def _parse_dt(value) -> datetime | None:
    """Coerce a datetime or ISO-8601 string into an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_ist_iso(value) -> str:
    """Return an ISO-8601 string in IST (+05:30). Empty string if unparseable."""
    dt = _parse_dt(value)
    if dt is None:
        return ""
    return dt.astimezone(IST).isoformat()


def fmt_ist(value) -> str:
    """Human-readable IST timestamp, e.g. '18 Jun 2026, 7:30 PM IST'."""
    dt = _parse_dt(value)
    if dt is None:
        return ""
    local = dt.astimezone(IST)
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local.day:02d} {local.strftime('%b')} {local.year}, {hour12}:{local.minute:02d} {ampm} IST"
