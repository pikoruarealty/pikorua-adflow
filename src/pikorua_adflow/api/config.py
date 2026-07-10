"""
Centralized configuration for the Pikorua AdFlow API.

Every path, rate, and timezone constant the backend depends on lives here so the
rest of the codebase never reconstructs `Path(__file__).parent.parent...` by hand.

Paths are absolute (anchored at the repo root) and therefore independent of the
current working directory. NOTE: the CrewAI pipeline still `os.chdir()`s to the
repo root in `campaign_service._run_pipeline`, because the crews write their
`output_file:` artifacts relative to CWD — that behavior is preserved.
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

# ── Anchors ──────────────────────────────────────────────────────────────────
# config.py lives at src/pikorua_adflow/api/config.py
#   parents[0] = api, [1] = pikorua_adflow, [2] = src, [3] = repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

OUTPUT_DIR = REPO_ROOT / "outputs"

# API package-local dirs for the new frontend
API_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = API_DIR / "templates"
STATIC_DIR = API_DIR / "static"

# ── Persistent files in outputs/ ─────────────────────────────────────────────
RUNS_PATH = OUTPUT_DIR / "runs.json"
USERS_DB_PATH = OUTPUT_DIR / "users.db"
BRAND_LOGO_PATH = OUTPUT_DIR / "brand_logo.png"
REFERENCE_IMAGES_DIR = OUTPUT_DIR / "reference_images"
AUDIENCES_REGISTRY_PATH = OUTPUT_DIR / "meta_audiences_registry.json"
INSIGHTS_PATH = OUTPUT_DIR / "crm_strategic_insights.json"
AUTOOPTIMISER_CACHE_PATH = OUTPUT_DIR / "autooptimiser_data_cache.json"

# Per-run crew artifacts (written relative to CWD by the crews, read from here)
TREND_HOOKS_PATH = OUTPUT_DIR / "trend_hooks.md"
COPY_SCORECARD_PATH = OUTPUT_DIR / "copy_scorecard.md"
TARGETING_BRIEF_PATH = OUTPUT_DIR / "targeting_brief.md"

# Brand/reference example imagery shipped with the repo
LOGO_DIR = STATIC_DIR

# ── Currency ─────────────────────────────────────────────────────────────────
# Meta Ads insights are returned in USD; the portal shows INR everywhere.
# Update this single constant if the working rate changes.
USD_TO_INR = 84

# ── Timezone ─────────────────────────────────────────────────────────────────
# All timestamps shown in the portal are in IST. Never display bare UTC / "Z".
# IST is a fixed UTC+5:30 offset with no DST, so a fixed-offset tzinfo is exact and
# avoids depending on the OS tz database (absent on Windows without the tzdata pkg).
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# ── Cache / freshness windows ────────────────────────────────────────────────
CRM_CACHE_TTL_SECS = 4 * 60 * 60        # CRM report cache
INSIGHTS_TTL_SECS = 4 * 60 * 60         # strategic insights cache
TREND_TTL_SECONDS = 8 * 60 * 60         # trend-hook reuse window in the pipeline
AUTOOPTIMISER_CACHE_TTL_SECS = 30 * 60   # AutoOptimiser evaluation pass cache

# ── AutoOptimiser — Tunables ──────────────────────────────────────────────────
# Seeded from hand-tuned values (2026-06-22 account snapshot).
# Override via environment variables at deploy time.
# The Tier-2 learning loop (optimization_tracker) will self-calibrate these
# as settled outcomes accumulate — do NOT change them manually once live data flows.
AO_BENCHMARK_CPL     = int(os.getenv("AO_BENCHMARK_CPL",    "85"))    # best-ever CPL anchor (₹)
AO_FREQ_SATURATED    = float(os.getenv("AO_FREQ_SATURATED",  "3.0"))  # audience fatigue threshold
AO_FREQ_EXHAUSTED    = float(os.getenv("AO_FREQ_EXHAUSTED",  "5.0"))  # pause-consideration threshold
AO_CPL_CEILING       = int(os.getenv("AO_CPL_CEILING",       "500"))  # bleeding CPL (₹)
AO_CPL_RISING_RATIO  = float(os.getenv("AO_CPL_RISING_RATIO","1.3"))  # 7d/30d ratio flagging deterioration
AO_QUALITY_LEAD_MIN  = int(os.getenv("AO_QUALITY_LEAD_MIN",  "5"))    # min quality leads to trust quality-CPL
AO_COOLDOWN_DAYS     = int(os.getenv("AO_COOLDOWN_DAYS",     "5"))    # days between stacking fixes
