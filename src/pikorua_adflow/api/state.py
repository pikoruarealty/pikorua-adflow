"""
Shared, process-wide campaign run registry.

This is the single source of truth for `_runs` — the dict of every campaign run
this server knows about — and the lock guarding it. Routes and services import
`RUNS`, `RUNS_LOCK`, and `save_runs()` from here so there is exactly one registry.

Persistence semantics are preserved verbatim from the original monolith:
  * `runs.json` survives restarts.
  * Any run still marked running/queued at load time is flipped to "failed"
    (the server clearly died mid-run), so the UI never shows a phantom in-progress run.
"""

from __future__ import annotations

import json
import threading

from .config import RUNS_PATH


def load_runs() -> dict[str, dict]:
    if not RUNS_PATH.exists():
        return {}
    try:
        data = json.loads(RUNS_PATH.read_text(encoding="utf-8"))
        for run in data.values():
            if run.get("status", "").startswith("running_") or run.get("status") == "queued":
                run["status"] = "failed"
                run["error"] = "Server restarted while run was in progress."
        return data
    except Exception:
        return {}


# RLock: reentrant so the pipeline can acquire inside nested helpers without deadlock.
# Guards every read-then-write and every json.dumps(RUNS) that must see a consistent snapshot.
RUNS_LOCK = threading.RLock()
RUNS: dict[str, dict] = load_runs()


def save_runs() -> None:
    RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_LOCK:
        payload = json.dumps(RUNS, indent=2, default=str)
    RUNS_PATH.write_text(payload, encoding="utf-8")
