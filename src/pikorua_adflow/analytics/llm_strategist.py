"""
llm_strategist.py — Tier-3 AutoOptimiser: LLM advisory brain.

Reads the same inputs the deterministic ladder uses (7d/30d Meta insights,
CRM summary, rung outputs, settled calibration outcomes) and returns structured
advice the ladder structurally cannot produce:

  • Plain-English explanation of *why* each campaign is in its current state
  • Anomalies no rung covers (e.g. spend-impressions mismatch, unusual CTR
    patterns, campaigns spending heavily but not generating leads in any rung)
  • Proposed new candidate rules for human review
  • Structured suggestions tagged ``safe`` (auto-apply through Tier-1) or
    ``risky`` (surface for human approval on the /autooptimiser page)

Autonomy contract (mirrors the user's chosen split):
  safe  → audience wiring, threshold recalibration nudges, dayparting signals
  risky → anything touching budget or pause decisions

Transport
---------
Uses litellm — the same library already wired in main.py — routed through
OpenRouter with the existing OPENROUTER_API_KEY.  No new API key required.

Models (OpenRouter prefixed):
  Daily pass  : openrouter/anthropic/claude-sonnet-4-5   (strong structured reasoning)
  Weekly deep : openrouter/anthropic/claude-opus-4-5     (reserved for new-rule generation)

Override either via AO_STRATEGIST_DAILY_MODEL / AO_STRATEGIST_WEEKLY_MODEL env vars.
Fallback to openrouter/openai/gpt-4o if the Claude models are unavailable.

Prompt caching
--------------
OpenRouter passes the ``cache_control`` block through to Anthropic when the
model is a Claude model.  The static system prompt is sent with
``cache_control: {"type": "ephemeral"}`` so repeat calls within the 5-minute
TTL pay ~0.1× the input token cost.  Cache hit tokens are logged.

Cost estimate (via OpenRouter): ~$4/month at daily Sonnet + weekly Opus across
~15 campaigns, ~20K input / 3K output per run.

State
-----
Persists last run timestamp and weekly deep-review schedule in
outputs/strategist_state.json.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Model constants ───────────────────────────────────────────────────────────
_DAILY_MODEL  = os.getenv("AO_STRATEGIST_DAILY_MODEL",
                           "openrouter/anthropic/claude-sonnet-4-5")
_WEEKLY_MODEL = os.getenv("AO_STRATEGIST_WEEKLY_MODEL",
                           "openrouter/anthropic/claude-opus-4-5")
_MAX_TOKENS   = int(os.getenv("AO_STRATEGIST_MAX_TOKENS", "2048"))

# ── State path ────────────────────────────────────────────────────────────────
_STATE_PATH = Path(__file__).resolve().parents[4] / "outputs" / "strategist_state.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_daily": None, "last_weekly": None}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    except OSError:
        pass


def _is_weekly_due(state: dict) -> bool:
    """True if we haven't run a weekly deep review in the last 6 days."""
    last = state.get("last_weekly")
    if not last:
        return True
    try:
        age = _now() - datetime.fromisoformat(last)
        return age > timedelta(days=6)
    except Exception:
        return True


# ── Static system prompt (cached on first call via cache_control) ─────────────
_SYSTEM_PROMPT = """You are the AutoOptimiser strategist for Pikorua Realty — a luxury real estate
advisory firm in Ahmedabad, India (also active in Mumbai, Dubai, Goa, Alibaug).

Your role: analyse live Meta Ads campaign data + CRM lead quality and return concise,
structured JSON. You are the *reasoning layer* — you advise, you never act directly.
All actions you suggest are executed by the deterministic Tier-1 ladder.

## Account context
- Currency: INR (₹). Meta API returns USD; already converted in the data you receive.
- Target buyers: HNIs (net worth ₹5 Cr+), NRIs (UAE/UK/US), 35–60 years, considered buyers.
- Best-ever CPL benchmark: ₹85. Typical good CPL: ₹85–200. Bleeding: >₹400.
- Campaigns are CLIENTELE-SCOPED — bungalow and apartment audiences are different people.

## The deterministic ladder (Tier 1) — what already fires automatically
Rung 0  (AUTO)   — pause losing variant, boost winner budget
Rung 1  (APPROVE) — geo trim/add based on CRM signals
Rung 2  (AUTO)   — wire CRM bad-lead exclusion audience
Rung 3  (AUTO)   — add CRM lookalike audience
Rung 4  (AUTO)   — enable Advantage+ on audience saturation
Rung 5  (APPROVE) — add NRI countries (UAE/UK/US/SG)
Rung 6  (APPROVE) — broaden geo radius by 15 km
Rung 7  (APPROVE) — add CRM-proven interest targeting
Rung 8  (APPROVE) — refresh creative when CTR < 0.8%
Rung 9  (APPROVE) — reduce daily budget on saturation
Rung 9.5(APPROVE) — dayparting from lead-arrival timing
Rung 10 (APPROVE) — pause as last resort

## Your job
Catch what the rungs CANNOT:
- Multi-campaign patterns (e.g. all bungalow campaigns bleeding simultaneously)
- Anomalies (CPL spiking but frequency is low → might be audience mismatch, not fatigue)
- Plain-English *why* for each campaign's current state (non-technical, for the client)
- Proposed new candidate rules (structured, for human review)
- Suggestions: structured actions tagged safe or risky

## Risk classification (STRICT)
safe  → audience wiring (exclusion, lookalike), threshold recalibration nudges,
        dayparting recommendation, interest suggestions
risky → anything touching daily budget amount, pausing campaigns or ads

## Output format (return ONLY valid JSON, no markdown fences)
{
  "explanations": [
    {
      "campaign_name": "string",
      "state": "bleeding|okay|winning|idle",
      "plain_why": "one plain-English sentence explaining the root cause",
      "recommended_focus": "one sentence on the single most valuable next action"
    }
  ],
  "anomalies": [
    {
      "title": "short anomaly title",
      "detail": "plain English — what's unusual and why it matters",
      "severity": "high|medium|low",
      "campaign_names": ["list", "of", "affected", "campaigns"]
    }
  ],
  "suggestions": [
    {
      "title": "short action title",
      "detail": "plain English rationale",
      "risk": "safe|risky",
      "fix": {
        "action": "string matching a Tier-1 action name",
        "campaign_id": "string",
        "fix_type": "string",
        "params": {}
      }
    }
  ],
  "proposed_rules": [
    {
      "title": "candidate rule title",
      "condition": "plain English: when this condition is met",
      "action": "plain English: do this",
      "rationale": "why this rule would help"
    }
  ],
  "model_used": "string",
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0
  }
}"""


def _build_user_message(evals: list[dict], crm_report: dict,
                        settled_outcomes: list[dict], deep: bool) -> str:
    """Assemble the per-run user message with live campaign data."""
    camps_summary = []
    for ev in evals:
        d7 = ev.get("metrics", {}).get("d7", {})
        d30 = ev.get("metrics", {}).get("d30", {})
        verdict = ev.get("verdict", {})
        quality = ev.get("quality", {})
        fixes = ev.get("fixes", [])
        camps_summary.append({
            "campaign_name": ev.get("campaign_name"),
            "clientele_type": ev.get("clientele_type"),
            "verdict": verdict.get("state"),
            "d7": {
                "cpl": d7.get("cpl"),
                "ctr": d7.get("ctr"),
                "frequency": d7.get("frequency"),
                "spend": round(d7.get("spend", 0), 0),
                "leads": d7.get("leads"),
                "cpl_rising": ev.get("metrics", {}).get("cpl_rising"),
            },
            "d30_cpl": d30.get("cpl"),
            "quality_metric": quality.get("metric_used"),
            "quality_value": quality.get("value"),
            "quality_building": quality.get("building"),
            "pending_ladder_fixes": [f.get("fix_type") for f in fixes],
        })

    crm_snapshot = {
        "total_leads": crm_report.get("total_leads"),
        "quality_rate": crm_report.get("quality_rate"),
        "top_cities": crm_report.get("top_cities", [])[:5],
        "top_industries": crm_report.get("top_industries", [])[:3],
    } if crm_report else {}

    settled_summary = [
        {
            "basis": r.get("basis"),
            "action": r.get("action"),
            "actual_pct": r.get("actual_pct"),
            "predicted_pct": r.get("predicted_pct"),
            "prediction_error_pp": r.get("prediction_error_pp"),
        }
        for r in (settled_outcomes or [])
        if r.get("actual_pct") is not None
    ]

    data = {
        "run_timestamp_ist": _now().isoformat(),
        "campaigns": camps_summary,
        "crm_snapshot": crm_snapshot,
        "recently_settled_outcomes": settled_summary,
    }

    task = (
        "Perform a DEEP strategic review. Include proposed_rules with at least 2 "
        "concrete candidate rules derived from the data patterns."
        if deep else
        "Perform a daily pass. Focus on explanations and anomalies. "
        "Propose rules only if a clear pattern is evident."
    )

    return f"{task}\n\nData:\n{json.dumps(data, ensure_ascii=False, indent=2)}"


def _call_llm(system_prompt: str, user_msg: str, model: str) -> dict:
    """
    Call the LLM through litellm → OpenRouter using the existing OPENROUTER_API_KEY.

    The system prompt is sent with ``cache_control: ephemeral`` so OpenRouter
    passes it through to Anthropic's prompt cache on Claude models — repeat
    calls within the 5-minute TTL pay ~0.1× the input token cost.

    Returns the parsed JSON result dict, or {} on any failure.
    """
    # litellm is already imported and configured in main.py startup.
    # We import lazily here so this module is safe to import before the app boots.
    try:
        import litellm
    except ImportError:
        logger.error("litellm not installed — cannot run strategist.")
        return {}

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        logger.warning("OPENROUTER_API_KEY not set — strategist skipped.")
        return {}

    # Build messages with cache_control on the system block.
    # litellm passes this through to OpenRouter → Anthropic for Claude models.
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system_prompt,
                    # Prompt caching: ~0.1× cost on repeat calls within 5 min TTL.
                    # Silently ignored by non-Claude models (litellm.drop_params=True).
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {"role": "user", "content": user_msg},
    ]

    try:
        response = litellm.completion(
            model=model,
            messages=messages,
            max_tokens=_MAX_TOKENS,
            api_key=openrouter_key,
            api_base="https://openrouter.ai/api/v1",
            temperature=0.2,   # low temp for structured JSON
        )
    except Exception as exc:
        logger.error("LLM strategist call failed [%s]: %s", model, exc)
        return {}

    raw_text = (response.choices[0].message.content or "").strip()
    usage = response.usage or {}

    # litellm exposes usage as object attrs or dict-like depending on version.
    def _u(key: str, fallback: int = 0) -> int:
        try:
            return int(getattr(usage, key, None) or usage.get(key, fallback))
        except Exception:
            return fallback

    usage_dict = {
        "input_tokens":                  _u("prompt_tokens"),
        "output_tokens":                 _u("completion_tokens"),
        # OpenRouter/Anthropic cache tokens — present when caching fires.
        "cache_read_input_tokens":       _u("cache_read_input_tokens"),
        "cache_creation_input_tokens":   _u("cache_creation_input_tokens"),
    }

    logger.info(
        "Strategist [%s] tokens — in: %d, out: %d, cache_read: %d, cache_created: %d",
        model,
        usage_dict["input_tokens"],
        usage_dict["output_tokens"],
        usage_dict["cache_read_input_tokens"],
        usage_dict["cache_creation_input_tokens"],
    )

    # Parse structured JSON
    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                logger.error("Strategist response not valid JSON: %s", raw_text[:300])
                return {}
        else:
            logger.error("No JSON in strategist response: %s", raw_text[:300])
            return {}

    result["model_used"] = model
    result["usage"] = usage_dict
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def run_daily_pass(evals: list[dict], crm_report: dict,
                   settled_outcomes: list[dict]) -> dict:
    """
    Run the daily (or weekly) strategist pass via OpenRouter + litellm.

    Automatically upgrades to a weekly deep review when due (> 6 days since last).
    Returns the structured JSON result, or {} if OPENROUTER_API_KEY is absent.
    All exceptions are caught — never raises.
    """
    try:
        state = _load_state()
        deep = _is_weekly_due(state)
        model = _WEEKLY_MODEL if deep else _DAILY_MODEL

        user_msg = _build_user_message(evals, crm_report, settled_outcomes, deep=deep)
        result = _call_llm(_SYSTEM_PROMPT, user_msg, model)

        if result:
            now_iso = _now().isoformat()
            state["last_daily"] = now_iso
            if deep:
                state["last_weekly"] = now_iso
            _save_state(state)

        result["pass_type"] = "weekly_deep" if deep else "daily"
        return result

    except Exception as exc:
        logger.error("run_daily_pass failed unexpectedly: %s", exc)
        return {}
