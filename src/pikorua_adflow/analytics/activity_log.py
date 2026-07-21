"""
Automation activity log — a durable, client-facing record of everything the
system does on its own: daily/monthly scheduler runs, CRM fetches, auto-applied
optimisations, retargeting, CAPI lead-quality events, and inbound webhook leads.

Backed by an append-only JSONL file (outputs/activity_log.jsonl): one JSON object
per line — cheap to append, restart-safe, and trivial to tail. The AutoOptimiser
state file already owns "what fixes were applied"; this log is the broader,
human-readable timeline surfaced on the /activity page.

Every write is wrapped so logging can never break a caller — an activity-log
failure must never take down an optimisation pass or a webhook.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _REPO_ROOT / "outputs"
_LOG_PATH = _OUTPUT_DIR / "activity_log.jsonl"

# Keep the file bounded — trim to the newest _MAX_LINES when it grows past
# _TRIM_TRIGGER, so an always-on server doesn't grow it without limit.
_MAX_LINES = 5000
_TRIM_TRIGGER = 6000

# The kinds the /activity page knows how to group + icon. Kept here as the single
# source of truth; unknown kinds still log fine (they fall into "Other").
KINDS = (
    "crm_fetch", "optimise_auto", "optimise_manual", "retarget",
    "capi_qualified", "capi_disqualified", "webhook_lead",
    "scheduler_run", "scheduler_error",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(kind: str, summary: str, *, detail: str = "",
              campaign_id: str = "", campaign_name: str = "",
              status: str = "ok", meta: dict | None = None) -> None:
    """
    Append one event to the activity log. Never raises.

    kind         one of KINDS (free-form tolerated; UI buckets unknowns as "Other")
    summary      one-line plain-language description shown in the timeline
    detail       optional longer text (second line / expandable)
    status       "ok" | "error" | "info" — drives the colour dot on the page
    meta         small dict of extra structured fields (counts, ids, ...)
    """
    try:
        entry = {
            "ts": _now_iso(),
            "kind": kind,
            "summary": summary,
            "detail": detail,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "status": status,
            "meta": meta or {},
        }
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _maybe_trim()
    except Exception:
        pass  # logging must never break the caller


def _maybe_trim() -> None:
    """Cap the file at _MAX_LINES once it exceeds _TRIM_TRIGGER (cheap, amortised)."""
    try:
        if not _LOG_PATH.exists():
            return
        with _LOG_PATH.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) <= _TRIM_TRIGGER:
            return
        with _LOG_PATH.open("w", encoding="utf-8") as fh:
            fh.writelines(lines[-_MAX_LINES:])
    except Exception:
        pass


def read_events(limit: int = 200, kinds: list[str] | None = None) -> list[dict]:
    """
    Return the most recent events, newest first. Optionally filter by kind.
    Corrupt lines are skipped rather than failing the whole read.
    """
    try:
        if not _LOG_PATH.exists():
            return []
        with _LOG_PATH.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []

    kindset = set(kinds) if kinds else None
    out: list[dict] = []
    # Walk newest-first so we can stop as soon as we have `limit`.
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if kindset and entry.get("kind") not in kindset:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out
