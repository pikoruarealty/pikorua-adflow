"""
Turn raw API errors into something the marketing operator can read — and stash the
full technical detail in a log file for the developer.

Two audiences:
  - operator: a plain-English sentence + whether it's something they can fix
    themselves (budget, retry, a declaration) vs. a setup/technical issue.
  - developer: the complete raw error, timestamped, in outputs/error_log.txt.

Meta is the easy case: its API already returns `error_user_title` / `error_user_msg`
written for advertisers, so we surface those verbatim. Everything else falls back to
a small pattern table, then to a generic "logged for your developer" message.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re

_LOG_PATH = pathlib.Path("outputs") / "error_log.txt"

# Meta error subcodes that are setup/technical (developer must act), not operator-fixable.
_DEV_SUBCODES = {33, 190, 200, 294, 2635}

# (regex, friendly message, operator_fixable) — checked in order when there's no
# Meta error_user_msg to lean on.
_PATTERNS: list[tuple[str, str, bool]] = [
    (r"Missing required env vars|not set\b",
     "The connection to Meta isn't fully set up — a required setting is missing. "
     "This is a one-time setup step for your developer.", False),
    (r"access token|\"code\":\s*190|session has expired",
     "Your Meta login has expired. Your developer needs to refresh the access token.", False),
    (r"\(#?33\)|error_subcode\"?:\s*33|do not have permission|cannot access",
     "This ad account or page can't be reached with the current Meta login. "
     "Your developer needs to check the account permissions.", False),
    (r"1010|rate.?limit|too many requests|\"code\":\s*(429|613)",
     "The image service is busy right now. Wait a minute and try generating again.", True),
    (r"No image service is connected",
     "No image service is connected yet. Your developer needs to add an Ideogram API key.", False),
    (r"Ideogram image request failed.*40[13]|invalid api key|unauthorized",
     "The image service rejected the request — its API key may be wrong or out of credits. "
     "Your developer can check the Ideogram account.", False),
    (r"daily budget|minimum budget|spend cap|budget.*(low|small)",
     "The daily budget is too low for this campaign. Try increasing it and publish again.", True),
    (r"timed out|timeout|connection reset|temporarily unavailable|\"is_transient\":\s*true",
     "The service didn't respond in time. This is usually temporary — try again in a moment.", True),
    (r"singapore_universal|3858550|singapore.*universal|universal.*declaration",
     "Singapore requires a compliance declaration for all ads targeting that location. "
     "This is now handled automatically — please try publishing again.", True),
    (r"All targeted locations require regulatory",
     "All locations in this campaign require compliance declarations that must be completed "
     "in Meta Ads Manager before publishing. Contact your developer for the next step.", False),
]

_GENERIC = ("Something went wrong and we couldn't finish this step. The technical "
            "details have been saved to a log for your developer.")


def log_error(context: str, detail: str) -> None:
    """Append a timestamped raw error to outputs/error_log.txt. Never raises."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] {context}\n{detail}\n{'-' * 60}\n")
    except OSError:
        pass


def humanize(raw: str | Exception) -> dict:
    """
    Return {"message": str, "fixable": bool, "raw": str}.

    `fixable` = True means the operator can likely resolve it themselves (adjust a
    setting, retry); False means it's a setup/technical issue for the developer.
    """
    raw_str = str(raw)

    # 1) Meta hands us an advertiser-facing message — prefer it verbatim.
    # Scan for all JSON objects in the string (the raw error may embed nested JSON strings).
    try:
        start = raw_str.find("{")
        if start != -1:
            # Try parsing from the first `{`; if that fails, try from the second `{` (handles
            # cases where the outer wrapper `{"error":{...}}` is embedded in a RuntimeError string).
            for attempt_start in [start, raw_str.find("{", start + 1)]:
                if attempt_start == -1:
                    break
                try:
                    err = json.loads(raw_str[attempt_start:])
                except ValueError:
                    continue
                err = err.get("error", err)
                user_msg = err.get("error_user_msg")
                if user_msg:
                    title = err.get("error_user_title") or ""
                    subcode = err.get("error_subcode")
                    blob = f"{title} {user_msg}".lower()
                    is_dev = subcode in _DEV_SUBCODES or "permission" in blob or "access token" in blob
                    msg = f"{title} — {user_msg}" if title and title.lower() not in user_msg.lower() else user_msg
                    return {"message": msg, "fixable": not is_dev, "raw": raw_str}
                break
    except (ValueError, AttributeError):
        pass

    # 2) Pattern table for non-Meta / message-less errors.
    for pattern, friendly, fixable in _PATTERNS:
        if re.search(pattern, raw_str, re.IGNORECASE):
            return {"message": friendly, "fixable": fixable, "raw": raw_str}

    # 3) Unknown — generic message, full detail goes to the log.
    return {"message": _GENERIC, "fixable": False, "raw": raw_str}


def explain_and_log(context: str, raw: str | Exception) -> dict:
    """Convenience: log the raw error under `context` and return the humanized dict."""
    result = humanize(raw)
    log_error(context, result["raw"])
    return result
