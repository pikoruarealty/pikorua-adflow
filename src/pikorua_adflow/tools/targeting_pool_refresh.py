"""
Targeting pool refresh — discoverability report, never auto-merged.

Meta periodically adds new targeting categories (e.g. the household-income
clusters added a few months ago) through the same Targeting Search API this
codebase already resolves interests/behaviours/cities through. Nothing today
queries the categories CLIENTELE_TARGETING_MAP hardcodes by id
(work_positions, income clusters) against Meta's live list, so a new or
renamed category is invisible until someone notices by hand.

This module queries the targeting-category classes Meta exposes beyond what
meta_targeting.py resolves live (interests/behaviours), diffs the results
against the ids already hardcoded in meta_targeting.py, and writes a report
of what's new. It never writes to CLIENTELE_TARGETING_MAP or any pool
constant — a human reviews the report and edits meta_targeting.py by hand.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from . import meta_targeting as _mt

_REPORT_PATH = pathlib.Path("outputs") / "targeting_pool_report.json"

# Targeting Search API classes not already queried live by meta_targeting.py
# (which only resolves "adinterest" and class=behaviors on demand).
_CLASSES_TO_CHECK = ["work_positions", "demographics", "life_events"]


def _known_ids() -> set[str]:
    """Every category id already hardcoded anywhere in meta_targeting.py's pools."""
    pools = [
        _mt._INCOME_TOP_10,
        _mt._WORK_POSITIONS_OWNERS,
        _mt._WORK_POSITIONS_CSUITE,
        _mt._WORK_POSITIONS_IT_CSUITE_AVOID,
        _mt._WORK_POSITION_PROPERTY,
        _mt._WORK_POSITION_CA,
        _mt._INDUSTRIES_ENTERPRISE,
    ]
    return {str(entry["id"]) for pool in pools for entry in pool}


def _fetch_class(cls: str, token: str, cache: dict) -> list[dict]:
    """Query one Targeting Search class, using meta_targeting's disk cache."""
    cache_key = f"pool_refresh:{cls}"
    cached = _mt._cache_get(cache, cache_key)
    if cached is not _mt._MISS:
        return cached
    try:
        data = _mt._get({"type": "adTargetingCategory", "class": cls, "limit": 2000}, token)
    except RuntimeError:
        data = []
    result = [
        {"id": str(d["id"]), "name": d.get("name", ""), "audience_size": d.get("audience_size_upper_bound") or 0}
        for d in data
        if d.get("id")
    ]
    _mt._cache_set(cache, cache_key, result)
    return result


def generate_report(token: str) -> dict:
    """Query the unqueried Targeting Search classes and diff against the hardcoded pools.

    Returns (and writes to outputs/targeting_pool_report.json) a dict of:
      generated_at   — ISO timestamp
      classes        — {class_name: {total, new: [{id, name}], known_count}}
    "new" entries are categories Meta returns that aren't in any hardcoded pool
    constant — candidates for a human to review and, if relevant, add to
    CLIENTELE_TARGETING_MAP by hand. This function never modifies that map.
    """
    cache = _mt._load_cache()
    known = _known_ids()

    classes: dict[str, Any] = {}
    for cls in _CLASSES_TO_CHECK:
        entries = _fetch_class(cls, token, cache)
        new_entries = [e for e in entries if e["id"] not in known]
        classes[cls] = {
            "total": len(entries),
            "known_count": len(entries) - len(new_entries),
            "new": new_entries,
        }

    _mt._save_cache(cache)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classes": classes,
    }
    try:
        _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return report


def _main() -> None:
    import os
    import sys

    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        print("META_ACCESS_TOKEN not set.", file=sys.stderr)
        raise SystemExit(1)
    report = generate_report(token)
    for cls, info in report["classes"].items():
        print(f"{cls}: {info['total']} total, {len(info['new'])} not in our hardcoded pools")
        for e in info["new"][:20]:
            print(f"    {e['id']}  {e['name']}")
    print(f"\nFull report written to {_REPORT_PATH}")


if __name__ == "__main__":
    _main()
