"""
CRM services: the cached analytics report, Claude-generated strategic insights,
CRM-derived optimisation signals, and Meta lead-webhook ingestion into Supabase.

The structured analytics themselves live in `pikorua_adflow.analytics.crm_analytics`
(unchanged); this module wraps them with caching and the LLM insight layer that the
portal consumes.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from ..config import CRM_CACHE_TTL_SECS, INSIGHTS_PATH, INSIGHTS_TTL_SECS

# In-memory cache so we don't re-fetch Supabase + recompute on every request.
_crm_cache: dict = {"data": None, "fetched_at": None, "source": ""}


def invalidate_crm_cache() -> None:
    """Drop the cached report (called after a new lead is ingested)."""
    _crm_cache["data"] = None


def crm_report(force: bool = False) -> dict:
    """Return the full CRM analytics report, served from cache unless stale."""
    from pikorua_adflow.analytics import crm_analytics

    now = datetime.now(timezone.utc)
    fetched = _crm_cache.get("fetched_at")
    fresh = (
        not force
        and _crm_cache.get("data") is not None
        and fetched is not None
        and (now - fetched).total_seconds() < CRM_CACHE_TTL_SECS
    )
    if fresh:
        return _crm_cache["data"]

    leads, source = crm_analytics.get_leads()
    report = crm_analytics.full_report(leads)
    report["source"] = source
    _crm_cache.update({"data": report, "fetched_at": now, "source": source})
    return report


def insights_cache_valid() -> bool:
    if not INSIGHTS_PATH.exists():
        return False
    try:
        data = json.loads(INSIGHTS_PATH.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data.get("generated_at", "2000-01-01"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() < INSIGHTS_TTL_SECS
    except Exception:
        return False


def build_crm_summary_text(rep: dict) -> str:
    """Condense full_report() into a compact text brief for the Claude prompt."""
    lines = [f"Total leads: {rep.get('total_leads', 0)}"]
    trend = rep.get("volume_trend", {})
    if trend.get("peak_month"):
        lines.append(f"Peak month: {trend['peak_month']} ({trend.get('peak_count', 0)} leads)")
    if trend.get("growth_rate") is not None:
        lines.append(f"Recent growth: {trend['growth_rate']:+g}% vs prior month")

    geo = rep.get("geography", {})
    top_cities = geo.get("top_cities", [])[:5]
    if top_cities:
        lines.append("Top cities: " + ", ".join(f"{c} ({n})" for c, n in top_cities))

    seg = rep.get("budget_segments", {})
    seg_parts = []
    for b in ["<5Cr", "5–7Cr", "7–10Cr", "10Cr+", "Unknown"]:
        d = seg.get(b, {})
        if d.get("count"):
            seg_parts.append(f"{b}: {d['count']} leads ({d.get('pct', 0):g}%, quality {d.get('avg_quality_score', 0):g}%)")
    if seg_parts:
        lines.append("Budget segments:\n  " + "\n  ".join(seg_parts))

    profs = rep.get("professions", {}).get("industries", [])[:8]
    if profs:
        prof_parts = [f"{p['industry']}: {p['count']} ({p.get('quality_rate', 0):g}% quality)" for p in profs]
        lines.append("Top professions:\n  " + "\n  ".join(prof_parts))

    funnel = rep.get("lead_quality", {}).get("stages", [])
    if funnel:
        funnel_parts = [f"{s['stage']}: {s['count']}" for s in funnel]
        lines.append("Lead funnel: " + " → ".join(funnel_parts))

    attr = rep.get("attribution", {})
    attr_parts = []
    for name, d in list(attr.items())[:8]:
        attr_parts.append(f"{name}: {d['count']} leads, quality {d.get('quality_rate', 0):g}%")
    if attr_parts:
        lines.append("Campaign attribution:\n  " + "\n  ".join(attr_parts))

    profiles = rep.get("top_profiles", [])[:5]
    if profiles:
        prof_lines = []
        for p in profiles:
            pr = p.get("profile", {})
            prof_lines.append(
                f"{pr.get('industry','?')}, ₹{pr.get('budget','?')}, {pr.get('city','?')}: "
                f"{p['count']} leads, {p.get('quality_rate', 0):g}% quality"
            )
        lines.append("Top converting profiles:\n  " + "\n  ".join(prof_lines))

    return "\n".join(lines)


def _insights_path_for(run_id: str = "") -> "Path":
    from ..config import OUTPUT_DIR
    if run_id:
        return OUTPUT_DIR / f"crm_strategic_insights_{run_id}.json"
    return INSIGHTS_PATH


def _insights_cache_valid_for(run_id: str = "") -> bool:
    path = _insights_path_for(run_id)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data.get("generated_at", "2000-01-01"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() < INSIGHTS_TTL_SECS
    except Exception:
        return False


def strategic_insights(force: bool = False, run_id: str = "",
                       campaign_brief: dict | None = None) -> dict:
    """5–8 visionary CRM insights from Claude via OpenRouter, cached 4h to disk.

    When run_id is supplied the cache is per-run and the prompt includes campaign
    context so Claude generates insights relevant to that specific property/audience.
    """
    cache_path = _insights_path_for(run_id)
    if not force and _insights_cache_valid_for(run_id):
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    rep = crm_report()
    if not rep or rep.get("total_leads", 0) == 0:
        return {"error": "No CRM data available. Load leads first.", "insights": []}

    summary = build_crm_summary_text(rep)

    campaign_context_block = ""
    if campaign_brief:
        b = campaign_brief
        campaign_context_block = (
            f"\n\nACTIVE CAMPAIGN BEING OPTIMISED:\n"
            f"Property: {b.get('property_name', 'Unknown')} "
            f"({b.get('property_type', '')}) in {b.get('city', '')} — "
            f"{b.get('locality', '')}\n"
            f"Price: ₹{b.get('price_cr', '?')} Cr | "
            f"Daily budget: ₹{b.get('daily_budget_inr', '?')} | "
            f"Target buyer: {b.get('buyer_type', 'HNI/NRI')}\n"
            f"All campaign-scoped insights and params MUST be relevant to this "
            f"specific property, price tier, and audience — not generic CRM-wide advice."
        )

    system_prompt = (
        "You are a razor-sharp chief marketing strategist advising Pikorua Realty — "
        "a luxury real estate broker in India (₹2Cr+ properties, HNI and NRI buyers). "
        "Your job is to read CRM data cold and surface the non-obvious moves a Steve Jobs "
        "or Elon Musk would make: contrarian bets, segments to go all-in on, segments to cull, "
        "10× leverage ideas, and product-framing shifts that change how the brand is perceived. "
        "You think in first principles, not marketing platitudes."
    )

    user_prompt = f"""Here is the current CRM intelligence snapshot for Pikorua Realty:

{summary}{campaign_context_block}

CONTEXT YOU MUST FACTOR IN (do not surface these as insights — they are already known):
- Pikorua deliberately targets Ahmedabad. Geographic concentration there is intentional, not a risk.
- Pikorua sells ₹5Cr+ luxury properties. That budget segment dominating is expected, not an insight.
- The charts already show: budget distribution, city split, profession breakdown, and funnel stage counts. Do NOT restate any of these as standalone insights — the user can already see them.
- Any insight that simply describes what is visible in a single metric (e.g. "most leads are from Ahmedabad", "₹5Cr+ dominates") is useless. Reject it.

WHAT MAKES A REAL INSIGHT:
- A finding that requires crossing two or more dimensions (e.g. a profession segment that has high volume but zero quality conversion — that is a budget/targeting leak)
- An anomaly that contradicts expectations (e.g. a normally strong segment that is now underperforming)
- A comparison between segments that reveals a counter-intuitive gap (e.g. ₹7–10Cr vs ₹10Cr+ quality rates diverge unexpectedly)
- A funnel breakdown (e.g. why a specific stage has a cliff-drop)
- An absence that matters (e.g. a segment present in volume but absent in quality)

Give me 6–8 strategic insights split into two scopes:

- scope "campaign": 2–3 insights directly applicable to the Meta ad campaign (targeting or budget changes only). These will surface in the live campaign optimisation panel with one-click apply buttons.
- scope "strategic": 4–5 insights the business must act on physically — messaging rewrites, product positioning, channel mix, timing strategy, or operations/process fixes. These appear on the CRM dashboard as read-only intelligence.

Each insight must have ALL of these fields:
1. "title": punchy 3–6 word title (ALL CAPS)
2. "finding": the specific cross-dimensional or anomalous finding (1 sentence, must cite actual numbers from the data)
3. "action": the specific action it implies (1–2 sentences, written as a direct instruction)
4. "confidence": HIGH / MEDIUM / SPECULATIVE
5. "category": targeting | budget | messaging | product | channel | timing | process
6. "scope": "campaign" or "strategic"
7. "params": REQUIRED for scope "campaign" only — structured action parameters:
   - For targeting category: {{"add_interests": ["interest name", ...]}} to add Meta audience interests, OR {{"action": "add_nri"}} to expand to NRI countries, OR {{"action": "broaden_radius"}} to widen geo radius
   - For budget category: {{"change_pct": 20}} (positive = increase %, negative = decrease %)
   Omit "params" entirely for strategic scope.

Format as JSON array. Return ONLY the JSON array. No preamble, no markdown, no explanation.
[
  {{
    "title": "TITLE HERE",
    "finding": "The cross-dimensional or anomalous finding with specific numbers...",
    "action": "What to do about it...",
    "confidence": "HIGH|MEDIUM|SPECULATIVE",
    "category": "targeting|messaging|budget|product|channel|timing|process",
    "scope": "campaign|strategic",
    "params": {{}}
  }},
  ...
]"""

    or_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not or_key:
        return {"error": "OPENROUTER_API_KEY not set in .env", "insights": []}

    try:
        import urllib.request as _urlreq
        payload = json.dumps({
            "model": "anthropic/claude-sonnet-4-6",
            "max_tokens": 3000,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }).encode("utf-8")
        req = _urlreq.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {or_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://pikorua.in",
                "X-Title": "Pikorua CRM Strategic Insights",
            },
            method="POST",
        )
        with _urlreq.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        raw = body["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\n?```\s*$", "", raw)
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        raw = raw.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        insights = json.loads(raw)
    except Exception as exc:
        try:
            from pikorua_adflow.tools.errors import explain_and_log
            explain_and_log("CRM strategic insights", exc)
        except Exception:
            pass
        return {"error": str(exc), "insights": []}

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_leads": rep.get("total_leads", 0),
        "insights": insights,
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return result


def delete_insights_cache(run_id: str = "") -> None:
    for path in [INSIGHTS_PATH, _insights_path_for(run_id)] if run_id else [INSIGHTS_PATH]:
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass


def crm_optimisation_signals() -> list[dict]:
    """Account-level recommendations derived from CRM analytics (not Meta)."""
    signals: list[dict] = []
    try:
        rep = crm_report()
    except Exception:
        return signals
    if not rep or rep.get("total_leads", 0) == 0:
        return signals

    industries = rep.get("professions", {}).get("industries", [])
    weighted = {p["industry"]: p["count"] * p["quality_rate"] for p in industries}
    total_w = sum(weighted.values()) or 1
    for industry, w in sorted(weighted.items(), key=lambda kv: kv[1], reverse=True)[:1]:
        share = w / total_w
        if industry == "IT/Tech" and share > 0.30:
            signals.append({
                "source": "crm", "action": "targeting_interests",
                "label": "Add Technology & Software interests",
                "detail": f"IT professionals are {round(share*100)}% of your quality leads — "
                          "lean targeting toward them.",
                "severity": "info", "params": {"interests": ["Technology", "Software"]},
            })

    seg = {k: v for k, v in rep.get("budget_segments", {}).items() if v.get("count")}
    best_seg = max(seg.items(),
                   key=lambda kv: kv[1]["count"] * (kv[1]["avg_quality_score"] or 1),
                   default=(None, None))[0] if seg else None
    if best_seg and best_seg != "Unknown":
        signals.append({
            "source": "crm", "action": "note",
            "label": f"Speak to the ₹{best_seg} buyer in copy",
            "detail": f"Your best-converting budget segment is ₹{best_seg}. "
                      "Make sure the ad copy resonates with that price tier.",
            "severity": "info", "params": {},
        })

    for prof in rep.get("top_profiles", []):
        city = prof.get("profile", {}).get("city")
        if city and city != "Unknown":
            signals.append({
                "source": "crm", "action": "note",
                "label": f"Top profile converts in {city}",
                "detail": f"{prof['profile']['industry']}, ₹{prof['profile']['budget']}, "
                          f"{city} — {prof['quality_rate']:g}% quality across {prof['count']} leads. "
                          "Confirm this city is in your geo targeting.",
                "severity": "info", "params": {},
            })
            break

    attribution = rep.get("attribution", {})
    rates = [(name, d["quality_rate"], d["count"]) for name, d in attribution.items()
             if d["count"] >= 5]
    if len(rates) >= 2:
        rates.sort(key=lambda x: x[1], reverse=True)
        top, others = rates[0], rates[1:]
        avg_other = sum(r[1] for r in others) / len(others) if others else 0
        if avg_other and top[1] >= 2 * avg_other:
            signals.append({
                "source": "crm", "action": "note",
                "label": f"Campaign “{top[0]}” converts 2× better",
                "detail": f"{top[0]} runs at {top[1]:g}% quality vs {round(avg_other)}% average — "
                          "consider shifting budget toward it.",
                "severity": "info", "params": {},
            })
    return signals


# ── Meta lead-webhook ingestion ──────────────────────────────────────────────

def fetch_lead_fields(leadgen_id: str, token: str) -> dict:
    """Call Graph API to retrieve field_data for a lead form submission."""
    import urllib.request
    import urllib.parse
    params = urllib.parse.urlencode({
        "fields": "field_data,created_time,ad_name,campaign_name,form_id,ad_id,page_id",
        "access_token": token,
    })
    url = f"https://graph.facebook.com/v20.0/{leadgen_id}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def insert_lead_supabase(lead_data: dict) -> str | None:
    """Insert a lead into Supabase meta_leads and a stub lead_crm_details row."""
    import requests as _req

    creds_raw = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )
    if not creds_raw or not key:
        return None

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    resp = _req.post(f"{creds_raw}/rest/v1/meta_leads", headers=headers, json=lead_data, timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    lead_id = rows[0].get("id")
    if lead_id:
        _req.post(
            f"{creds_raw}/rest/v1/lead_crm_details",
            headers={**headers, "Prefer": "return=minimal"},
            json={"lead_id": lead_id},
            timeout=10,
        )
    return lead_id
