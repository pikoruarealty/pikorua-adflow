"""
Campaign copy services: parsing the crew's markdown output, the non-destructive
user-edit overlay, the two-stage CrewAI pipeline runner, and the structured
detail payload consumed by the campaign-detail page.

The user-edit overlay (edits.json / audience.json) sits beside the AI output and
is never overwritten, so every change is revertible. All read paths go through the
`effective_*` helpers, so an edit automatically flows into what gets published.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from ..config import COPY_SCORECARD_PATH, OUTPUT_DIR, REPO_ROOT, TARGETING_BRIEF_PATH, TREND_HOOKS_PATH, TREND_TTL_SECONDS
from ..models import CampaignBrief
from ..state import RUNS, RUNS_LOCK, save_runs
from . import image_service


# ── Markdown rendering for persona/targeting briefs ──────────────────────────

def md_to_html(text: str) -> str:
    """Lightweight markdown-to-HTML for persona/targeting briefs."""
    import re as _re

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def inline(s: str) -> str:
        s = esc(s)
        s = _re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', s)
        s = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = _re.sub(r'\*([^*]+?)\*', r'<em>\1</em>', s)
        s = _re.sub(r'`([^`]+?)`', r'<code>\1</code>', s)
        return s

    lines = text.strip().splitlines()
    out: list[str] = []
    i = 0
    in_ul = False
    in_table = False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    def close_table():
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    while i < len(lines):
        line = lines[i]
        hm = _re.match(r'^(#{1,6})\s+(.*)', line)
        if hm:
            close_ul(); close_table()
            lvl = len(hm.group(1))
            tag = "h3" if lvl <= 2 else "h4" if lvl == 3 else "h5"
            out.append(f'<{tag}>{inline(hm.group(2))}</{tag}>')
            i += 1
            continue
        if _re.match(r'^[-=]{3,}\s*$', line):
            close_ul(); close_table()
            i += 1
            continue
        if "|" in line and _re.match(r'^\s*\|', line):
            if _re.match(r'^[\s|:\-]+$', line):
                i += 1
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not in_table:
                close_ul()
                out.append('<table class="brief-table">')
                out.append('<tbody>')
                in_table = True
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            i += 1
            continue
        else:
            close_table()
        lm = _re.match(r'^(\s*)[-*]\s+(.*)', line)
        if lm:
            if not in_ul:
                out.append('<ul>')
                in_ul = True
            out.append(f'<li>{inline(lm.group(2))}</li>')
            i += 1
            continue
        if not line.strip():
            close_ul(); close_table()
            out.append("")
            i += 1
            continue
        close_ul(); close_table()
        out.append(f'<p>{inline(line)}</p>')
        i += 1

    close_ul(); close_table()
    return "\n".join(out)


# ── Copy cleaning & parsing ──────────────────────────────────────────────────

def clean_copy(text: str) -> str:
    """Strip LLM markdown artefacts and char/word-count annotations from copy."""
    text = re.sub(r'\*{1,3}|_{1,3}', '', text)
    text = re.sub(r'\[\s*\d+\s*(?:chars?|characters?|words?)\s*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\s*X\s*(?:chars?|characters?|words?)\s*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def parse_ad_copy(text: str) -> dict:
    """Parse ad_copy.md into {meta:{num:{headline,body}}, google, whatsapp, email}."""
    result = {"meta": {}, "google": "", "whatsapp": "", "email": ""}
    if not text:
        return result
    text2 = "\n" + text
    channel_patterns = [
        ("meta",     re.compile(r'write\s+meta', re.I)),
        ("google",   re.compile(r'write\s+google', re.I)),
        ("whatsapp", re.compile(r'write\s+whats?app', re.I)),
        ("email",    re.compile(r'write\s+e-?mail', re.I)),
        ("format",   re.compile(r'format\s+for\s+api', re.I)),
    ]
    boundaries = []
    for m in re.finditer(r'\n##\s+([^\n]+)\n', text2):
        header = m.group(1)
        for label, pat in channel_patterns:
            if pat.search(header):
                boundaries.append((m.start(), m.end(), label))
                break
    chunks: dict[str, str] = {}
    for i, (_, bstart, label) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text2)
        chunks[label] = text2[bstart:end].strip()
    result["meta"] = meta_from_prose(chunks.get("meta", ""))
    if not result["meta"]:
        result["meta"] = meta_from_format_json(chunks.get("format", ""))
    result["google"] = clean_google_copy(chunks.get("google", ""))
    result["whatsapp"] = chunks.get("whatsapp", "")
    result["email"] = chunks.get("email", "")
    return result


def clean_google_copy(text: str) -> str:
    """Extract only the final 3 headlines + 2 descriptions from Google-ads output."""
    if not text:
        return text

    def pick(label: str, n: int) -> list:
        out = []
        for k in range(1, n + 1):
            matches = re.findall(rf'(?im)^\s*{label}\s+{k}\s*:\s*(.+?)\s*$', text)
            if matches:
                val = matches[-1].strip().strip('"').strip()
                val = re.sub(r'\s*\(\s*\d+\s*chars?\s*\)\s*$', '', val).strip()
                out.append(f"{label} {k}: {val}")
        return out

    heads = pick("Headline", 3)
    descs = pick("Description", 2)
    if heads and descs:
        return "\n".join(heads + descs)
    return text.strip()


def google_copy_issues(text: str, city: str = "") -> list[dict]:
    """Validate Google Ads copy against Google's character limits and basic rules."""
    issues: list[dict] = []
    if not text:
        return issues
    headlines: list[str] = []
    for k in range(1, 4):
        m = re.search(rf'(?im)^headline\s+{k}\s*:\s*(.+?)\s*$', text)
        if m:
            val = m.group(1).strip()
            headlines.append(val)
            if len(val) > 30:
                issues.append({"line": f"Headline {k}", "issue": f"{len(val)} chars — Google limit is 30", "severity": "error"})
        else:
            issues.append({"line": f"Headline {k}", "issue": "Missing — Google Ads needs exactly 3 headlines", "severity": "error"})
    for k in range(1, 3):
        m = re.search(rf'(?im)^description\s+{k}\s*:\s*(.+?)\s*$', text)
        if m:
            val = m.group(1).strip()
            if len(val) > 90:
                issues.append({"line": f"Description {k}", "issue": f"{len(val)} chars — Google limit is 90", "severity": "error"})
        else:
            issues.append({"line": f"Description {k}", "issue": "Missing — Google Ads needs exactly 2 descriptions", "severity": "error"})
    seen: set[str] = set()
    for i, h in enumerate(headlines, start=1):
        norm = h.lower().strip()
        if norm in seen:
            issues.append({"line": f"Headline {i}", "issue": "Duplicate — identical to another headline; Google may discard it", "severity": "warning"})
        seen.add(norm)
    if city and headlines and not any(city.lower() in h.lower() for h in headlines):
        issues.append({"line": "Headlines", "issue": f"None mention '{city}' — including the city improves local relevance", "severity": "hint"})
    return issues


def meta_from_prose(meta_body: str) -> dict:
    """Parse Meta variant blocks (### Variant N / N.) into {num: {headline, body}}."""
    out: dict[int, dict] = {}
    if not meta_body:
        return out
    blocks = re.split(
        r'\n(?=(?:\*{0,4}|#{0,4})\s*(?:\d+\.|\bVariant\s+\d+\b))',
        meta_body, flags=re.IGNORECASE,
    )
    for block in blocks:
        block = block.strip()
        nm_n = re.match(r'(?:\*{0,4}|#{0,4})\s*(\d+)\.', block)
        nm_v = re.match(r'(?:\*{0,4}|#{0,4})\s*Variant\s+(\d+)', block, re.IGNORECASE)
        if nm_v:
            num = int(nm_v.group(1))
        elif nm_n:
            num = int(nm_n.group(1))
        else:
            continue
        hm = re.search(r'Headline:\s*\*{0,2}(.+?)\*{0,2}(?:\s*\[[\d\s\*]+chars\*{0,2}\])?\s*$', block, re.MULTILINE | re.IGNORECASE)
        bm = re.search(r'Body:\s*\*{0,2}(.+?)\*{0,2}(?:\s*\[[\d\s\*]+chars\*{0,2}\])?\s*$', block, re.MULTILINE | re.IGNORECASE)
        if hm or bm:
            out[num] = {
                "headline": hm.group(1).strip() if hm else "",
                "body": bm.group(1).strip() if bm else "",
            }
    return out


def meta_from_format_json(format_body: str) -> dict:
    """Fallback: pull headline/body from the Format-For-API JSON `ads` array."""
    import json as _json
    if not format_body:
        return {}
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', format_body, re.DOTALL)
    raw = m.group(1) if m else format_body
    raw = re.sub(r'//[^\n]*', '', raw)
    try:
        data = _json.loads(raw)
    except Exception:
        return {}
    out: dict[int, dict] = {}
    for i, ad in enumerate(data.get("ads", []), 1):
        headline = (ad.get("headline") or "").strip()
        body = (ad.get("body") or ad.get("primary_text") or "").strip()
        if headline or body:
            out[i] = {"headline": headline, "body": body}
    return out


def parse_scorecard(text: str) -> list:
    """Parse copy_scorecard.md into a list of variant dicts."""
    variants = []
    blocks = re.split(r'\n(?=(?:#{0,4}|\*{0,4})\s*Variant \d)', text.strip())
    for block in blocks:
        if not block.strip():
            continue
        v = {"variant": None, "angle": "", "headline": "", "body": "",
             "scores": {}, "status": "PASS", "flag_reason": "", "rewrite": None}
        m = re.match(r'(?:#{0,4}|\*{0,4})\s*Variant (\d+)\s*[—-]\s*(.+)', block)
        if m:
            v["variant"] = int(m.group(1))
            v["angle"] = m.group(2).strip().rstrip('*').strip()
        for dim, key in [
            ("Brand Voice", "brand_voice"), ("Platform Fit", "platform_fit"),
            ("Specificity", "specificity"), ("Luxury Signal", "luxury_signal")
        ]:
            sm = re.search(rf'{re.escape(dim)}\b[^\n]*?(\d+(?:\.\d+)?)\s*/\s*10', block, re.IGNORECASE)
            if sm:
                v["scores"][key] = round(float(sm.group(1)))
        if re.search(r'\bFLAG\b', block, re.IGNORECASE):
            v["status"] = "FLAG"
            fr = re.search(r'FLAG\s*[—-]\s*(.+)', block)
            if fr:
                v["flag_reason"] = fr.group(1).strip()
        hm = re.search(r'Headline:\s*(.+)', block)
        bm = re.search(r'Body:\s*(.+)', block)
        if hm:
            v["headline"] = hm.group(1).strip()
        if bm:
            v["body"] = bm.group(1).strip()
        if v["variant"] is not None:
            variants.append(v)
    return variants


def merge_rewrites(variants: list, rewrites_text: str) -> None:
    """Merge rewritten copy into variant dicts where rewrites exist."""
    if not rewrites_text or "No rewrites needed" in rewrites_text:
        return
    blocks = re.split(r'\n(?=(?:#{0,4}|\*{0,4})\s*Variant \d)', rewrites_text)
    for block in blocks:
        m = re.match(r'(?:#{0,4}|\*{0,4})\s*Variant (\d+)', block)
        if not m:
            continue
        num = int(m.group(1))
        hm = re.search(r'Headline:\s*(.+?)(?:\s*\[[\*\d\s]+chars[\*\s]*\])?\s*$', block, re.MULTILINE)
        bm = re.search(r'Body:\s*(.+?)(?:\s*\[[\*\d\s]+chars[\*\s]*\])?\s*$', block, re.MULTILINE)
        for v in variants:
            if v["variant"] == num and (hm or bm):
                v["rewrite"] = {
                    "headline": hm.group(1).strip() if hm else "",
                    "body": bm.group(1).strip() if bm else "",
                }


# ── Run guard ────────────────────────────────────────────────────────────────

def require_complete(run_id: str) -> dict:
    """Shared guard: run must exist, be complete, and have a review folder."""
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = RUNS[run_id]
    if run.get("status") != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or no review folder.")
    return run


# ── Content edit overlay (edits.json) ────────────────────────────────────────

def edits_path(review_folder) -> Path:
    return Path(review_folder) / "edits.json"


def load_edits(review_folder) -> dict:
    p = edits_path(review_folder)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_edits(review_folder, edits: dict) -> None:
    edits_path(review_folder).write_text(
        json.dumps(edits, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── Audience overlay (audience.json) ─────────────────────────────────────────

def audience_path(review_folder) -> Path:
    return Path(review_folder) / "audience.json"


def load_audience(review_folder) -> dict | None:
    p = audience_path(review_folder)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_audience(review_folder, audience: dict) -> None:
    audience_path(review_folder).write_text(
        json.dumps(audience, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def effective_audience(review_folder, brief: dict) -> dict:
    """Saved audience if present, else resolve+seed the curated default once."""
    saved = load_audience(review_folder)
    if saved is not None:
        return saved
    from pikorua_adflow.tools import meta_targeting as _mt
    token = os.getenv("META_ACCESS_TOKEN", "")
    city = brief.get("city", "") or ""

    llm_selection = None
    selection_path = Path(review_folder) / "targeting_selection.json"
    if selection_path.exists():
        try:
            raw_text = selection_path.read_text(encoding="utf-8")
            raw_text = re.sub(r"```(?:json)?", "", raw_text).strip("`").strip()
            raw_selection = json.loads(raw_text)
            llm_selection = _mt.resolve_llm_targeting(raw_selection, token)
        except Exception:
            llm_selection = None

    try:
        audience = _mt.build_default_audience(
            city, token,
            locality=brief.get("locality", ""),
            nri_geographies=brief.get("nri_geographies", ""),
            clientele_type=brief.get("clientele_type", ""),
            llm_selection=llm_selection,
        )
    except Exception as exc:
        audience = {
            "country": "IN", "city": "", "city_key": None, "region": "",
            "radius_km": _mt.DEFAULT_RADIUS_KM,
            "age_min": _mt.DEFAULT_AGE_MIN, "age_max": _mt.DEFAULT_AGE_MAX,
            "interests": [], "behaviours": [], "resolve_error": str(exc),
        }
    save_audience(review_folder, audience)
    return audience


# ── Creative mode overlay (deploy_settings.json) ─────────────────────────────
# Opt-in toggle: "curated" (default, one image+headline+body per variant) or
# "dynamic" (pools every variant's assets into one Meta Dynamic Creative ad and
# lets Meta pick combinations). See deploy_dynamic_ad() in tools/meta_tool.py.

def deploy_settings_path(review_folder) -> Path:
    return Path(review_folder) / "deploy_settings.json"


def get_creative_mode(review_folder) -> str:
    p = deploy_settings_path(review_folder)
    if not p.exists():
        return "curated"
    try:
        mode = json.loads(p.read_text(encoding="utf-8")).get("creative_mode", "curated")
        return mode if mode in ("curated", "dynamic") else "curated"
    except Exception:
        return "curated"


def set_creative_mode(review_folder, mode: str) -> None:
    if mode not in ("curated", "dynamic"):
        raise ValueError(f"Invalid creative_mode: {mode!r}")
    deploy_settings_path(review_folder).write_text(
        json.dumps({"creative_mode": mode}, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── Effective (overlay-applied) copy ─────────────────────────────────────────

def base_meta(review_folder) -> dict[int, dict]:
    """AI baseline Meta copy (ad_copy.md with rewrites merged), before user edits."""
    rf = Path(review_folder)
    ac = rf / "ad_copy.md"
    meta = parse_ad_copy(ac.read_text(encoding="utf-8")).get("meta", {}) if ac.exists() else {}
    base: dict[int, dict] = {
        num: {"headline": c.get("headline", ""), "body": c.get("body", "")}
        for num, c in meta.items()
    }
    sc = rf / "copy_scorecard.md"
    rw = rf / "copy_rewrites.md"
    vlist = parse_scorecard(sc.read_text(encoding="utf-8") if sc.exists() else "")
    merge_rewrites(vlist, rw.read_text(encoding="utf-8") if rw.exists() else "")
    for v in vlist:
        n = v.get("variant")
        rwc = v.get("rewrite") or {}
        if n in base and rwc:
            if rwc.get("headline"):
                base[n]["headline"] = rwc["headline"]
            if rwc.get("body"):
                base[n]["body"] = rwc["body"]
    return base


def effective_meta(review_folder) -> dict[int, dict]:
    """AI baseline with the user overlay applied. Deleted versions removed."""
    base = base_meta(review_folder)
    edits = load_edits(review_folder)
    meta_edits = edits.get("meta", {})
    deleted = set(edits.get("deleted_variants", []))
    out: dict[int, dict] = {}
    for num, c in base.items():
        if num in deleted:
            continue
        e = meta_edits.get(str(num))
        if e:
            out[num] = {
                "headline": e.get("headline", c["headline"]),
                "body": e.get("body", c["body"]),
                "edited": True, "added": False,
            }
        else:
            out[num] = {**c, "edited": False, "added": False}
    for k, e in meta_edits.items():
        n = int(k)
        if e.get("added") and n not in deleted and n not in out:
            out[n] = {"headline": e.get("headline", ""), "body": e.get("body", ""),
                      "edited": True, "added": True}
    for entry in out.values():
        entry["headline"] = clean_copy(entry.get("headline", ""))
        entry["body"] = clean_copy(entry.get("body", ""))
    return dict(sorted(out.items()))


def effective_channel(review_folder, channel: str) -> tuple[str, bool]:
    """(text, edited?) for google/whatsapp/email with overlay applied."""
    rf = Path(review_folder)
    ac = rf / "ad_copy.md"
    base = parse_ad_copy(ac.read_text(encoding="utf-8")).get(channel, "") if ac.exists() else ""
    base = clean_copy(base)
    ov = load_edits(review_folder).get(channel)
    return (ov, True) if ov is not None else (base, False)


# ── Detail payload for the campaign-detail page ──────────────────────────────

def get_run_detail(run_id: str) -> dict:
    """Structured JSON for the campaign-detail page (all tabs). Read-only."""
    run = require_complete(run_id)
    review_folder = Path(run["review_folder"])
    brief = run.get("brief", {})

    def read(name):
        p = review_folder / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    scorecard_text = read("copy_scorecard.md")
    rewrites_text = read("copy_rewrites.md")
    persona_text = read("persona.md")
    targeting_text = read("targeting_brief.md")
    visual_text = read("visual_brief.md")

    sc_variants = parse_scorecard(scorecard_text)
    merge_rewrites(sc_variants, rewrites_text)
    sc_by_num = {v.get("variant"): v for v in sc_variants}

    eff_meta = effective_meta(review_folder)
    edits = load_edits(review_folder)
    deleted_nums = sorted(edits.get("deleted_variants", []))

    # Pre-selection: explicit selection, else top-3 PASS/auto-rewritten by avg score.
    already_selected = run.get("selected_variants", [])
    if already_selected:
        default_selected = set(already_selected)
    else:
        candidates = [v for v in sc_variants
                      if v.get("status") == "PASS" or (v.get("status") == "FLAG" and v.get("rewrite"))]
        candidates.sort(
            key=lambda v: sum(v.get("scores", {}).values()) / max(len(v.get("scores", {})), 1),
            reverse=True,
        )
        default_selected = {v["variant"] for v in candidates[:3]}

    variants = []
    meta_overlay = edits.get("meta", {})
    for num in sorted(eff_meta.keys()):
        emc = eff_meta[num]
        info = sc_by_num.get(num, {})
        status = info.get("status")
        revised = status == "FLAG" and bool(info.get("rewrite"))
        variants.append({
            "variant": num,
            "angle": info.get("angle", "") or ("Your custom version" if emc.get("added") else ""),
            "status": status,
            "revised": revised,
            "scores": info.get("scores", {}),
            "flag_reason": info.get("flag_reason", ""),
            "headline": emc.get("headline", ""),
            "body": emc.get("body", ""),
            "edited": emc.get("edited", False),
            "added": emc.get("added", False),
            "selected": num in default_selected,
            "image_num": meta_overlay.get(str(num), {}).get("image_num"),
        })

    google_text, google_edited = effective_channel(review_folder, "google")
    whatsapp_text, whatsapp_edited = effective_channel(review_folder, "whatsapp")
    email_text, email_edited = effective_channel(review_folder, "email")

    # Image prompts — from visual_prompts.json (new pipeline) or visual_brief.md (legacy)
    prompt_overrides = edits.get("prompt_overrides", {})
    image_prompts = []
    _VP_LABELS = {
        "lifestyle_private_retreat": "Lifestyle — Private Retreat",
        "lifestyle_social_home": "Lifestyle — The Social Home",
        "lifestyle_dynamic_a": "Lifestyle — Scene A",
        "lifestyle_dynamic_b": "Lifestyle — Scene B",
        "interior_signature_moment": "Interior Signature Moment",
        "exterior_establishing_shot": "Exterior Establishing Shot",
        # legacy keys kept for runs generated before the variant restructure
        "lifestyle_city_connection": "Lifestyle — The Address",
        "architectural_perspective": "Architectural Perspective",
        "lifestyle_moment": "Lifestyle Moment",
        "iconic_representation": "Iconic Representation",
    }
    _VP_OPT_IN = {"exterior_establishing_shot"}
    vp_path = review_folder / "visual_prompts.json"
    if vp_path.exists():
        try:
            vp_entries = json.loads(vp_path.read_text(encoding="utf-8"))
        except Exception:
            vp_entries = []
        for entry in vp_entries:
            i = entry.get("prompt_num", 0)
            vk = entry.get("variant_key", "")
            title = _VP_LABELS.get(vk, f"Prompt {i}")
            ptext = entry.get("ideogram_prompt", "")
            if not ptext and entry.get("scene_prose"):
                # New-pipeline entries don't store raw prompt text — rebuild it
                # deterministically from the persisted AdSpec so the edit view shows
                # the actual prompt used, without needing a fresh "AI rewrite" call.
                try:
                    from .image.brief_model import BriefModel
                    from .image.art_director import AdSpec
                    from .image import baked_prompt as _baked_prompt
                    sample_ready = bool(brief.get("sample_ready", False))
                    default_cta = "Sample Flat Ready" if sample_ready else ""
                    headline = (eff_meta.get(i) or {}).get("headline", "")
                    bm = BriefModel.from_brief(
                        brief, headline=headline, cta=default_cta,
                        sample_ready_override=sample_ready,
                    )
                    bm.has_logo = image_service.BRAND_LOGO_PATH.exists()
                    spec = AdSpec.from_entry({**entry, "variant_key": vk, "prompt_num": i})
                    ptext = _baked_prompt.build(spec, bm)
                except Exception:
                    ptext = entry.get("scene_prose", "")
            has_prompt = bool(entry.get("scene_prose") or ptext)
            image_prompts.append({
                "num": i, "title": title,
                "prompt_text": prompt_overrides.get(str(i), ptext),
                "edited": str(i) in prompt_overrides,
                "opt_in": vk in _VP_OPT_IN,
                "has_prompt": has_prompt,
            })
    elif visual_text:
        for i, block in enumerate(re.split(r"\n---\n", visual_text), 1):
            tm = re.search(r"\*\*Prompt\s+\d+\s*[—\-–]\s*(.+?)(?:\*\*|:)", block)
            title = tm.group(1).strip() if tm else f"Prompt {i}"
            pq = re.search(r'"([\s\S]+?)"(?:\s*$)', block.strip())
            ptext = pq.group(1).strip() if pq else block.strip().strip('"')
            if ptext:
                image_prompts.append({
                    "num": i, "title": title,
                    "prompt_text": prompt_overrides.get(str(i), ptext),
                    "edited": str(i) in prompt_overrides,
                })

    images_dir = review_folder / "images"
    existing_images = []
    if images_dir.exists():
        existing_images = sorted(
            f.name for f in images_dir.iterdir()
            if re.match(r"image_(?:\d+(?:_v\d+)?|r\d+(?:_v\d+)?)\.png$", f.name)
        )

    audience = effective_audience(review_folder, brief)

    meta_ads = run.get("meta_ads", [])
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    has_live_ads = bool([a for a in meta_ads if not a.get("dry_run") and a.get("ad_id")]) and not dry_run

    reference_variants = edits.get("reference_variants", {})

    return {
        "run_id": run_id,
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "brief": brief,
        "scorecard_summary": run.get("copy_scorecard_summary", ""),
        "variants": variants,
        "deleted_variants": deleted_nums,
        "google": {"text": google_text, "edited": google_edited,
                   "issues": google_copy_issues(google_text, brief.get("city", ""))},
        "whatsapp": {"text": whatsapp_text, "edited": whatsapp_edited},
        "email": {"text": email_text, "edited": email_edited},
        "image_prompts": image_prompts,
        "existing_images": existing_images,
        "reference_variants": reference_variants,
        "persona_html": md_to_html(persona_text) if persona_text else "",
        "targeting_html": md_to_html(targeting_text) if targeting_text else "",
        "audience": audience,
        "meta_ads": meta_ads,
        "has_live_ads": has_live_ads,
        "approved": run.get("approved", False),
        "selected_variants": sorted(default_selected),
    }


# ── Pipeline helpers ──────────────────────────────────────────────────────────

def _collect_prior_visual_state(property_name: str) -> dict:
    """
    Scan all completed RUNS for the same property_name and collect the scene_tag /
    tone_tag / recipe_tag history per variant_key from their visual_prompts.json files.

    Returns: {variant_key: {"scene": [...oldest first...], "tone": [...], "recipe": [...]}}
    """
    state: dict[str, dict[str, list]] = {}
    with RUNS_LOCK:
        runs_snapshot = dict(RUNS)
    for run in runs_snapshot.values():
        if run.get("brief", {}).get("property_name") != property_name:
            continue
        review_folder = run.get("review_folder")
        if not review_folder:
            continue
        vp_path = Path(review_folder) / "visual_prompts.json"
        if not vp_path.exists():
            continue
        try:
            entries = json.loads(vp_path.read_text(encoding="utf-8"))
            for entry in entries:
                vk = entry.get("variant_key")
                if not vk:
                    continue
                bucket = state.setdefault(vk, {"scene": [], "tone": [], "recipe": []})
                if scene := entry.get("scene_tag"):
                    bucket["scene"].append(scene)
                if tone := entry.get("tone_tag"):
                    bucket["tone"].append(tone)
                if recipe := entry.get("recipe_tag"):
                    bucket["recipe"].append(recipe)
        except Exception:
            pass
    return state


# ── Pipeline ─────────────────────────────────────────────────────────────────

def _pipeline_state_path(run_id: str) -> Path:
    return OUTPUT_DIR / f"pipeline_state_{run_id}.json"


def _load_pipeline_state(run_id: str) -> dict | None:
    p = _pipeline_state_path(run_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_pipeline_state(run_id: str, state: dict) -> None:
    try:
        p = _pipeline_state_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError:
        pass


def _clear_pipeline_state(run_id: str) -> None:
    p = _pipeline_state_path(run_id)
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


def _run_content_stage(run_id: str, brief: CampaignBrief, inputs: dict):
    """Stage 2: ContentCrew + save_for_review. Shared by run_pipeline and resume_pipeline."""
    from pikorua_adflow.crews.content_crew.content_crew import ContentCrew
    from pikorua_adflow.utils.output_saver import save_for_review

    inputs.setdefault("company_name", "")
    inputs.setdefault("property_type", "")
    inputs.setdefault("daily_budget_inr", "1000")
    inputs.setdefault("cta", "GET_QUOTE")
    inputs.setdefault("sample_ready", "no")
    inputs.setdefault("cheque_only", "no")
    inputs.setdefault("clientele_type", "hni_nri")
    inputs.setdefault("standout_feature", "none provided — use the thin-brief fallback")

    try:
        from pikorua_adflow.tools.memory_tool import get_fewshot_context
        inputs["past_campaigns"] = get_fewshot_context({
            "property_name": brief.property_name,
            "property_type": brief.property_type,
            "city": brief.city,
            "buyer_type": brief.buyer_type,
            "goal": brief.goal,
        })
    except Exception:
        inputs["past_campaigns"] = ""

    try:
        with RUNS_LOCK:
            RUNS[run_id]["status"] = "running_stage2"
        prior_visual_state = _collect_prior_visual_state(brief.property_name)
        # B3: read per-clientele creative priors (palette/recipe that won before).
        # {} on first run — no prior knowledge, no bias.
        try:
            from pikorua_adflow.analytics import creative_learning as _cl
            creative_priors = _cl.get_priors(brief.clientele_type or "")
        except Exception:
            creative_priors = {}
        content_result = ContentCrew(
            prior_visual_state=prior_visual_state,
            creative_priors=creative_priors,
        ).crew().kickoff(inputs=inputs)
        review_folder = save_for_review(content_result, audience_result=inputs.get("_audience_output"))
        summary = None
        if COPY_SCORECARD_PATH.exists():
            text = COPY_SCORECARD_PATH.read_text(encoding="utf-8")
            summary = next(
                (l.strip() for l in reversed(text.splitlines()) if l.strip() and ("passed" in l or "flagged" in l)),
                None,
            )
        with RUNS_LOCK:
            RUNS[run_id]["status"] = "complete"
            RUNS[run_id]["review_folder"] = str(review_folder)
            if summary:
                RUNS[run_id]["copy_scorecard_summary"] = summary
        save_runs()
        _clear_pipeline_state(run_id)
    except Exception as exc:
        with RUNS_LOCK:
            RUNS[run_id]["status"] = "failed"
            RUNS[run_id]["error"] = str(exc)
        save_runs()


def resume_pipeline(run_id: str):
    """Resume a run whose ContentCrew stage failed, skipping AudienceCrew via its checkpoint."""
    state = _load_pipeline_state(run_id)
    if not state or state.get("stage") != "audience_done":
        raise ValueError("No resumable checkpoint found for this run.")
    brief_data = RUNS[run_id].get("brief", {})
    brief = CampaignBrief(**brief_data)
    os.chdir(REPO_ROOT)
    with RUNS_LOCK:
        RUNS[run_id]["status"] = "running_stage2"
        RUNS[run_id].pop("error", None)
    save_runs()
    _run_content_stage(run_id, brief, state["inputs"])


def run_pipeline(run_id: str, brief: CampaignBrief):
    """Runs both crews in a background thread and updates the run registry."""
    from pikorua_adflow.crews.audience_crew.audience_crew import AudienceCrew
    from pikorua_adflow.utils.crm_analyser import analyse as crm_analyse

    sys.stdout.reconfigure(encoding="utf-8")

    # Crews write output_file: paths relative to CWD — force repo root so the
    # portal and output_saver read/write one outputs/ dir.
    os.chdir(REPO_ROOT)
    outputs_dir = OUTPUT_DIR

    for stale in (
        "copy_scorecard.md", "copy_rewrites.md", "targeting_brief.md",
        "render_prompts.md", "visual_brief.md", "visual_prompts.json",
        "targeting_selection.json",
    ):
        p = outputs_dir / stale
        if p.exists():
            p.unlink()

    crm_insights = crm_analyse()

    from pikorua_adflow.tools import meta_targeting as _mt

    locality_str = f", {brief.locality}" if brief.locality else ""
    nri_str = f" NRI target geographies: {brief.nri_geographies}." if brief.nri_geographies else ""
    feature_str = f" Standout feature: {brief.standout_feature}." if brief.standout_feature else ""
    company_str = brief.company_name.strip() if brief.company_name else ""
    inputs = {
        "platform": brief.platform,
        "product": (
            f"{'(' + company_str + ') — ' if company_str else ''}Luxury Real Estate Consultancy. "
            f"Property: {brief.property_name}, "
            f"a {brief.property_type} in {brief.city}{locality_str} at ₹{brief.price_cr} Cr.{feature_str}"
        ),
        "target_audience": (
            f"{brief.buyer_type} buyers seeking premium {brief.property_type} in {brief.city}. "
            f"Campaign goal: {brief.goal}. Budget: ₹{brief.budget_inr:,}. "
            f"Duration: {brief.campaign_duration_days} days.{nri_str}"
        ),
        "property_type": brief.property_type,
        "city": brief.city,
        "locality": brief.locality,
        "locality_suffix": locality_str,
        "price_cr": brief.price_cr,
        "goal": brief.goal,
        "buyer_type": brief.buyer_type,
        "nri_geographies": brief.nri_geographies,
        "campaign_duration_days": str(brief.campaign_duration_days),
        "daily_budget_inr": str(brief.daily_budget_inr),
        "cta": brief.cta,
        "sample_ready": "yes" if brief.sample_ready else "no",
        "cheque_only": "yes" if brief.cheque_only else "no",
        "clientele_type": brief.clientele_type,
        "standout_feature": brief.standout_feature or "none provided — use the thin-brief fallback",
        "company_name": company_str,
        "persona": "No persona data — audience crew has not run yet.",
        "trends": "No trend data — audience crew has not run yet.",
        "targeting": "No targeting data — audience crew has not run yet.",
        "crm_insights": crm_insights,
        "targeting_pool": _mt.render_targeting_pool_for_prompt(),
        "today": date.today().strftime("%B %d, %Y"),
        "reference_images": image_service.build_reference_images_context(),
    }

    with RUNS_LOCK:
        RUNS[run_id]["status"] = "running_stage1"

    trend_hooks_path = TREND_HOOKS_PATH
    trend_age = (
        datetime.now().timestamp() - trend_hooks_path.stat().st_mtime
        if trend_hooks_path.exists() else float("inf")
    )
    use_cached_trends = trend_age < TREND_TTL_SECONDS

    audience_output = None
    audience_result = None
    try:
        audience_result = AudienceCrew(skip_trends=use_cached_trends).crew().kickoff(inputs=inputs)
        audience_output = str(audience_result)
        inputs["persona"] = audience_output[:1500]
        targeting_path = outputs_dir / "targeting_brief.md"
        if targeting_path.exists():
            inputs["targeting"] = targeting_path.read_text(encoding="utf-8")[:1200]
        else:
            inputs["targeting"] = audience_output[:1200]
        if trend_hooks_path.exists():
            inputs["trends"] = trend_hooks_path.read_text(encoding="utf-8")[:800]
        time.sleep(8)
        inputs["_audience_output"] = audience_output
        with RUNS_LOCK:
            RUNS[run_id]["status"] = "running_stage2"
        _save_pipeline_state(run_id, {"stage": "audience_done", "inputs": inputs})
    except Exception as exc:
        with RUNS_LOCK:
            RUNS[run_id]["stage1_warning"] = str(exc)
            RUNS[run_id]["status"] = "running_stage2"

    _run_content_stage(run_id, brief, inputs)
