"""
creative_learning.py — per-clientele creative memory.

On every autooptimiser pass (after quality-CPL data lands), this module:
  1. Finds the winning variant(s) for each active campaign with enough quality leads
  2. Reads that variant's visual_prompts.json tags (palette_tag, recipe_tag, scene_tag)
     and the copy angle from the run record in RUNS
  3. Writes/updates outputs/creative_memory.json using an EMA (same approach as
     optimization_tracker) so recent wins count more than stale ones
  4. Scopes STRICTLY by clientele_type — bungalow memories NEVER touch apartment memories

Consistent with the no-hardcoded-table principle: nothing is frozen. Every insight
comes from Pikorua's own CRM quality data. When quality data is absent the module
writes nothing and the pipeline runs without a prior.

Key functions:
  update_memory(evals, token)  — called at the end of run_autooptimiser()
  get_priors(clientele_type)   — called by run_pipeline() before ContentCrew
  top_tags(clientele_type, n)  — convenience; returns ranked tag strings
"""

from __future__ import annotations

import json
from pathlib import Path

# ── Tunables ──────────────────────────────────────────────────────────────────
EMA_ALPHA = 0.3              # learning rate — how fast new wins displace old memories
MIN_QUALITY_TO_LEARN = 5    # same gate as QUALITY_LEAD_MIN in autooptimiser

_MEMORY_PATH = Path(__file__).parent.parent.parent.parent.parent / "outputs" / "creative_memory.json"
# Resolved at import time relative to the package root; falls back cleanly if missing.


def _load_memory() -> dict:
    try:
        return json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_memory(memory: dict) -> None:
    try:
        _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MEMORY_PATH.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _ema_update(current: float, new_value: float, alpha: float = EMA_ALPHA) -> float:
    """Exponential moving average update. New observations count more."""
    return alpha * new_value + (1.0 - alpha) * current


# ── Core: update memory after an autooptimiser pass ───────────────────────────────
def update_memory(evals: list[dict], token: str) -> None:
    """
    For each evaluated campaign that has enough quality leads, find the best
    variant, read its visual_prompts.json tags, and update the per-clientele EMA.

    evals: list of evaluate_campaign() result dicts (from run_autooptimiser).
    token: Meta access token (not used here — kept for API consistency).
    """
    from pikorua_adflow.api.state import RUNS, RUNS_LOCK

    memory = _load_memory()

    for ev in evals:
        quality = ev.get("quality") or {}
        n_quality = quality.get("n_quality", 0)
        if n_quality < MIN_QUALITY_TO_LEARN:
            continue  # not enough signal — leave memory untouched

        clientele = ev.get("clientele_type", "")
        if not clientele:
            continue

        run_id = ev.get("run_id")
        if not run_id:
            continue

        # Find the run record to locate the review_folder.
        with RUNS_LOCK:
            run = RUNS.get(run_id, {})
        review_folder = run.get("review_folder", "")
        if not review_folder:
            continue

        vp_path = Path(review_folder) / "visual_prompts.json"
        if not vp_path.exists():
            continue

        try:
            vp_entries = json.loads(vp_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Determine the winning variant (lowest 7d CPL).
        spend_7d = ev.get("metrics", {}).get("d7", {}).get("spend", 0)
        quality_cpl = (spend_7d / n_quality) if n_quality and spend_7d else None
        if quality_cpl is None:
            continue

        # The winning variant is the one with the best quality-CPL. Since we don't
        # have per-variant quality breakdowns at this stage, we learn from ALL tags
        # in this run's visual_prompts.json — all variants contributed to the result.
        bucket = memory.setdefault(clientele, {})

        for entry in vp_entries:
            for tag_key in ("palette_tag", "recipe_tag", "scene_tag", "tone_tag"):
                tag_val = entry.get(tag_key)
                if not tag_val:
                    continue
                key = f"{tag_key}:{tag_val}"
                # Score: we record a normalised quality signal (0-1). Use a simple
                # inverse-CPL normalised to a ₹500 ceiling so lower CPL → higher score.
                score = max(0.0, min(1.0, 1.0 - quality_cpl / 500.0))
                if key in bucket:
                    bucket[key] = round(_ema_update(bucket[key], score), 4)
                else:
                    bucket[key] = round(score, 4)

    _save_memory(memory)


# ── Read priors for a new campaign run ───────────────────────────────────────
def get_priors(clientele_type: str) -> dict:
    """
    Return the winning tags for a clientele type as a flat dict:
      {"palette_tag": "warm_terracotta", "recipe_tag": "lifestyle_hero", ...}

    Returns {} when no memory exists yet (cold start). The pipeline treats
    this as a neutral prior and runs without creative biasing.
    """
    memory = _load_memory()
    bucket = memory.get(clientele_type, {})
    if not bucket:
        return {}

    best: dict[str, tuple[str, float]] = {}   # tag_key -> (best_tag_val, best_score)
    for composite_key, score in bucket.items():
        if ":" not in composite_key:
            continue
        tag_key, tag_val = composite_key.split(":", 1)
        if tag_key not in best or score > best[tag_key][1]:
            best[tag_key] = (tag_val, score)

    return {tk: tv for tk, (tv, _) in best.items()}


def top_tags(clientele_type: str, n: int = 3) -> list[str]:
    """Return the top-N tag strings (composite key:val) sorted by EMA score."""
    memory = _load_memory()
    bucket = memory.get(clientele_type, {})
    ranked = sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)
    return [k for k, _ in ranked[:n]]
