"""
Optimization impact tracker — predict, measure, and learn.

Every time we apply an optimization to a live Meta ad we:
  1. PREDICT the impact on a metric (e.g. audience reach) with a simple
     heuristic, scaled by a *learned* calibration factor for that kind of action.
  2. MEASURE the actual impact right after applying.
  3. LEARN: compare predicted vs actual and nudge the calibration factor so the
     next prediction of the same kind lands closer to reality.

The heuristic is deliberately naive (e.g. "shrinking the city radius scales reach
by the area ratio"). It is usually wrong on its own — for a Pikorua audience the
NRI countries dominate reach, so a radius change barely moves the total. The
calibration factor is what turns that naive guess into an increasingly accurate
one as real outcomes accumulate.

State persists in `outputs/optimization_history.json`. Pure stdlib, no deps.

Calibration model: for each `basis` (heuristic family) we keep a multiplicative
correction `factor` such that  expected_multiplier = raw_multiplier * factor.
On settle we observe  ratio = actual_multiplier / raw_multiplier  and move the
factor toward it with an exponential moving average.
"""
from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, timezone

_PATH = pathlib.Path(__file__).resolve().parents[3] / "outputs" / "optimization_history.json"
_ALPHA = 0.4              # EMA weight for each new observation
_FACTOR_CLAMP = (0.02, 50.0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {"records": [], "calibration": {}}


def _load() -> dict:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        data.setdefault("records", [])
        data.setdefault("calibration", {})
        return data
    except Exception:
        return _empty()


def _save(state: dict) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def _clamp(x: float) -> float:
    return max(_FACTOR_CLAMP[0], min(_FACTOR_CLAMP[1], x))


def get_calibration(basis: str) -> tuple[float, int]:
    """Return (factor, n_samples) for a heuristic family. 1.0 / 0 if unseen."""
    c = _load()["calibration"].get(basis)
    if not c:
        return 1.0, 0
    return float(c.get("factor", 1.0)), int(c.get("n", 0))


def predict(basis: str, raw_multiplier: float, before_value: float | None) -> dict:
    """
    Apply the learned calibration to a raw heuristic multiplier.

    Returns a JSON-serialisable dict describing the expected change. When
    `before_value` is known the absolute `expected_after` is included; otherwise
    only the relative `expected_pct` is meaningful (e.g. lead counts pre-spend).
    """
    factor, n = get_calibration(basis)
    raw_multiplier = max(raw_multiplier, 0.0)
    expected_multiplier = _clamp(raw_multiplier * factor) if raw_multiplier else 0.0
    expected_after = (round(before_value * expected_multiplier)
                      if before_value not in (None, 0) else None)
    return {
        "basis": basis,
        "raw_multiplier": round(raw_multiplier, 4),
        "calibration_factor": round(factor, 4),
        "expected_multiplier": round(expected_multiplier, 4),
        "expected_after": expected_after,
        "expected_pct": round((expected_multiplier - 1) * 100, 1),
        "n_samples": n,
        "calibrated": n > 0,
    }


def open_record(*, run_id: str, variant: int, action: str, basis: str,
                metric: str, label: str, before: float | None,
                raw_multiplier: float, expected: dict) -> str:
    """Persist a pending prediction and return its id (settle it once measured)."""
    state = _load()
    rid = uuid.uuid4().hex[:12]
    state["records"].append({
        "id": rid, "run_id": run_id, "variant": variant, "action": action,
        "basis": basis, "metric": metric, "label": label,
        "before": before, "raw_multiplier": raw_multiplier,
        "predicted_after": expected.get("expected_after"),
        "predicted_pct": expected.get("expected_pct"),
        "calibration_factor_used": expected.get("calibration_factor"),
        "opened_at": _now(),
        "actual_after": None, "actual_pct": None,
        "prediction_error_pp": None, "settled_at": None,
    })
    _save(state)
    return rid


def settle(record_id: str, actual_after: float | None) -> dict | None:
    """
    Record the measured outcome for a prediction and update the calibration.

    Returns the settled record (with actual_pct + prediction_error_pp), or None
    if the record is unknown or the outcome can't be used (missing before value).
    """
    state = _load()
    rec = next((r for r in state["records"] if r["id"] == record_id), None)
    if rec is None:
        return None

    rec["actual_after"] = actual_after
    rec["settled_at"] = _now()

    before = rec.get("before")
    raw_mult = rec.get("raw_multiplier") or 0.0
    if before and actual_after is not None and before > 0 and raw_mult > 0:
        actual_multiplier = actual_after / before
        rec["actual_pct"] = round((actual_multiplier - 1) * 100, 1)
        if rec.get("predicted_pct") is not None:
            rec["prediction_error_pp"] = round(
                abs(rec["actual_pct"] - rec["predicted_pct"]), 1)

        # LEARN: nudge this basis' factor toward the observed correction.
        observed_ratio = actual_multiplier / raw_mult
        cal = state["calibration"].get(rec["basis"], {"factor": 1.0, "n": 0})
        n = int(cal.get("n", 0))
        if n == 0:
            new_factor = observed_ratio
        else:
            new_factor = (1 - _ALPHA) * float(cal["factor"]) + _ALPHA * observed_ratio
        state["calibration"][rec["basis"]] = {
            "factor": _clamp(new_factor), "n": n + 1, "updated_at": _now(),
            "last_observed_ratio": round(observed_ratio, 4),
        }

    _save(state)
    return rec


def history(run_id: str | None = None) -> dict:
    """Return records (optionally filtered to one run) plus the calibration table."""
    state = _load()
    recs = state["records"]
    if run_id:
        recs = [r for r in recs if r.get("run_id") == run_id]
    # Accuracy summary across settled records that produced an error figure.
    settled = [r for r in recs if r.get("prediction_error_pp") is not None]
    avg_err = (round(sum(r["prediction_error_pp"] for r in settled) / len(settled), 1)
               if settled else None)
    return {
        "records": list(reversed(recs)),       # newest first
        "calibration": state["calibration"],
        "settled_count": len(settled),
        "avg_prediction_error_pp": avg_err,
    }
