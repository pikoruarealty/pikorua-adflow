"""
FastAPI portal endpoint — Task 1.6.
Allows Pikorua team to launch campaigns without terminal access.

Run with:
    uvicorn pikorua_adflow.api.main:app --reload --port 8000

Then open: http://localhost:8000  (redirects to portal form)
Or POST directly to: http://localhost:8000/launch-campaign
"""

import os
import re
import sys
import uuid
import threading
from pathlib import Path
from datetime import datetime, date, timezone

# Must set up dotenv and litellm before any crew imports
from dotenv import load_dotenv
load_dotenv()
import litellm
litellm.drop_params = True
litellm.num_retries = 6          # retry up to 6x on 429/5xx
litellm.request_timeout = 120    # 2 min per request before timeout

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
import json
from pydantic import BaseModel, Field

app = FastAPI(
    title="Pikorua Campaign Portal",
    description="Internal tool — launches AI-generated ad campaigns for Pikorua Realty team.",
    version="1.0.0",
)

# Persistent run registry — survives server restarts
_RUNS_PATH = Path(__file__).parent.parent.parent.parent / "outputs" / "runs.json"


def _load_runs() -> dict[str, dict]:
    if not _RUNS_PATH.exists():
        return {}
    try:
        data = json.loads(_RUNS_PATH.read_text(encoding="utf-8"))
        for run in data.values():
            if run.get("status", "").startswith("running_") or run.get("status") == "queued":
                run["status"] = "failed"
                run["error"] = "Server restarted while run was in progress."
        return data
    except Exception:
        return {}


def _save_runs() -> None:
    _RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RUNS_PATH.write_text(json.dumps(_runs, indent=2, default=str), encoding="utf-8")


_runs: dict[str, dict] = _load_runs()


class CampaignBrief(BaseModel):
    property_name: str = Field(..., min_length=2, description="Name/description of the property")
    platform: str = Field(..., description="e.g. 'Meta Ads', 'Google Ads'")
    goal: str = Field(..., description="e.g. 'Lead Generation', 'Brand Awareness'")
    budget_inr: int = Field(..., gt=0, description="Campaign budget in INR")
    city: str = Field(..., min_length=2, description="Target city, e.g. 'Mumbai'")
    locality: str = Field("", description="Specific area within city, e.g. 'Thaltej', 'Bandra West'")
    property_type: str = Field(..., description="e.g. 'sea-view apartment', '4BHK villa'")
    price_cr: str = Field(..., description="Price in crores, e.g. '4.5'")
    standout_feature: str = Field("", description="One concrete differentiator the copywriter can anchor on, e.g. 'infinity pool on 32nd floor', 'only north-facing units', 'Tadao Ando-influenced facade'. Optional — leave blank if none.")
    buyer_type: str = Field("HNI/NRI", description="Target buyer segment: 'HNI', 'NRI', or 'HNI/NRI'")
    nri_geographies: str = Field("", description="NRI diaspora locations if relevant, e.g. 'UAE, US, UK'")
    campaign_duration_days: int = Field(30, gt=0, description="Campaign flight duration in days")
    landing_page_url: str = Field("https://pikorua.in/", description="URL shown on Lead Gen form Thank You screen")
    daily_budget_inr: int = Field(1000, gt=0, description="Daily budget per Meta ad set in INR (Meta uses paise internally)")
    cta: str = Field("GET_QUOTE", description="Call to action: GET_QUOTE, CONTACT_US, LEARN_MORE")
    company_name: str = Field("", description="Optional: company/page name to reference in copy (e.g. 'Pikorua', 'Sky Properties'). Leave blank to omit any company name from copy — useful when posting from multiple pages.")


class ApproveRequest(BaseModel):
    selected_variants: list[int] = Field(
        default=[],
        description="Variant numbers selected for launch (e.g. [1,3]). Empty list = approve all.",
    )


class CRMAudienceRequest(BaseModel):
    target_countries: list[str] = Field(["IN"], description="ISO-2 country codes for lookalike. Use ['AE','US','SG'] for NRI audiences.")
    split: bool = Field(False, description="If true, split leads into good/bad and create two audiences. Default false = single audience (legacy).")


def _run_pipeline(run_id: str, brief: CampaignBrief):
    """Runs both crews in a background thread and updates the run registry."""
    # Import here so dotenv is already loaded before CrewAI initialises
    from pikorua_adflow.crews.audience_crew.audience_crew import AudienceCrew
    from pikorua_adflow.crews.content_crew.content_crew import ContentCrew
    from pikorua_adflow.utils.output_saver import save_for_review
    from pikorua_adflow.utils.crm_analyser import analyse as crm_analyse

    import os
    sys.stdout.reconfigure(encoding="utf-8")

    # CrewAI writes output_file: paths (scorecard, rewrites, targeting, render
    # prompts) relative to the CURRENT WORKING DIRECTORY. If the server was
    # started from src/, those files land in src/outputs/ while output_saver and
    # the portal read from repo-root outputs/ — so they'd show a previous run's
    # files forever. Force CWD to the repo root so everyone reads/writes one dir.
    repo_root = Path(__file__).parent.parent.parent.parent
    os.chdir(repo_root)
    outputs_dir = repo_root / "outputs"

    # Clear per-run files that are produced via output_file:. If a task fails or
    # is skipped (e.g. evaluator hits a rate limit), the previous run's file must
    # NOT leak into this run's review folder. Absent file = honest "no data".
    for stale in ("copy_scorecard.md", "copy_rewrites.md", "targeting_brief.md",
                  "render_prompts.md", "visual_brief.md"):
        p = outputs_dir / stale
        if p.exists():
            p.unlink()

    # Run CRM analysis before crew kickoff — graceful if file missing or Supabase unreachable.
    crm_insights = crm_analyse()

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
        "price_cr": brief.price_cr,
        "goal": brief.goal,
        "buyer_type": brief.buyer_type,
        "nri_geographies": brief.nri_geographies,
        "campaign_duration_days": str(brief.campaign_duration_days),
        "daily_budget_inr": str(brief.daily_budget_inr),
        "cta": brief.cta,
        "standout_feature": brief.standout_feature or "none provided — use the thin-brief fallback",
        "company_name": company_str,
        "persona": "No persona data — audience crew has not run yet.",
        "trends": "No trend data — audience crew has not run yet.",
        "targeting": "No targeting data — audience crew has not run yet.",
        "crm_insights": crm_insights,
        "today": date.today().strftime("%B %d, %Y"),
    }

    _runs[run_id]["status"] = "running_stage1"

    # Trend hooks are stable for hours — skip the web-search task if the file is
    # younger than TREND_TTL_SECONDS and reuse the cached result instead.
    TREND_TTL_SECONDS = 8 * 3600
    trend_hooks_path = outputs_dir / "trend_hooks.md"
    trend_age = (
        datetime.now().timestamp() - trend_hooks_path.stat().st_mtime
        if trend_hooks_path.exists() else float("inf")
    )
    use_cached_trends = trend_age < TREND_TTL_SECONDS

    audience_output = None
    try:
        audience_result = AudienceCrew(skip_trends=use_cached_trends).crew().kickoff(inputs=inputs)
        audience_output = str(audience_result)
        # Cap persona at 1500 chars — the copywriter needs the insight, not a wall of text.
        inputs["persona"] = audience_output[:1500]
        # Load targeting brief from file if the agent wrote it; fall back to crew output
        targeting_path = outputs_dir / "targeting_brief.md"
        if targeting_path.exists():
            inputs["targeting"] = targeting_path.read_text(encoding="utf-8")[:1200]
        else:
            inputs["targeting"] = audience_output[:1200]
        # Pass actual trend hooks to content crew — read from file (fresh or cached)
        if trend_hooks_path.exists():
            inputs["trends"] = trend_hooks_path.read_text(encoding="utf-8")[:800]
        import time; time.sleep(8)  # brief pause to let RPM window reset before Stage 2 burst
        _runs[run_id]["status"] = "running_stage2"
    except Exception as exc:
        _runs[run_id]["stage1_warning"] = str(exc)
        _runs[run_id]["status"] = "running_stage2"

    # Ensure all content crew template vars are always present — guards against
    # stale bytecache or old brief dicts that predate new fields.
    inputs.setdefault("company_name", "")
    inputs.setdefault("property_type", "")
    inputs.setdefault("daily_budget_inr", "1000")
    inputs.setdefault("cta", "GET_QUOTE")
    inputs.setdefault("standout_feature", "none provided — use the thin-brief fallback")

    try:
        _runs[run_id]["status"] = "running_stage2"
        content_result = ContentCrew().crew().kickoff(inputs=inputs)
        review_folder = save_for_review(content_result, audience_result=audience_output)
        _runs[run_id]["status"] = "complete"
        _runs[run_id]["review_folder"] = str(review_folder)
        # Surface copy scorecard summary if the evaluator wrote it
        scorecard_path = Path(__file__).parent.parent.parent.parent / "outputs" / "copy_scorecard.md"
        if scorecard_path.exists():
            text = scorecard_path.read_text(encoding="utf-8")
            # Pull just the summary line (last non-empty line starting with a digit or "X/")
            summary = next(
                (l.strip() for l in reversed(text.splitlines()) if l.strip() and ("passed" in l or "flagged" in l)),
                None,
            )
            if summary:
                _runs[run_id]["copy_scorecard_summary"] = summary
        _save_runs()
    except Exception as exc:
        _runs[run_id]["status"] = "failed"
        _runs[run_id]["error"] = str(exc)
        _save_runs()


@app.get("/", response_class=RedirectResponse)
def root():
    """Redirect root to the portal form."""
    return RedirectResponse(url="/portal")


@app.get("/portal", response_class=HTMLResponse)
def portal():
    """Serve the campaign launch form from portal/index.html."""
    portal_path = Path(__file__).parent.parent.parent.parent / "portal" / "index.html"
    if not portal_path.exists():
        raise HTTPException(status_code=404, detail="portal/index.html not found")
    return HTMLResponse(content=portal_path.read_text(encoding="utf-8"))


BRAND_CSS = """
/* ============================================================
   PIKORUA brand stylesheet — single source of truth.
   Palette taken from the logo + reference ads:
   brushed gold, deep forest green, warm cream, charcoal.
   Edit colours here once; every page updates.
   ============================================================ */
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap');

:root{
  --gold:#C9A84C;        /* brushed gold — brand accent */
  --gold-deep:#A8842B;   /* darker gold for text/hover   */
  --gold-soft:#F1E6C4;   /* gold tint backgrounds        */
  --green:#1F3D2E;       /* deep forest green — primary  */
  --green-mid:#2E5740;
  --green-soft:#EAF1EC;
  --cream:#F7F2E8;       /* page background              */
  --paper:#FFFFFF;       /* cards                        */
  --paper-warm:#FDFBF5;  /* inset / subtle panels        */
  --ink:#2A2520;         /* primary text (warm black)    */
  --ink-soft:#6B6256;    /* secondary text               */
  --muted:#9B8F7C;       /* tertiary / hints             */
  --line:#E7DFCE;        /* borders                      */
  --danger:#B23B2E; --danger-soft:#FBEEEB;
  --ok:#2E5740;          --ok-soft:#EAF1EC;
  --warn:#9A7320;        --warn-soft:#FaF3DE;
  --radius:12px;
  --shadow:0 6px 24px rgba(42,37,32,0.07);
  /* theming hooks — overridden by dark mode */
  --btn-bg:var(--green);
  --btn-hover-bg:var(--green-mid);
  --btn-txt:#fff;
  --btn-shadow:rgba(31,61,46,0.25);
  --topbar-bg:var(--ink);
  --topbar-border:transparent;
  --navlink-txt:rgba(255,255,255,0.78);
  --navlink-hover-bg:rgba(255,255,255,0.1);
  --navlink-hover-txt:#fff;
  --navlink-active-bg:rgba(255,255,255,0.16);
  --navlink-active-txt:#fff;
}

*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}

body{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:var(--cream);
  color:var(--ink);
  line-height:1.55;
  -webkit-font-smoothing:antialiased;
}

h1,h2,h3,.display{font-family:'Cormorant Garamond','Georgia',serif;font-weight:600;color:var(--ink);letter-spacing:0.01em;}
h1{font-size:2.1rem;line-height:1.1;}
h2{font-size:1.35rem;}
a{color:var(--green-mid);text-decoration:none;}
a:hover{color:var(--gold-deep);}

/* ---- top bar ---- */
.topbar{
  display:flex;align-items:center;justify-content:space-between;gap:1rem;
  padding:0.75rem 1.6rem;background:var(--topbar-bg);
  border-bottom:1px solid var(--topbar-border);position:sticky;top:0;z-index:20;
  box-shadow:0 2px 12px rgba(0,0,0,0.15);
}
.brand{display:flex;align-items:center;}
.nav{display:flex;gap:0.4rem;}
.navlink{
  font-size:0.85rem;font-weight:500;color:var(--navlink-txt);
  padding:0.5rem 0.95rem;border-radius:999px;transition:all .15s;
}
.navlink:hover{background:var(--navlink-hover-bg);color:var(--navlink-hover-txt);}
.navlink.active{background:var(--navlink-active-bg);color:var(--navlink-active-txt);font-weight:600;}
.navlink.active:hover{filter:brightness(1.1);}

/* ---- layout ---- */
.wrap{max-width:880px;margin:0 auto;padding:2.2rem 1.4rem 4rem;}
.wrap-wide{max-width:1180px;margin:0 auto;padding:2.2rem 1.4rem 4rem;}
.lede{color:var(--ink-soft);font-size:1rem;margin:0.4rem 0 0;max-width:60ch;}

/* ---- cards ---- */
.card{background:var(--paper);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:1.8rem 2rem;}
.section{margin-top:1.8rem;}
.section-title{font-family:'Cormorant Garamond',serif;font-size:1.25rem;color:var(--ink);
  margin-bottom:0.2rem;}
.section-sub{font-size:0.85rem;color:var(--muted);margin-bottom:1.1rem;}
.eyebrow{font-size:0.7rem;letter-spacing:0.16em;text-transform:uppercase;color:var(--gold-deep);font-weight:600;}

/* ---- forms ---- */
.field{margin-bottom:1.15rem;}
label{display:block;font-size:0.82rem;font-weight:600;color:var(--ink);margin-bottom:0.4rem;}
label .opt{font-weight:400;color:var(--muted);font-size:0.78rem;}
.hint{font-size:0.78rem;color:var(--muted);margin-top:0.3rem;}
input,select,textarea{
  width:100%;padding:0.72rem 0.85rem;border:1px solid var(--line);border-radius:8px;
  font-size:0.95rem;font-family:inherit;background:var(--paper-warm);color:var(--ink);
  transition:border-color .15s, box-shadow .15s;
}
input::placeholder,textarea::placeholder{color:#bdb3a2;}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--gold);box-shadow:0 0 0 3px var(--gold-soft);background:var(--paper);}
.row{display:grid;grid-template-columns:1fr 1fr;gap:1rem;}
@media(max-width:560px){.row{grid-template-columns:1fr;}}

/* ---- buttons ---- */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:0.5rem;
  font-family:inherit;font-size:0.92rem;font-weight:600;cursor:pointer;
  padding:0.8rem 1.5rem;border-radius:10px;border:1px solid transparent;
  background:var(--btn-bg);color:var(--btn-txt);transition:all .15s;
}
.btn:hover{background:var(--btn-hover-bg);box-shadow:0 4px 14px var(--btn-shadow);}
.btn:disabled{background:var(--line);color:var(--muted);cursor:not-allowed;box-shadow:none;}
.btn-block{width:100%;}
.btn-gold{background:var(--gold);color:#3a2f12;}
.btn-gold:hover{background:var(--gold-deep);color:#fff;}
.btn-ghost{background:transparent;color:var(--green-mid);border-color:var(--line);}
.btn-ghost:hover{background:var(--cream);color:var(--ink);box-shadow:none;}
.btn-sm{padding:0.45rem 1rem;font-size:0.82rem;border-radius:8px;}

/* ---- badges ---- */
.badge{display:inline-block;font-size:0.72rem;font-weight:600;padding:0.18rem 0.6rem;border-radius:999px;}
.badge-ok{background:var(--ok-soft);color:var(--ok);}
.badge-warn{background:var(--warn-soft);color:var(--warn);}
.badge-danger{background:var(--danger-soft);color:var(--danger);}
.badge-gold{background:var(--gold-soft);color:var(--gold-deep);}
.badge-muted{background:var(--cream);color:var(--ink-soft);}

/* ---- status box ---- */
.statusbox{display:none;margin-top:1.4rem;padding:1rem 1.2rem;border-radius:10px;font-size:0.92rem;border:1px solid var(--line);}
.statusbox.show{display:block;}
.status-queued{background:var(--cream);border-color:var(--line);color:var(--ink-soft);}
.status-running{background:var(--green-soft);border-color:#bcd6c4;color:var(--green);}
.status-complete{background:var(--green-soft);border-color:#a9cbb4;color:var(--green);}
.status-failed{background:var(--danger-soft);border-color:#e6bdb6;color:var(--danger);}

.spinner{display:inline-block;width:13px;height:13px;border:2px solid #bcd6c4;
  border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite;
  margin-right:0.5rem;vertical-align:-1px;}
@keyframes spin{to{transform:rotate(360deg);}}

/* ---- tabs ---- */
.tab-bar{display:flex;gap:0.4rem;margin:1.4rem 0;flex-wrap:wrap;
  border-bottom:1px solid var(--line);padding-bottom:0;}
.tab{padding:0.6rem 1.1rem;border:none;background:none;color:var(--ink-soft);
  font-family:inherit;font-size:0.88rem;font-weight:500;cursor:pointer;
  border-bottom:2px solid transparent;margin-bottom:-1px;transition:all .15s;}
.tab:hover{color:var(--ink);}
.tab.active{color:var(--green);border-bottom-color:var(--gold);font-weight:600;}
.panel{display:none;}.panel.active{display:block;}

/* ---- tables ---- */
table{width:100%;border-collapse:collapse;background:var(--paper);
  border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;}
th{text-align:left;padding:0.8rem 1rem;font-size:0.72rem;letter-spacing:0.06em;
  text-transform:uppercase;color:var(--ink-soft);background:var(--paper-warm);
  border-bottom:1px solid var(--line);font-weight:600;}
td{padding:0.85rem 1rem;font-size:0.9rem;vertical-align:top;}
tr:not(:last-child) td{border-bottom:1px solid var(--line);}
tbody tr:hover td{background:var(--paper-warm);}

/* ---- misc ---- */
details.adv{margin-top:0.4rem;border-top:1px solid var(--line);padding-top:1rem;}
details.adv > summary{cursor:pointer;font-size:0.85rem;font-weight:600;color:var(--green-mid);
  list-style:none;display:flex;align-items:center;gap:0.4rem;}
details.adv > summary::-webkit-details-marker{display:none;}
details.adv > summary::before{content:'+';font-size:1.1rem;color:var(--gold-deep);width:1rem;}
details.adv[open] > summary::before{content:'–';}
.toast{position:fixed;bottom:1.5rem;right:1.5rem;background:var(--ink);color:#fff;
  padding:0.6rem 1.1rem;border-radius:8px;font-size:0.85rem;opacity:0;
  transition:opacity .3s;pointer-events:none;z-index:50;}

/* ── DARK MODE — black · gold · warm white (logo palette) ───
   Applied when data-theme="dark" on <html>, OR by system
   preference unless the user has explicitly set data-theme="light".
   ─────────────────────────────────────────────────────────── */
@media(prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --cream:#111009;--paper:#1C1A14;--paper-warm:#221F18;
    --ink:#F0EAD6;--ink-soft:#9E9282;--muted:#6A6050;--line:#2E2A20;
    --gold-deep:#DDB84E;--gold-soft:#241E0D;
    --green:#2A5E40;--green-mid:#3A7A57;--green-soft:#111F18;
    --danger:#D95B50;--danger-soft:#2A1512;
    --ok:#3A7A57;--ok-soft:#111F18;
    --warn:#C9A84C;--warn-soft:#241E0D;
    --shadow:0 6px 32px rgba(0,0,0,0.5);
    --btn-bg:var(--gold);--btn-hover-bg:#DDB84E;--btn-txt:#111009;
    --btn-shadow:rgba(201,168,76,0.35);
    --topbar-bg:#0E0D0A;--topbar-border:rgba(201,168,76,0.18);
    --navlink-txt:rgba(255,255,255,0.55);
    --navlink-hover-bg:rgba(255,255,255,0.06);--navlink-hover-txt:rgba(255,255,255,0.9);
    --navlink-active-bg:rgba(201,168,76,0.15);--navlink-active-txt:var(--gold);
  }
}
[data-theme="dark"]{
  color-scheme:dark;
  --cream:#111009;--paper:#1C1A14;--paper-warm:#221F18;
  --ink:#F0EAD6;--ink-soft:#9E9282;--muted:#6A6050;--line:#2E2A20;
  --gold-deep:#DDB84E;--gold-soft:#241E0D;
  --green:#2A5E40;--green-mid:#3A7A57;--green-soft:#111F18;
  --danger:#D95B50;--danger-soft:#2A1512;
  --ok:#3A7A57;--ok-soft:#111F18;
  --warn:#C9A84C;--warn-soft:#241E0D;
  --shadow:0 6px 32px rgba(0,0,0,0.5);
  --btn-bg:var(--gold);--btn-hover-bg:#DDB84E;--btn-txt:#111009;
  --btn-shadow:rgba(201,168,76,0.35);
  --topbar-bg:#0E0D0A;--topbar-border:rgba(201,168,76,0.18);
  --navlink-txt:rgba(255,255,255,0.55);
  --navlink-hover-bg:rgba(255,255,255,0.06);--navlink-hover-txt:rgba(255,255,255,0.9);
  --navlink-active-bg:rgba(201,168,76,0.15);--navlink-active-txt:var(--gold);
}

/* dark mode component tweaks — manual toggle */
[data-theme="dark"] input:focus,[data-theme="dark"] select:focus,[data-theme="dark"] textarea:focus{
  box-shadow:0 0 0 3px rgba(201,168,76,0.28);}
[data-theme="dark"] .status-running,[data-theme="dark"] .status-complete{border-color:var(--green-mid);}
[data-theme="dark"] .spinner{border-color:rgba(58,122,87,0.35);border-top-color:var(--green-mid);}
[data-theme="dark"] .btn-ghost{color:var(--gold);border-color:var(--line);}
[data-theme="dark"] .btn-ghost:hover{background:var(--paper-warm);color:var(--gold);}
[data-theme="dark"] .badge-muted{background:var(--paper-warm);color:var(--ink-soft);}
[data-theme="dark"] input::placeholder,[data-theme="dark"] textarea::placeholder{color:var(--muted);}

/* dark mode component tweaks — system preference */
@media(prefers-color-scheme:dark){
  :root:not([data-theme="light"]) input:focus,
  :root:not([data-theme="light"]) select:focus,
  :root:not([data-theme="light"]) textarea:focus{box-shadow:0 0 0 3px rgba(201,168,76,0.28);}
  :root:not([data-theme="light"]) .status-running,
  :root:not([data-theme="light"]) .status-complete{border-color:var(--green-mid);}
  :root:not([data-theme="light"]) .spinner{border-color:rgba(58,122,87,0.35);border-top-color:var(--green-mid);}
  :root:not([data-theme="light"]) .btn-ghost{color:var(--gold);border-color:var(--line);}
  :root:not([data-theme="light"]) .badge-muted{background:var(--paper-warm);color:var(--ink-soft);}
  :root:not([data-theme="light"]) input::placeholder,
  :root:not([data-theme="light"]) textarea::placeholder{color:var(--muted);}
}

/* theme toggle button */
.theme-btn{
  background:none;border:none;cursor:pointer;
  font-size:1.1rem;line-height:1;color:var(--gold);
  padding:0.35rem 0.5rem;border-radius:6px;transition:opacity .15s;
  opacity:0.7;
}
.theme-btn:hover{opacity:1;}

/* logo — background-image zooms past PNG whitespace without growing navbar */
.logo-slot{
  display:block;width:210px;height:52px;
  background-repeat:no-repeat;
  background-size:145% auto;
  background-position:50% 46%;
  flex-shrink:0;
}
.logo-slot.logo-light{background-image:url('/logo/light');}
.logo-slot.logo-dark {background-image:url('/logo/dark');display:none;}
[data-theme="dark"] .logo-slot.logo-light{display:none;}
[data-theme="dark"] .logo-slot.logo-dark{display:block;}
@media(prefers-color-scheme:dark){
  :root:not([data-theme="light"]) .logo-slot.logo-light{display:none;}
  :root:not([data-theme="light"]) .logo-slot.logo-dark{display:block;}
}
"""


def _theme_fouc() -> str:
    """Inline script for <head>: applies saved theme before first paint to prevent flash."""
    return (
        '<script>'
        '(function(){'
        'var t=localStorage.getItem("pikorua-theme");'
        'if(t){document.documentElement.dataset.theme=t;}'
        'else if(window.matchMedia("(prefers-color-scheme:dark)").matches){'
        'document.documentElement.dataset.theme="dark";}'
        '})();'
        '</script>'
    )


_THEME_JS = """
<script>
function _pikTheme(){
  var html=document.documentElement;
  var next=html.dataset.theme==='dark'?'light':'dark';
  html.dataset.theme=next;
  localStorage.setItem('pikorua-theme',next);
  document.querySelectorAll('.theme-btn').forEach(function(b){
    b.title=next==='dark'?'Switch to light mode':'Switch to dark mode';
    b.setAttribute('aria-label',b.title);
    b.textContent=next==='dark'?'☀':'◐';
  });
}
</script>
"""


def _topbar(active: str = "") -> str:
    """Shared top navigation bar — same on every page."""
    def cls(name):
        return "navlink active" if name == active else "navlink"
    return (
        _THEME_JS +
        '<header class="topbar">'
        '<a class="brand" href="/portal" aria-label="PIKORUA — Good People, Great Properties">'
        '<span class="logo-slot logo-light"></span>'
        '<span class="logo-slot logo-dark"></span>'
        '</a>'
        '<nav class="nav" style="align-items:center;">'
        f'<a class="{cls("new")}" href="/portal">New campaign</a>'
        f'<a class="{cls("runs")}" href="/runs">My campaigns</a>'
        '<button class="theme-btn" onclick="_pikTheme()" '
        'title="Switch to dark mode" aria-label="Switch to dark mode">◐</button>'
        '</nav>'
        '</header>'
    )


@app.get("/brand.css")
def brand_css():
    """Serve the shared brand stylesheet (one source of truth for the palette)."""
    return Response(content=BRAND_CSS, media_type="text/css")


_LOGO_DIR = Path(__file__).parent.parent.parent.parent / "project_context" / "ad_images_examples"

# Uploaded brand logo — composited onto generated images bottom-right.
# Stored as-is (usually PNG with transparency). Shared across all runs.
_BRAND_LOGO_PATH = Path("outputs") / "brand_logo.png"


@app.post("/brand-logo")
async def upload_brand_logo(request: Request):
    """Store a brand logo (PNG/JPG/WebP) to be composited onto generated images."""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="No image data received.")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo too large (max 8 MB).")
    if not (data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff"
            or data[:4] in (b"RIFF", b"WEBP") or data[:4] == b"\x89PNG"):
        raise HTTPException(status_code=400, detail="File must be PNG, JPG, or WebP.")
    _BRAND_LOGO_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Convert to RGBA PNG so we always have an alpha channel for compositing.
    from PIL import Image as _PILImage
    import io
    img = _PILImage.open(io.BytesIO(data)).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    _BRAND_LOGO_PATH.write_bytes(buf.getvalue())
    return {"ok": True, "width": img.width, "height": img.height}


@app.get("/brand-logo")
def get_brand_logo():
    """Return the stored brand logo, or 404 if none uploaded yet."""
    if not _BRAND_LOGO_PATH.exists():
        raise HTTPException(status_code=404, detail="No brand logo uploaded yet.")
    return Response(content=_BRAND_LOGO_PATH.read_bytes(), media_type="image/png")


@app.delete("/brand-logo")
def delete_brand_logo():
    """Remove the brand logo (images generated after this will have no logo)."""
    if _BRAND_LOGO_PATH.exists():
        _BRAND_LOGO_PATH.unlink()
    return {"ok": True}


@app.post("/apply-logo/{run_id}")
def apply_logo_to_run(run_id: str):
    """Composite the brand logo onto all images already on disk for this run."""
    if not _BRAND_LOGO_PATH.exists():
        raise HTTPException(status_code=400, detail="No brand logo uploaded yet.")
    run = _require_complete(run_id)
    images_dir = Path(run["review_folder"]) / "images"
    if not images_dir.exists():
        return {"ok": True, "count": 0}
    count = 0
    for img_path in sorted(images_dir.glob("image_*.png")):
        try:
            # Back up the clean version if not already done so user can revert.
            backup_dir = images_dir / ".logo_backup"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / img_path.name
            if not backup.exists():
                import shutil as _shutil
                _shutil.copy2(img_path, backup)
            _composite_logo(img_path, _BRAND_LOGO_PATH)
            count += 1
        except Exception:
            pass
    return {"ok": True, "count": count}


@app.get("/logo/light")
def logo_light():
    p = _LOGO_DIR / "without Sparkle Logo.png"
    return Response(content=p.read_bytes(), media_type="image/png")

@app.get("/logo/dark")
def logo_dark():
    p = _LOGO_DIR / "with Sparkle Logo.png"
    return Response(content=p.read_bytes(), media_type="image/png")


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/launch-campaign")
def launch_campaign(brief: CampaignBrief):
    """
    Accepts a campaign brief, queues a pipeline run in the background,
    and immediately returns a run_id so the caller can poll /status/{run_id}.

    The pipeline runs both crews sequentially:
      Stage 1 — AudienceCrew (persona, competitor intel, trends)
      Stage 2 — ContentCrew (Meta ads, Google ads, WhatsApp, email)

    Outputs are saved to outputs/pending_review/<timestamp>/ for human review.
    No ad platform API is called. DRY_RUN=true is the default.
    """
    run_id = str(uuid.uuid4())[:8]
    _runs[run_id] = {
        "status": "queued",
        "brief": brief.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "review_folder": None,
    }
    _save_runs()

    thread = threading.Thread(
        target=_run_pipeline,
        args=(run_id, brief),
        daemon=True,
        name=f"pipeline-{run_id}",
    )
    thread.start()

    return JSONResponse(
        status_code=202,
        content={
            "status": "queued",
            "run_id": run_id,
            "message": "Pipeline started. Poll /status/{run_id} for progress.",
            "poll_url": f"/status/{run_id}",
        },
    )


@app.get("/status/{run_id}")
def get_status(run_id: str):
    """Returns the current status of a pipeline run."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return _runs[run_id]


@app.get("/results/{run_id}", response_class=HTMLResponse)
def get_results(run_id: str):
    """Full detail page for a completed run — copy cards, scores, visual prompts, targeting."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = _runs[run_id]
    if run["status"] != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or no review folder.")

    review_folder = Path(run["review_folder"])
    brief = run.get("brief", {})

    def read(name):
        p = review_folder / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    scorecard_text  = read("copy_scorecard.md")
    rewrites_text   = read("copy_rewrites.md")
    persona_text    = read("persona.md")
    targeting_text  = read("targeting_brief.md")
    visual_text     = read("visual_brief.md")

    variants = _parse_scorecard(scorecard_text)
    _merge_rewrites(variants, rewrites_text)

    # Parse image prompts from visual_brief.md
    image_prompts = _parse_image_prompts(visual_text)

    # Check for already-generated images
    import os
    images_dir = review_folder / "images"
    existing_images = []
    if images_dir.exists():
        existing_images = sorted(
            [f.name for f in images_dir.iterdir() if f.name.startswith("image_") and f.name.endswith(".png")]
        )

    ideogram_key    = os.getenv("IDEOGRAM_API_KEY", "")
    replicate_token = os.getenv("REPLICATE_API_TOKEN", "")
    together_key    = os.getenv("TOGETHER_API_KEY", "")
    deploy_html     = _build_deploy_html(run_id, run, brief)

    # Determine which variants to pre-check: top 2–3 PASS by avg score
    already_selected = run.get("selected_variants", [])
    if already_selected:
        default_selected = set(already_selected)
    else:
        # Candidates = clean passes PLUS variants that were flagged but auto-rewritten
        # (their displayed copy is the corrected version, so they're launch-eligible too).
        candidates = [v for v in variants
                      if v.get("status") == "PASS" or (v.get("status") == "FLAG" and v.get("rewrite"))]
        candidates.sort(
            key=lambda v: sum(v.get("scores", {}).values()) / max(len(v.get("scores", {})), 1),
            reverse=True,
        )
        default_selected = {v["variant"] for v in candidates[:3]}

    # Effective Meta copy folds in user edits, rewrites, added & deleted versions.
    eff_meta = _effective_meta(review_folder)
    sc_by_num = {v.get("variant"): v for v in variants}
    edits_overlay = _load_edits(review_folder)
    deleted_nums = sorted(edits_overlay.get("deleted_variants", []))

    # Build variant cards HTML — iterate the effective set so user-added versions
    # appear and deleted ones drop out. Scorecard data (scores/flag/angle) is
    # matched in by number where it exists.
    variant_cards_html = ""
    for num in sorted(eff_meta.keys()):
        emc = eff_meta[num]
        info = sc_by_num.get(num, {})
        added = emc.get("added", False)
        edited = emc.get("edited", False)
        angle = info.get("angle", "") or ("Your custom version" if added else "")
        status = info.get("status")            # PASS / FLAG / None (added)
        scores = info.get("scores", {})
        flag_reason = info.get("flag_reason", "")
        # A flagged variant that was auto-rewritten now shows the corrected copy.
        # Present it as "Revised" (amber), not a bare red FLAG, so good copy that
        # was already fixed doesn't look rejected.
        revised = status == "FLAG" and bool(info.get("rewrite"))
        headline = emc.get("headline", "")
        body = emc.get("body", "")

        if revised:
            status_colour, status_bg = "var(--gold-deep)", "var(--gold-soft)"
            card_border = "rgba(176,141,87,0.30)"
            status_label = "REVISED"
        elif status == "FLAG":
            status_colour, status_bg = "var(--danger)", "var(--danger-soft)"
            card_border = "rgba(178,59,46,0.22)"
            status_label = "FLAG"
        elif status == "PASS":
            status_colour, status_bg = "var(--ok)", "var(--ok-soft)"
            card_border = "rgba(46,87,64,0.22)"
            status_label = "PASS"
        else:                                  # user-added — no AI score
            status_colour, status_bg = "var(--ink-soft)", "var(--paper-warm)"
            card_border = "var(--line)"
            status_label = None

        score_bars = ""
        avg_score = None
        if scores:
            avg_score = round(sum(scores.values()) / len(scores), 1)
            dim_labels = {"brand_voice": "Brand Voice", "platform_fit": "Platform Fit",
                          "specificity": "Specificity", "luxury_signal": "Luxury Signal"}
            for key, label in dim_labels.items():
                val = scores.get(key, 0)
                bar_w = val * 10
                bar_colour = "var(--ok)" if val >= 7 else ("var(--warn)" if val >= 5 else "var(--danger)")
                score_bars += f"""
                <div style="margin-bottom:6px;">
                  <div style="display:flex;justify-content:space-between;font-size:0.72rem;color:var(--ink-soft);margin-bottom:2px;">
                    <span>{label}</span><span style="color:{bar_colour};font-weight:bold;">{val}/10</span>
                  </div>
                  <div style="background:var(--line);border-radius:2px;height:5px;">
                    <div style="background:{bar_colour};width:{bar_w}%;height:5px;border-radius:2px;"></div>
                  </div>
                </div>"""

        avg_html = f'<span style="font-size:1.1rem;font-weight:bold;color:var(--ink);">{avg_score}/10</span>' if avg_score else ""
        status_badge = (f'<span style="background:{status_bg};color:{status_colour};padding:2px 10px;'
                        f'border-radius:2px;font-size:0.72rem;letter-spacing:0.06em;">{status_label}</span>'
                        if status_label else
                        '<span style="background:var(--paper-warm);color:var(--ink-soft);padding:2px 10px;'
                        'border-radius:2px;font-size:0.72rem;letter-spacing:0.06em;">CUSTOM</span>')
        edited_badge = (f'<span id="editbadge-{num}" style="display:{"inline-block" if edited else "none"};'
                        'background:var(--gold-soft);color:var(--gold-deep);padding:2px 8px;border-radius:2px;'
                        'font-size:0.66rem;letter-spacing:0.05em;margin-left:6px;">EDITED</span>')

        flag_html = ""
        if revised:
            flag_html = (f'<div style="background:var(--gold-soft);border-left:3px solid var(--gold-deep);'
                         f'padding:8px 12px;margin:10px 0;font-size:0.8rem;color:var(--gold-deep);">'
                         f'<strong>Auto-revised</strong> &mdash; originally flagged ({_esc(flag_reason)}). '
                         f'The copy shown below is the corrected version.</div>')
        elif status == "FLAG":
            flag_html = f'<div style="background:var(--danger-soft);border-left:3px solid var(--danger);padding:8px 12px;margin:10px 0;font-size:0.8rem;color:var(--danger);"><strong>FLAG</strong> &mdash; {_esc(flag_reason)}</div>'

        # ── VIEW mode (default) — shows current effective copy ──
        view_html = f"""
            <div id="view-{num}">
              <div style="margin-bottom:6px;">
                {f'<div style="font-size:0.7rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--muted);margin-bottom:4px;">Headline</div><div id="hl-{num}" style="font-size:0.95rem;font-weight:bold;color:var(--ink);margin-bottom:8px;">{_esc(headline)}</div>' if headline else f'<div id="hl-{num}" style="font-size:0.85rem;color:var(--muted);margin-bottom:8px;">(no headline yet)</div>'}
                {f'<div style="font-size:0.7rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--muted);margin-bottom:4px;">Body</div><div id="bd-{num}" style="font-size:0.85rem;color:var(--ink-soft);line-height:1.6;">{_esc(body)}</div>' if body else f'<div id="bd-{num}" style="font-size:0.85rem;color:var(--muted);">(no body yet)</div>'}
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
                <button class="mini-btn" onclick="startEdit({num})">Edit</button>
                <button class="mini-btn" onclick="duplicateVariant('{run_id}',{num})">Duplicate</button>
                <button class="mini-btn" onclick="copyFromData(this)" data-copy="{_esc(headline)} — {_esc(body)}">Copy</button>
                <button class="mini-btn mini-btn-danger" onclick="deleteVariant('{run_id}',{num},{str(added).lower()})">Delete</button>
              </div>
            </div>"""

        # ── EDIT mode (hidden until "Edit") — textareas + live char counters ──
        edit_html = f"""
            <div id="edit-{num}" style="display:none;">
              <label class="edit-label">Headline <span class="char-count" id="cc-hl-{num}"></span></label>
              <textarea id="ehl-{num}" class="edit-input" rows="2"
                oninput="updateCount('hl',{num},40)">{_esc(headline)}</textarea>
              <label class="edit-label">Body <span class="char-count" id="cc-bd-{num}"></span></label>
              <textarea id="ebd-{num}" class="edit-input" rows="4"
                oninput="updateCount('bd',{num},125)">{_esc(body)}</textarea>
              <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
                <button class="mini-btn mini-btn-primary" onclick="saveEdit('{run_id}',{num})">Save</button>
                <button class="mini-btn" onclick="cancelEdit({num})">Cancel</button>
                <button class="mini-btn" onclick="revertVariant('{run_id}',{num},{str(added).lower()})">Revert to original</button>
              </div>
            </div>"""

        # ── Image control (pick any generated image, upload, or revert) ──
        # Resolved order: user's explicit assignment → image_{num}.png → nothing
        assigned_img_num = edits_overlay.get("meta", {}).get(str(num), {}).get("image_num")
        if assigned_img_num is None and (images_dir / f"image_{num}.png").exists():
            assigned_img_num = num
        has_img = assigned_img_num is not None
        img_preview = (
            f'<img id="thumb-{num}" src="/image/{run_id}/image_{assigned_img_num}.png" '
            f'style="width:100%;max-width:240px;border-radius:6px;border:1px solid var(--line);'
            f'display:block;margin-bottom:8px;cursor:zoom-in;" '
            f'onclick="openLightbox(\'/image/{run_id}/image_{assigned_img_num}.png\', \'Version {num}\')">'
            if has_img else
            f'<div id="thumb-{num}" style="font-size:0.78rem;color:var(--muted);margin-bottom:8px;">'
            f'No image — generate in the Images tab or upload your own.</div>'
        )
        # Dropdown listing all generated images so any can be assigned to this variant
        img_options = '<option value="">— None / upload your own —</option>'
        for fname in existing_images:
            try:
                img_n = int(fname[len("image_"):-len(".png")])
            except ValueError:
                continue
            sel = "selected" if img_n == assigned_img_num else ""
            label = f"Image {img_n}"
            if img_n <= len(image_prompts):
                ptitle = image_prompts[img_n - 1][0][:35]
                label = f"Image {img_n} — {ptitle}"
            img_options += f'<option value="{img_n}" {sel}>{_esc(label)}</option>'
        img_select_html = (
            f'<div style="margin-bottom:10px;">'
            f'<label style="font-size:0.78rem;color:var(--ink-soft);display:block;margin-bottom:4px;">Use generated image</label>'
            f'<select id="imgsel-{num}" onchange="assignImage(\'{run_id}\',{num},this.value)" '
            f'style="padding:5px 8px;border:1px solid var(--line);border-radius:6px;'
            f'background:var(--paper);color:var(--ink);font-size:0.8rem;max-width:280px;">'
            f'{img_options}</select></div>'
            if existing_images else
            '<div style="font-size:0.78rem;color:var(--muted);margin-bottom:8px;">'
            'No generated images yet — go to the Images tab to create some.</div>'
        )
        image_block = f"""
            <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line);">
              <div class="eyebrow" style="margin-bottom:8px;">Image</div>
              {img_preview}
              {img_select_html}
              <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <label class="mini-btn" style="cursor:pointer;display:inline-flex;align-items:center;">Upload your own
                  <input type="file" accept="image/*" style="display:none;"
                    onchange="uploadImage('{run_id}',{num},this)">
                </label>
                <button class="mini-btn" onclick="revertImage('{run_id}',{num})">Revert image</button>
              </div>
              <div id="imgstatus-{num}" style="font-size:0.74rem;color:var(--ink-soft);margin-top:6px;min-height:1em;"></div>
            </div>"""

        checked = "checked" if num in default_selected else ""
        select_checkbox = f"""
          <label style="display:flex;align-items:center;gap:6px;font-size:0.72rem;color:var(--ink-soft);
            cursor:pointer;margin-top:6px;user-select:none;justify-content:flex-end;">
            <input type="checkbox" id="sel-{num}" value="{num}" {checked}
              style="width:14px;height:14px;accent-color:var(--green);cursor:pointer;">
            Launch
          </label>"""

        variant_cards_html += f"""
        <div id="card-{num}" style="background:var(--paper);border:1px solid {card_border};border-radius:4px;padding:20px;margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
            <div>
              <span class="eyebrow">Version {num}</span>{edited_badge}
              <div style="font-family:'Cormorant Garamond',serif;font-size:1.15rem;color:var(--ink);margin-top:2px;">{_esc(angle)}</div>
            </div>
            <div style="text-align:right;">
              {status_badge}
              <div style="margin-top:4px;">{avg_html}</div>
              {select_checkbox}
            </div>
          </div>
          {flag_html}
          {view_html}
          {edit_html}
          {image_block}
          <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line);">
            {score_bars if score_bars else '<div style="font-size:0.74rem;color:var(--muted);">Custom version — not AI-scored.</div>'}
          </div>
        </div>"""

    # "Add version" button + restore-deleted link
    restore_html = ""
    if deleted_nums:
        chips = " ".join(
            f'<button class="mini-btn" onclick="restoreVariant(\'{run_id}\',{d})">Restore Version {d}</button>'
            for d in deleted_nums
        )
        restore_html = (f'<div style="margin:4px 0 16px;font-size:0.8rem;color:var(--ink-soft);">'
                        f'Deleted: {chips}</div>')
    add_variant_html = (
        f'<div style="margin-bottom:18px;">'
        f'<button class="mini-btn mini-btn-primary" onclick="addVariant(\'{run_id}\')">+ Add a version</button>'
        f'</div>{restore_html}')

    # Other copy sections (Google, WhatsApp, Email) — editable textareas with
    # Save / Revert, backed by the same overlay as the Meta versions.
    other_copy_html = ""
    section_labels = [
        ("google", "Google Ads"),
        ("whatsapp", "WhatsApp Script"),
        ("email", "Email"),
    ]
    for key, label in section_labels:
        text, was_edited = _effective_channel(review_folder, key)
        if not text:
            continue
        edited_tag = (f'<span id="chedit-{key}" style="display:{"inline-block" if was_edited else "none"};'
                      'background:var(--gold-soft);color:var(--gold-deep);padding:2px 8px;border-radius:2px;'
                      'font-size:0.66rem;letter-spacing:0.05em;margin-left:8px;">EDITED</span>')
        other_copy_html += f"""
            <div style="margin-bottom:28px;">
              <h3 style="font-size:0.78rem;letter-spacing:0.12em;text-transform:uppercase;
                color:var(--ink-soft);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--line);">{label}{edited_tag}</h3>
              <textarea id="ta-{key}" class="edit-input" rows="8"
                style="font-family:'Georgia',serif;font-size:0.85rem;line-height:1.7;">{_esc(text)}</textarea>
              <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
                <button class="mini-btn mini-btn-primary" onclick="saveChannel('{run_id}','{key}')">Save</button>
                <button class="mini-btn" onclick="revertChannel('{run_id}','{key}')">Revert to original</button>
                <button class="mini-btn" onclick="copyTextarea('{key}')">Copy</button>
                <span id="chstatus-{key}" style="font-size:0.74rem;color:var(--ink-soft);align-self:center;"></span>
              </div>
            </div>"""

    # Persona section
    persona_html = ""
    if persona_text:
        persona_html = f"""<pre style="white-space:pre-wrap;font-family:'Georgia',serif;font-size:0.84rem;
          color:var(--ink);line-height:1.7;background:var(--paper-warm);padding:14px;
          border:1px solid var(--line);border-radius:3px;">{_esc(persona_text.strip())}</pre>"""

    # Targeting brief section
    targeting_html = ""
    if targeting_text:
        targeting_html = f"""<pre style="white-space:pre-wrap;font-family:'Georgia',serif;font-size:0.82rem;
          color:var(--ink);line-height:1.7;background:var(--paper-warm);padding:14px;
          border:1px solid var(--line);border-radius:3px;">{_esc(targeting_text.strip())}</pre>"""

    scorecard_summary = run.get("copy_scorecard_summary", "")

    if run.get("approved"):
        sel = run.get("selected_variants", [])
        sel_label = ", ".join(f"Version {v}" for v in sel) if sel else "all versions"
        approve_bar_html = (
            f'<div style="margin-top:12px;padding:12px 16px;background:var(--green-soft);'
            f'border:1px solid #bcd6c4;border-radius:10px;font-size:0.9rem;color:var(--green);">'
            f'&#10003; Approved — {_esc(sel_label)} saved.</div>'
        )
    else:
        approve_bar_html = f"""
    <div id="approve-bar" style="display:flex;align-items:center;gap:16px;margin-top:14px;
      padding:16px;background:var(--green-soft);border:1px solid #bcd6c4;border-radius:12px;flex-wrap:wrap;">
      <div style="flex:1;min-width:240px;font-size:0.9rem;color:var(--green);">
        Tick the versions you want to launch, then approve.
        <span style="color:var(--ink-soft);">Your budget splits across the ones you pick — 2–3 works best.</span>
      </div>
      <button id="approve-selected-btn" class="btn" onclick="approveSelected('{run_id}')"
        style="white-space:nowrap;">Approve selected</button>
    </div>
    <div id="approve-status" style="font-size:0.85rem;color:var(--ink-soft);margin-top:8px;min-height:1.2em;"></div>"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  {_theme_fouc()}
  <title>PIKORUA — {_esc(brief.get('property_name','Campaign'))}</title>
  <link rel="stylesheet" href="/brand.css"/>
  <style>
    .meta-row{{font-size:0.9rem;color:var(--ink-soft);margin:0.3rem 0 0.5rem;}}
    .meta-row .dot{{color:var(--line);margin:0 0.4rem;}}
    .toast{{}} /* defined in brand.css */
    /* ── inline editing controls ── */
    .mini-btn{{background:var(--paper);border:1px solid var(--line);padding:5px 12px;
      font-size:0.74rem;color:var(--ink-soft);cursor:pointer;border-radius:5px;
      font-family:inherit;transition:all .15s;}}
    .mini-btn:hover{{border-color:var(--gold);color:var(--ink);}}
    .mini-btn-primary{{background:var(--green);color:#fff;border-color:var(--green);}}
    .mini-btn-primary:hover{{background:var(--green-mid);color:#fff;}}
    .mini-btn-danger:hover{{border-color:var(--danger);color:var(--danger);}}
    .edit-label{{display:block;font-size:0.7rem;letter-spacing:0.1em;text-transform:uppercase;
      color:var(--muted);margin:10px 0 4px;}}
    .char-count{{text-transform:none;letter-spacing:0;color:var(--muted);font-size:0.7rem;}}
    .char-count.over{{color:var(--warn);font-weight:bold;}}
    .edit-input{{width:100%;box-sizing:border-box;border:1px solid var(--line);border-radius:6px;
      padding:9px 11px;font-family:inherit;font-size:0.88rem;color:var(--ink);background:var(--paper-warm);
      line-height:1.5;resize:vertical;}}
    .edit-input:focus{{outline:none;border-color:var(--gold);background:var(--paper);}}
  </style>
</head>
<body>
  {_topbar('runs')}
  <div class="wrap-wide">
  <div class="eyebrow">Campaign results</div>
  <h1>{_esc(brief.get('property_name','Campaign'))}</h1>
  <div class="meta-row">
    {_esc(brief.get('city',''))}<span class="dot">·</span>
    ₹{_esc(str(brief.get('price_cr','')))} Cr<span class="dot">·</span>
    {_esc(brief.get('platform',''))}<span class="dot">·</span>
    {_esc(brief.get('property_type',''))}
    {f'<span class="dot">·</span><strong style="color:var(--green-mid);">{_esc(scorecard_summary)}</strong>' if scorecard_summary else ""}
  </div>

  {_build_logo_section_html(run_id)}

  <div class="tab-bar">
    <button class="tab active" onclick="showTab('meta')">Facebook &amp; Instagram ads</button>
    <button class="tab" onclick="showTab('other')">Google · WhatsApp · Email</button>
    <button class="tab" onclick="showTab('visuals')">Images</button>
    <button class="tab" onclick="showTab('audience')">Buyers &amp; targeting</button>
    <button class="tab" onclick="showTab('deploy')">Publish</button>
  </div>

  <div id="tab-meta" class="panel active">
    <h2 style="margin-top:0.5rem;">Your ad versions</h2>
    <p class="section-sub">Edit any headline, body or image to fine-tune a version — your changes save instantly and can be reverted. Pick the ones you like, then approve. We recommend launching 2–3.</p>
    {add_variant_html}
    {variant_cards_html if variant_cards_html else '<p style="color:var(--muted);font-size:0.9rem;">No ad copy found for this campaign.</p>'}
    {approve_bar_html}
  </div>

  <div id="tab-other" class="panel">
    <h2 style="margin-top:0.5rem;">WhatsApp, Email &amp; Google copy</h2>
    <p class="section-sub">Edit any message below and click Save. Revert restores the original AI version.</p>
    {other_copy_html if other_copy_html else '<p style="color:var(--muted);font-size:0.9rem;">No copy found for these channels.</p>'}
  </div>

  <div id="tab-visuals" class="panel">
    <h2 style="margin-top:0.5rem;">Campaign images</h2>
    <p class="section-sub">
      We create a set of ad images for you — social banners with text, plus clean lifestyle shots.
    </p>
    {_build_visuals_html(run_id, image_prompts, existing_images, ideogram_key, replicate_token, together_key, images_dir)}
  </div>

  <div id="tab-audience" class="panel">
    <h2 style="margin-top:0.5rem;">Who we'd target</h2>
    <p class="section-sub">The ideal buyer for this property, and how we'd reach them.</p>
    {persona_html if persona_html else '<p style="color:var(--muted);font-size:0.9rem;">No buyer profile found.</p>'}
    <h2 style="margin-top:1.6rem;">Targeting plan</h2>
    {targeting_html if targeting_html else '<p style="color:var(--muted);font-size:0.9rem;">No targeting plan found.</p>'}
  </div>

  <div id="tab-deploy" class="panel">
    <h2 style="margin-top:0.5rem;">Publish to Facebook &amp; Instagram</h2>
    <p class="section-sub">Preview exactly how your ads will look before anything goes live.</p>
    {deploy_html}
  </div>

  <p style="margin-top:2.4rem;font-size:0.9rem;">
    <a href="/runs">&#8592; Back to my campaigns</a>
  </p>
  </div>
  <div class="toast" id="copy-notice">Copied</div>

  <script>
    function showTab(name) {{
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      document.querySelectorAll('.tab').forEach(t => {{
        if (t.getAttribute('onclick') === "showTab('" + name + "')") t.classList.add('active');
      }});
    }}
    // Restore tab saved before a page reload (e.g. after image generation)
    (function() {{
      const t = sessionStorage.getItem('activeTab');
      if (t) {{ sessionStorage.removeItem('activeTab'); showTab(t); }}
    }})();
    function copyText(btn, text) {{
      navigator.clipboard.writeText(text).then(() => {{
        const n = document.getElementById('copy-notice');
        n.style.opacity = 1;
        setTimeout(() => n.style.opacity = 0, 1600);
      }});
    }}
    function copyFromData(btn) {{
      navigator.clipboard.writeText(btn.getAttribute('data-copy')).then(() => {{
        const n = document.getElementById('copy-notice');
        n.style.opacity = 1;
        setTimeout(() => n.style.opacity = 0, 1600);
      }});
    }}
    function toast() {{
      const n = document.getElementById('copy-notice');
      n.style.opacity = 1; setTimeout(() => n.style.opacity = 0, 1400);
    }}
    function copyTextarea(key) {{
      const ta = document.getElementById('ta-' + key);
      navigator.clipboard.writeText(ta.value).then(toast);
    }}

    // ── Inline editing: Meta versions ──
    function startEdit(num) {{
      document.getElementById('view-' + num).style.display = 'none';
      document.getElementById('edit-' + num).style.display = 'block';
      updateCount('hl', num, 40); updateCount('bd', num, 125);
    }}
    function cancelEdit(num) {{
      document.getElementById('edit-' + num).style.display = 'none';
      document.getElementById('view-' + num).style.display = 'block';
    }}
    function updateCount(field, num, limit) {{
      const ta = document.getElementById('e' + field + '-' + num);
      const cc = document.getElementById('cc-' + field + '-' + num);
      if (!ta || !cc) return;
      const n = ta.value.length;
      cc.textContent = n + ' / ' + limit + (n > limit ? ' — over Meta\\'s recommended limit' : '');
      cc.classList.toggle('over', n > limit);
    }}
    async function saveEdit(runId, num) {{
      const headline = document.getElementById('ehl-' + num).value;
      const body = document.getElementById('ebd-' + num).value;
      try {{
        const res = await fetch('/edit-content/' + runId, {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{channel: 'meta', variant: num, headline, body}}),
        }});
        if (!res.ok) {{ const d = await res.json(); alert('Could not save: ' + (d.detail||'error')); return; }}
        document.getElementById('hl-' + num).textContent = headline || '(no headline yet)';
        document.getElementById('bd-' + num).textContent = body || '(no body yet)';
        const eb = document.getElementById('editbadge-' + num);
        if (eb) eb.style.display = 'inline-block';
        cancelEdit(num); toast();
      }} catch(e) {{ alert('Request failed: ' + e.message); }}
    }}
    async function revertVariant(runId, num, added) {{
      if (added) {{
        if (!confirm('This is a custom version. Reverting removes it. Continue?')) return;
      }}
      try {{
        const res = await fetch('/revert-content/' + runId, {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{channel: 'meta', variant: num}}),
        }});
        const d = await res.json();
        if (!res.ok) {{ alert('Could not revert: ' + (d.detail||'error')); return; }}
        if (d.removed) {{ reloadMeta(); return; }}
        document.getElementById('ehl-' + num).value = d.headline || '';
        document.getElementById('ebd-' + num).value = d.body || '';
        document.getElementById('hl-' + num).textContent = d.headline || '(no headline yet)';
        document.getElementById('bd-' + num).textContent = d.body || '(no body yet)';
        const eb = document.getElementById('editbadge-' + num);
        if (eb) eb.style.display = 'none';
        cancelEdit(num); toast();
      }} catch(e) {{ alert('Request failed: ' + e.message); }}
    }}
    async function addVariant(runId) {{
      const res = await fetch('/add-variant/' + runId, {{method: 'POST'}});
      if (res.ok) reloadMeta(); else alert('Could not add a version.');
    }}
    async function duplicateVariant(runId, num) {{
      const res = await fetch('/duplicate-variant/' + runId, {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{channel: 'meta', variant: num}}),
      }});
      if (res.ok) reloadMeta(); else alert('Could not duplicate.');
    }}
    async function deleteVariant(runId, num, added) {{
      const msg = added ? 'Delete this custom version?'
        : 'Delete Version ' + num + '? You can restore it afterwards.';
      if (!confirm(msg)) return;
      const res = await fetch('/delete-variant/' + runId, {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{channel: 'meta', variant: num}}),
      }});
      if (res.ok) reloadMeta(); else alert('Could not delete.');
    }}
    async function restoreVariant(runId, num) {{
      const res = await fetch('/restore-variant/' + runId, {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{channel: 'meta', variant: num}}),
      }});
      if (res.ok) reloadMeta(); else alert('Could not restore.');
    }}
    function reloadMeta() {{ sessionStorage.setItem('activeTab', 'meta'); window.location.reload(); }}

    // ── Inline editing: Google / WhatsApp / Email ──
    async function saveChannel(runId, key) {{
      const text = document.getElementById('ta-' + key).value;
      const status = document.getElementById('chstatus-' + key);
      const res = await fetch('/edit-content/' + runId, {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{channel: key, text}}),
      }});
      if (res.ok) {{
        const tag = document.getElementById('chedit-' + key);
        if (tag) tag.style.display = 'inline-block';
        if (status) {{ status.style.color = 'var(--green)'; status.textContent = 'Saved.'; }}
        toast();
      }} else if (status) {{ status.style.color = 'var(--danger)'; status.textContent = 'Save failed.'; }}
    }}
    async function revertChannel(runId, key) {{
      const status = document.getElementById('chstatus-' + key);
      const res = await fetch('/revert-content/' + runId, {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{channel: key}}),
      }});
      const d = await res.json();
      if (res.ok) {{
        document.getElementById('ta-' + key).value = d.text || '';
        const tag = document.getElementById('chedit-' + key);
        if (tag) tag.style.display = 'none';
        if (status) {{ status.style.color = 'var(--ink-soft)'; status.textContent = 'Reverted to original.'; }}
      }} else if (status) {{ status.style.color = 'var(--danger)'; status.textContent = 'Revert failed.'; }}
    }}

    // ── Image assignment — pick any generated image for a variant ──
    async function assignImage(runId, variantNum, imageNumStr) {{
      const imageNum = imageNumStr ? parseInt(imageNumStr) : null;
      const status = document.getElementById('imgstatus-' + variantNum);
      try {{
        const res = await fetch('/assign-image/' + runId + '/' + variantNum, {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{image_num: imageNum}})
        }});
        if (!res.ok) throw new Error(await res.text());
        // Swap the thumbnail immediately without a page reload
        const thumbEl = document.getElementById('thumb-' + variantNum);
        if (thumbEl) {{
          if (imageNum) {{
            thumbEl.outerHTML = `<img id="thumb-${{variantNum}}" src="/image/${{runId}}/image_${{imageNum}}.png"
              style="width:100%;max-width:240px;border-radius:6px;border:1px solid var(--line);
              display:block;margin-bottom:8px;cursor:zoom-in;"
              onclick="openLightbox('/image/${{runId}}/image_${{imageNum}}.png','Version ${{variantNum}}')">`;
          }} else {{
            thumbEl.outerHTML = `<div id="thumb-${{variantNum}}" style="font-size:0.78rem;color:var(--muted);margin-bottom:8px;">No image assigned.</div>`;
          }}
        }}
        if (status) {{ status.style.color = 'var(--green)'; status.textContent = imageNum ? 'Image assigned.' : 'Cleared.'; setTimeout(()=>{{if(status)status.textContent='';}},2000); }}
      }} catch(e) {{
        if (status) {{ status.style.color = 'var(--danger)'; status.textContent = 'Error: ' + e.message; }}
      }}
    }}

    // ── Image upload / revert per version ──
    async function uploadImage(runId, num, input) {{
      const file = input.files[0];
      const status = document.getElementById('imgstatus-' + num);
      if (!file) return;
      if (status) {{ status.style.color = 'var(--ink-soft)'; status.textContent = 'Uploading…'; }}
      try {{
        const res = await fetch('/upload-image/' + runId + '/' + num, {{
          method: 'POST', headers: {{'Content-Type': file.type || 'application/octet-stream'}},
          body: file,
        }});
        const d = await res.json();
        if (res.ok) {{ sessionStorage.setItem('activeTab', 'meta'); window.location.reload(); }}
        else if (status) {{ status.style.color = 'var(--danger)'; status.textContent = d.detail || 'Upload failed.'; }}
      }} catch(e) {{ if (status) {{ status.style.color = 'var(--danger)'; status.textContent = 'Upload failed: ' + e.message; }} }}
    }}
    async function revertImage(runId, num) {{
      if (!confirm('Revert to the original image (or remove your upload)?')) return;
      const res = await fetch('/revert-image/' + runId + '/' + num, {{method: 'POST'}});
      if (res.ok) {{ sessionStorage.setItem('activeTab', 'meta'); window.location.reload(); }}
      else alert('Could not revert image.');
    }}
    async function approveSelected(runId) {{
      const checkboxes = document.querySelectorAll('input[id^="sel-"]');
      const selected = Array.from(checkboxes)
        .filter(cb => cb.checked)
        .map(cb => parseInt(cb.value));
      if (selected.length === 0) {{
        document.getElementById('approve-status').textContent = 'Select at least one variant before approving.';
        return;
      }}
      const btn = document.getElementById('approve-selected-btn');
      const status = document.getElementById('approve-status');
      btn.disabled = true;
      btn.textContent = 'Storing…';
      status.textContent = '';
      try {{
        const res = await fetch('/approve/' + runId, {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{selected_variants: selected}}),
        }});
        const data = await res.json();
        if (res.ok) {{
          document.getElementById('approve-bar').style.display = 'none';
          status.style.color = 'var(--green)';
          status.textContent = '✓ Approved — Version ' + selected.join(', ') + ' saved.';
        }} else {{
          btn.disabled = false;
          btn.textContent = 'Approve Selected';
          status.style.color = 'var(--danger)';
          status.textContent = 'Error: ' + (data.detail || 'Unknown error');
        }}
      }} catch(e) {{
        btn.disabled = false;
        btn.textContent = 'Approve Selected';
        status.style.color = '#8a2020';
        status.textContent = 'Request failed: ' + e.message;
      }}
    }}
    async function deployToMeta(runId) {{
      const btn = document.getElementById('deploy-btn');
      const status = document.getElementById('deploy-status');
      btn.disabled = true;
      btn.textContent = 'Working…';
      if (status) status.textContent = '';
      try {{
        const res = await fetch('/deploy-to-meta/' + runId, {{method: 'POST'}});
        const data = await res.json();
        if (res.ok) {{
          const deployed = data.deployed || [];
          const apiErrors = data.errors || [];
          const dryCount = deployed.filter(r => r.dry_run).length;
          const okCount  = deployed.filter(r => !r.dry_run).length;
          // Nothing published — reload so the detailed, plain-English error
          // banner (with "you can fix this" / "saved to developer log" tags) shows.
          if (deployed.length === 0 && apiErrors.length > 0) {{
            if (status) {{ status.style.color = 'var(--danger)'; status.textContent = 'Couldn\\'t publish — see details below.'; }}
            sessionStorage.setItem('activeTab', 'deploy');
            setTimeout(() => window.location.reload(), 700);
            return;
          }}
          const label = dryCount > 0
            ? 'Preview ready — loading…'
            : okCount + ' ad(s) created (PAUSED) — loading…';
          if (status) {{ status.style.color = 'var(--green)'; status.textContent = label; }}
          sessionStorage.setItem('activeTab', 'deploy');
          setTimeout(() => window.location.reload(), 900);
        }} else {{
          btn.disabled = false;
          btn.textContent = 'Preview & publish';
          if (status) {{ status.style.color = 'var(--danger)'; status.textContent = 'Error: ' + (data.detail || 'Unknown error'); }}
        }}
      }} catch(e) {{
        btn.disabled = false;
        btn.textContent = 'Preview & publish';
        if (status) {{ status.style.color = 'var(--danger)'; status.textContent = 'Request failed: ' + e.message; }}
      }}
    }}
    async function uploadBrandLogo(input) {{
      const file = input.files[0];
      if (!file) return;
      const status = document.getElementById('logo-status') || document.getElementById('gen-status');
      if (status) status.textContent = 'Uploading…';
      try {{
        const res = await fetch('/brand-logo', {{
          method: 'POST',
          headers: {{'Content-Type': file.type || 'image/png'}},
          body: file
        }});
        const data = await res.json();
        if (res.ok) {{
          window.location.reload();
        }} else {{
          if (status) status.textContent = 'Upload failed: ' + (data.detail || 'unknown error');
        }}
      }} catch(e) {{
        if (status) status.textContent = 'Upload failed: ' + e.message;
      }}
    }}

    async function removeBrandLogo() {{
      const status = document.getElementById('logo-status') || document.getElementById('gen-status');
      try {{
        await fetch('/brand-logo', {{method: 'DELETE'}});
        window.location.reload();
      }} catch(e) {{
        if (status) status.textContent = 'Remove failed: ' + e.message;
      }}
    }}

    async function applyLogoToImages(runId) {{
      const status = document.getElementById('logo-status');
      if (status) status.textContent = 'Applying logo…';
      try {{
        const res = await fetch('/apply-logo/' + runId, {{method: 'POST'}});
        const data = await res.json();
        if (res.ok) {{
          if (status) status.textContent = 'Logo applied to ' + data.count + ' image(s). Reloading…';
          setTimeout(() => window.location.reload(), 1200);
        }} else {{
          if (status) status.textContent = 'Failed: ' + (data.detail || 'unknown error');
        }}
      }} catch(e) {{
        if (status) status.textContent = 'Failed: ' + e.message;
      }}
    }}

    async function revertLogo(runId, promptNum) {{
      const status = document.getElementById('gen-status');
      status.textContent = 'Reverting…';
      try {{
        const res = await fetch('/revert-logo/' + runId + '/' + promptNum, {{method: 'POST'}});
        const data = await res.json();
        if (res.ok) {{
          sessionStorage.setItem('activeTab', 'visuals');
          window.location.reload();
        }} else {{
          status.textContent = 'Revert failed: ' + (data.detail || 'unknown error');
        }}
      }} catch(e) {{
        status.textContent = 'Revert failed: ' + e.message;
      }}
    }}

    function imgSelectAll(on) {{
      document.querySelectorAll('.img-row .img-sel').forEach(cb => {{
        cb.checked = on;
        imgCheckChanged(cb);
      }});
    }}

    function imgCheckChanged(cb) {{
      const row = cb.closest('.img-row');
      const hasImg = row.dataset.hasImg === '1';
      const replaceRow = row.querySelector('.img-replace-row');
      if (replaceRow) {{
        replaceRow.style.display = cb.checked ? 'flex' : 'none';
      }}
    }}

    function revertPrompt(btn) {{
      const ta = btn.closest('details').querySelector('.img-prompt-edit');
      ta.value = ta.dataset.original;
      btn.closest('details').querySelector('.img-edited-badge').style.display = 'none';
    }}

    function openLightbox(src, caption) {{
      let box = document.getElementById('img-lightbox');
      if (!box) {{
        box = document.createElement('div');
        box.id = 'img-lightbox';
        box.onclick = closeLightbox;
        box.style.cssText = 'position:fixed;inset:0;background:rgba(20,18,16,0.88);z-index:9999;'
          + 'display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:zoom-out;padding:24px;';
        box.innerHTML = '<img id="img-lightbox-img" style="max-width:92vw;max-height:82vh;'
          + 'border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,0.5);">'
          + '<div id="img-lightbox-cap" style="color:#f2ece4;font-size:0.9rem;margin-top:14px;text-align:center;"></div>'
          + '<a id="img-lightbox-open" target="_blank" rel="noopener" onclick="event.stopPropagation();" '
          + 'style="color:#d9c4a3;font-size:0.82rem;margin-top:8px;text-decoration:underline;cursor:pointer;">'
          + 'Open in new tab</a>';
        document.body.appendChild(box);
      }}
      document.getElementById('img-lightbox-img').src = src;
      document.getElementById('img-lightbox-cap').textContent = caption || '';
      document.getElementById('img-lightbox-open').href = src;
      box.style.display = 'flex';
    }}
    function closeLightbox() {{
      const box = document.getElementById('img-lightbox');
      if (box) box.style.display = 'none';
    }}
    document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeLightbox(); }});

    // On load: initialise replace/keep visibility for rows that start checked.
    document.querySelectorAll('.img-row .img-sel').forEach(cb => imgCheckChanged(cb));

    // Mark textarea as edited when user changes it.
    document.addEventListener('input', e => {{
      if (!e.target.classList.contains('img-prompt-edit')) return;
      const ta = e.target;
      const badge = ta.closest('details').querySelector('.img-edited-badge');
      if (badge) badge.style.display = ta.value !== ta.dataset.original ? 'inline' : 'none';
    }});

    async function generateImages(runId) {{
      const btn = document.getElementById('gen-btn');
      const status = document.getElementById('gen-status');

      // Collect ticked prompts. For existing images the action radio decides:
      // replace → overwrite image_N.png; new_version → save alongside as image_N_vK.png; skip → skip.
      const prompts = [];
      const alongside = [];
      const speeds = {{}};
      const ratios = {{}};
      const custom_prompts = {{}};
      document.querySelectorAll('.img-row').forEach(row => {{
        const n = parseInt(row.dataset.prompt, 10);
        if (!row.querySelector('.img-sel').checked) return;
        const actionRadio = row.querySelector('.img-action:checked');
        const action = actionRadio ? actionRadio.value : 'replace';
        if (action === 'new_version') {{
          alongside.push(n);
        }} else {{
          prompts.push(n);
        }}
        speeds[n] = row.querySelector('.img-speed').value;
        ratios[n] = row.querySelector('.img-ratio').value;
        const ta = row.querySelector('.img-prompt-edit');
        if (ta && ta.value !== ta.dataset.original) {{
          custom_prompts[n] = ta.value;
        }}
      }});
      if (!prompts.length && !alongside.length) {{
        status.textContent = 'Tick at least one image to create, or switch a "Skip" to Replace or New version.';
        return;
      }}

      btn.disabled = true;
      btn.textContent = 'Generating… (this may take 1–2 minutes)';
      status.textContent = '';
      try {{
        const res = await fetch('/generate-images/' + runId, {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{prompts, alongside, speeds, ratios, custom_prompts}})
        }});
        const data = await res.json();
        if (res.ok) {{
          const ok = data.generated.filter(r => r.status === 'generated').length;
          const errCount = data.errors.length;
          if (ok > 0) {{
            const msg = ok + ' image(s) generated' + (errCount ? ', ' + errCount + ' error(s)' : '') + ' — reloading…';
            status.textContent = msg;
            sessionStorage.setItem('activeTab', 'visuals');
            setTimeout(() => window.location.reload(), 1200);
          }} else {{
            btn.disabled = false;
            btn.textContent = 'Create selected';
            const errDetails = data.errors.map(e => 'Prompt ' + e.prompt + ': ' + e.error).join(' | ');
            status.textContent = 'No images generated.' + (errDetails ? ' ' + errDetails : '');
          }}
        }} else {{
          btn.disabled = false;
          btn.textContent = 'Create selected';
          status.textContent = 'Error: ' + (data.detail || 'Unknown error');
        }}
      }} catch(e) {{
        btn.disabled = false;
        btn.textContent = 'Create selected';
        status.textContent = 'Request failed: ' + e.message;
      }}
    }}
  </script>
</body></html>"""
    return HTMLResponse(content=html)


def _esc(s: str) -> str:
    """HTML-escape a string for safe inline rendering."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def _clean_copy(text: str) -> str:
    """Strip LLM markdown artefacts from copy before display.
    Removes bold/italic markers and inline character/word count annotations.
    """
    import re
    # Remove bold (**text** or __text__) and italic (*text* or _text_) markers
    text = re.sub(r'\*{1,3}|_{1,3}', '', text)
    # Remove char/word count annotations: [29 chars], [X chars], [50 characters], [120 words]
    text = re.sub(r'\[\s*\d+\s*(?:chars?|characters?|words?)\s*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\s*X\s*(?:chars?|characters?|words?)\s*\]', '', text, flags=re.IGNORECASE)
    # Collapse any double spaces left behind
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def _parse_ad_copy(text: str) -> dict:
    """
    Parse ad_copy.md into sections keyed by channel.
    Returns dict with keys: meta (dict of variant_num -> {headline, body}),
    google, whatsapp, email (strings).

    The crew emits one '## <Task Name>' header per channel (## Write Meta Ads,
    ## Write Google Ads, ...), but each channel's body ALSO contains its own
    '## ' sub-headers (## STEP 1, ## FINAL OUTPUT, ## COVERAGE CHECK). Splitting
    on every '## ' fragments a channel and the real content lands under a
    sub-header that doesn't match the channel keyword. So we split ONLY on the
    task-name boundary headers and keep each channel's body whole.
    """
    import re
    result = {"meta": {}, "google": "", "whatsapp": "", "email": ""}
    if not text:
        return result

    text2 = "\n" + text

    # Channel boundary headers (task names the crew writes). Anything else that
    # starts with '## ' is a sub-header inside a channel and is NOT a boundary.
    channel_patterns = [
        ("meta",     re.compile(r'write\s+meta', re.I)),
        ("google",   re.compile(r'write\s+google', re.I)),
        ("whatsapp", re.compile(r'write\s+whats?app', re.I)),
        ("email",    re.compile(r'write\s+e-?mail', re.I)),
        ("format",   re.compile(r'format\s+for\s+api', re.I)),
    ]
    boundaries = []  # (header_start, body_start, channel_label)
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

    # META: prefer the prose variants (what the scorecard scored and the user
    # reviewed); fall back to the structured Format-For-API JSON if prose yields
    # nothing (keeps deploy working even if the variant markup drifts).
    result["meta"] = _meta_from_prose(chunks.get("meta", ""))
    if not result["meta"]:
        result["meta"] = _meta_from_format_json(chunks.get("format", ""))

    result["google"] = _clean_google_copy(chunks.get("google", ""))
    result["whatsapp"] = chunks.get("whatsapp", "")
    result["email"] = chunks.get("email", "")
    return result


def _clean_google_copy(text: str) -> str:
    """The Google-ads agent sometimes 'shows its work' — selection notes and the
    iterative character-count trimming passes — instead of just the final answer.
    Extract only the final 3 headlines + 2 descriptions.

    The final answer writes each line as 'Headline 1: ...' (colon immediately after
    the number); the working notes use 'Headline 1 — concrete detail: ...' (a dash
    first), so a colon-immediate match skips the chatter. We take the LAST match per
    index in case the model restates them. If nothing matches, return the raw text
    so a differently-formatted response is never silently blanked."""
    if not text:
        return text
    import re

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


def _meta_from_prose(meta_body: str) -> dict:
    """Parse Meta variant blocks (### Variant N / N.) into {num: {headline, body}}."""
    import re
    out: dict[int, dict] = {}
    if not meta_body:
        return out
    # Variant markers seen from the LLM: "1. **Angle**", "**1. Angle**",
    # "**Variant 1 — Angle**", "### Variant 1 — Angle".
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
        # Only record real variant blocks — the STEP 1 hook list also starts with
        # "N." but has no Headline:/Body:, so skip anything without either.
        if hm or bm:
            out[num] = {
                "headline": hm.group(1).strip() if hm else "",
                "body": bm.group(1).strip() if bm else "",
            }
    return out


def _meta_from_format_json(format_body: str) -> dict:
    """Fallback: pull headline/body from the Format-For-API JSON `ads` array."""
    import re, json as _json
    if not format_body:
        return {}
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', format_body, re.DOTALL)
    raw = m.group(1) if m else format_body
    raw = re.sub(r'//[^\n]*', '', raw)  # strip // comments the LLM adds
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


# ── Content editing overlay ────────────────────────────────────────────────
# User edits live in edits.json beside the AI output and never overwrite it, so
# every change is fully revertible. The overlay holds per-channel text overrides,
# user-added Meta versions, and soft-deleted version numbers. Every read path
# (results page, deploy preview, deploy) goes through the _effective_* helpers,
# so an edit made here automatically flows into what gets published.

def _require_complete(run_id: str) -> dict:
    """Shared guard: run must exist, be complete, and have a review folder."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = _runs[run_id]
    if run.get("status") != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or no review folder.")
    return run


def _edits_path(review_folder) -> Path:
    return Path(review_folder) / "edits.json"


def _load_edits(review_folder) -> dict:
    p = _edits_path(review_folder)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_edits(review_folder, edits: dict) -> None:
    _edits_path(review_folder).write_text(
        json.dumps(edits, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── Audience overlay ─────────────────────────────────────────────────────────
# The ad-set targeting (city geo + age + interests/behaviours) is stored per run
# in audience.json, mirroring the edits.json overlay pattern. It is auto-seeded
# from a curated luxury-RE set the first time the Deploy tab is opened, then the
# user can tweak it before publishing. Resolving names to Meta IDs happens once
# (at seed time) so opening the tab repeatedly doesn't re-hit Meta.
def _audience_path(review_folder) -> Path:
    return Path(review_folder) / "audience.json"


def _load_audience(review_folder) -> dict | None:
    p = _audience_path(review_folder)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_audience(review_folder, audience: dict) -> None:
    _audience_path(review_folder).write_text(
        json.dumps(audience, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _effective_audience(review_folder, brief: dict) -> dict:
    """Saved audience if present, else resolve+seed the curated default once."""
    saved = _load_audience(review_folder)
    if saved is not None:
        return saved
    from pikorua_adflow.tools import meta_targeting as _mt
    token = os.getenv("META_ACCESS_TOKEN", "")
    city = brief.get("city", "") or ""
    try:
        audience = _mt.build_default_audience(
            city, token,
            locality=brief.get("locality", ""),
            nri_geographies=brief.get("nri_geographies", ""),
        )
    except Exception as exc:
        # Resolver unavailable (no token / network) — seed an un-resolved shell so
        # the panel still renders; deploy falls back to country-level India.
        audience = {
            "country": "IN", "city": "", "city_key": None, "region": "",
            "radius_km": _mt.DEFAULT_RADIUS_KM,
            "age_min": _mt.DEFAULT_AGE_MIN, "age_max": _mt.DEFAULT_AGE_MAX,
            "interests": [], "behaviours": [], "resolve_error": str(exc),
        }
    _save_audience(review_folder, audience)
    return audience


def _base_meta(review_folder) -> dict[int, dict]:
    """The AI baseline Meta copy (ad_copy.md with rewrites merged), before user edits."""
    rf = Path(review_folder)
    ac = rf / "ad_copy.md"
    meta = _parse_ad_copy(ac.read_text(encoding="utf-8")).get("meta", {}) if ac.exists() else {}
    base: dict[int, dict] = {
        num: {"headline": c.get("headline", ""), "body": c.get("body", "")}
        for num, c in meta.items()
    }
    sc = rf / "copy_scorecard.md"
    rw = rf / "copy_rewrites.md"
    vlist = _parse_scorecard(sc.read_text(encoding="utf-8") if sc.exists() else "")
    _merge_rewrites(vlist, rw.read_text(encoding="utf-8") if rw.exists() else "")
    for v in vlist:
        n = v.get("variant")
        rwc = v.get("rewrite") or {}
        if n in base and rwc:
            if rwc.get("headline"):
                base[n]["headline"] = rwc["headline"]
            if rwc.get("body"):
                base[n]["body"] = rwc["body"]
    return base


def _effective_meta(review_folder) -> dict[int, dict]:
    """AI baseline with the user overlay applied. Each entry:
    {headline, body, edited: bool, added: bool}. Deleted versions are removed."""
    base = _base_meta(review_folder)
    edits = _load_edits(review_folder)
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
    # Strip LLM artefacts ([24 chars], **bold**, etc.) from AI-generated values only.
    # User edits in the overlay are stored already-clean so this is safe to apply globally.
    for entry in out.values():
        entry["headline"] = _clean_copy(entry.get("headline", ""))
        entry["body"] = _clean_copy(entry.get("body", ""))
    return dict(sorted(out.items()))


def _effective_channel(review_folder, channel: str) -> tuple[str, bool]:
    """(text, edited?) for google/whatsapp/email with overlay applied."""
    rf = Path(review_folder)
    ac = rf / "ad_copy.md"
    base = _parse_ad_copy(ac.read_text(encoding="utf-8")).get(channel, "") if ac.exists() else ""
    base = _clean_copy(base)
    ov = _load_edits(review_folder).get(channel)
    return (ov, True) if ov is not None else (base, False)


class ContentEdit(BaseModel):
    channel: str = Field(..., description="meta | google | whatsapp | email")
    variant: int | None = Field(None, description="Meta version number (required when channel=meta)")
    headline: str | None = None
    body: str | None = None
    text: str | None = Field(None, description="Full text for google/whatsapp/email")


@app.post("/edit-content/{run_id}")
def edit_content(run_id: str, payload: ContentEdit):
    """Save a user edit into the overlay. Non-destructive — AI output is untouched."""
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = _load_edits(rf)
    ch = payload.channel
    if ch == "meta":
        if payload.variant is None:
            raise HTTPException(status_code=400, detail="variant is required for channel=meta")
        m = edits.setdefault("meta", {})
        cur = m.get(str(payload.variant), {})
        if payload.headline is not None:
            cur["headline"] = payload.headline
        if payload.body is not None:
            cur["body"] = payload.body
        cur.setdefault("added", cur.get("added", False))
        m[str(payload.variant)] = cur
    elif ch in ("google", "whatsapp", "email"):
        edits[ch] = payload.text or ""
    else:
        raise HTTPException(status_code=400, detail=f"Unknown channel '{ch}'")
    _save_edits(rf, edits)
    return {"ok": True}


@app.post("/revert-content/{run_id}")
def revert_content(run_id: str, payload: ContentEdit):
    """Drop a user edit and restore the AI original. Returns the restored values."""
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = _load_edits(rf)
    ch = payload.channel
    if ch == "meta":
        m = edits.get("meta", {})
        key = str(payload.variant)
        was_added = bool(m.get(key, {}).get("added"))
        m.pop(key, None)
        edits["meta"] = m
        _save_edits(rf, edits)
        if was_added:
            return {"ok": True, "removed": True}
        base = _base_meta(rf).get(payload.variant, {})
        return {"ok": True, "removed": False,
                "headline": base.get("headline", ""), "body": base.get("body", "")}
    if ch in ("google", "whatsapp", "email"):
        edits.pop(ch, None)
        _save_edits(rf, edits)
        text, _ = _effective_channel(rf, ch)
        return {"ok": True, "text": text}
    raise HTTPException(status_code=400, detail=f"Unknown channel '{ch}'")


@app.post("/add-variant/{run_id}")
def add_variant(run_id: str):
    """Add a new, blank Meta version the user can fill in."""
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = _load_edits(rf)
    nums = set(_base_meta(rf).keys()) | {int(k) for k in edits.get("meta", {})}
    new_num = (max(nums) + 1) if nums else 1
    m = edits.setdefault("meta", {})
    m[str(new_num)] = {"headline": "", "body": "", "added": True}
    edits["deleted_variants"] = [d for d in edits.get("deleted_variants", []) if d != new_num]
    _save_edits(rf, edits)
    return {"ok": True, "variant": new_num}


@app.post("/duplicate-variant/{run_id}")
def duplicate_variant(run_id: str, payload: ContentEdit):
    """Clone an existing version into a new editable one."""
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    src = _effective_meta(rf).get(payload.variant, {})
    edits = _load_edits(rf)
    nums = set(_base_meta(rf).keys()) | {int(k) for k in edits.get("meta", {})}
    new_num = (max(nums) + 1) if nums else 1
    m = edits.setdefault("meta", {})
    m[str(new_num)] = {"headline": src.get("headline", ""), "body": src.get("body", ""), "added": True}
    _save_edits(rf, edits)
    return {"ok": True, "variant": new_num}


@app.post("/delete-variant/{run_id}")
def delete_variant(run_id: str, payload: ContentEdit):
    """Remove a version. User-added ones are dropped; AI ones are soft-deleted
    (kept in deleted_variants so they can be restored)."""
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    v = payload.variant
    edits = _load_edits(rf)
    m = edits.get("meta", {})
    if m.get(str(v), {}).get("added"):
        m.pop(str(v), None)
        edits["meta"] = m
    else:
        d = set(edits.get("deleted_variants", []))
        d.add(v)
        edits["deleted_variants"] = sorted(d)
    # if this version was selected for launch, unselect it
    if "selected_variants" in run:
        run["selected_variants"] = [s for s in run["selected_variants"] if s != v]
    _save_edits(rf, edits)
    _save_runs()
    return {"ok": True}


@app.post("/restore-variant/{run_id}")
def restore_variant(run_id: str, payload: ContentEdit):
    """Undo a soft-delete of an AI version."""
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = _load_edits(rf)
    edits["deleted_variants"] = [d for d in edits.get("deleted_variants", []) if d != payload.variant]
    _save_edits(rf, edits)
    return {"ok": True}


@app.post("/upload-image/{run_id}/{variant}")
async def upload_image(run_id: str, variant: int, request: Request):
    """Replace a version's image with a user upload. The raw image bytes are the
    request body (no multipart dependency). The AI image is backed up once so it
    can be restored."""
    import shutil
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="No image data received.")
    # Validate it's actually an image (JPEG/PNG/WebP/GIF magic bytes)
    if not (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"
            or data[:4] == b"RIFF" or data[:3] == b"GIF"):
        raise HTTPException(status_code=400, detail="File doesn't look like a PNG/JPG/WebP/GIF image.")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 12 MB).")
    images = rf / "images"
    images.mkdir(exist_ok=True)
    target = images / f"image_{variant}.png"
    if target.exists():
        backup_dir = images / ".ai_backup"
        backup_dir.mkdir(exist_ok=True)
        b = backup_dir / f"image_{variant}.png"
        if not b.exists():
            shutil.copy2(target, b)
    target.write_bytes(data)
    return {"ok": True, "variant": variant}


@app.post("/revert-image/{run_id}/{variant}")
def revert_image(run_id: str, variant: int):
    """Restore the AI image if one was backed up; otherwise remove the upload."""
    import shutil
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    images = rf / "images"
    target = images / f"image_{variant}.png"
    backup = images / ".ai_backup" / f"image_{variant}.png"
    if backup.exists():
        shutil.copy2(backup, target)
        return {"ok": True, "restored": True}
    if target.exists():
        target.unlink()
    return {"ok": True, "restored": False}


@app.post("/revert-logo/{run_id}/{prompt_num}")
def revert_logo(run_id: str, prompt_num: int):
    """Restore the pre-logo original for a specific generated image."""
    import shutil
    run = _require_complete(run_id)
    images = Path(run["review_folder"]) / "images"
    target = images / f"image_{prompt_num}.png"
    backup = images / ".logo_backup" / f"image_{prompt_num}.png"
    if not backup.exists():
        raise HTTPException(status_code=404, detail="No logo backup found for this image.")
    shutil.copy2(backup, target)
    backup.unlink()  # remove backup so the button disappears after revert
    return {"ok": True}


class _AssignImagePayload(BaseModel):
    image_num: int | None = None


@app.post("/assign-image/{run_id}/{variant_num}")
def assign_image(run_id: str, variant_num: int, payload: _AssignImagePayload):
    """Store which generated image to use for a given ad variant."""
    run = _require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = _load_edits(rf)
    m = edits.setdefault("meta", {})
    cur = m.get(str(variant_num), {})
    if payload.image_num is not None:
        cur["image_num"] = payload.image_num
    else:
        cur.pop("image_num", None)
    m[str(variant_num)] = cur
    _save_edits(rf, edits)
    return {"ok": True}


def _parse_image_prompts(text: str) -> list:
    """Parse visual_brief.md into list of (title, prompt_text) tuples."""
    import re
    if not text:
        return []
    prompts = []
    # Split on any of: "Prompt N", "1. Prompt N", "**Prompt N" — LLM varies the format
    blocks = re.split(r'\n(?=(?:\d+\.\s+)?(?:\*{0,2})Prompt\s+\d+)', text.strip())
    for block in blocks:
        if not block.strip():
            continue
        # Extract title
        tm = re.match(r'(?:\d+\.\s+)?(?:\*\*)?Prompt\s+\d+\s*[—-]\s*([^:\*\n]+)', block)
        title = tm.group(1).strip().rstrip("*:") if tm else f"Prompt {len(prompts)+1}"
        # Extract headline overlay if the brief specifies one (e.g. Headline overlay: "text")
        hm = re.search(r'Headline\s+overlay\s*:\s*"([^"]+)"', block, re.I)
        headline_overlay = hm.group(1).strip() if hm else None
        # Extract the quoted prompt text
        qm = re.search(r'"([^"]{40,})"', block, re.DOTALL)
        if qm:
            base = qm.group(1)
            # Prepend headline as a structured tag so _sanitize_image_prompt can use it
            text = f'[HEADLINE:"{headline_overlay}"] {base}' if headline_overlay else base
            prompts.append((title, text))
        else:
            # Fallback: everything after the title line
            rest = re.sub(r'^[^\n]+\n', '', block, count=1).strip()
            rest = rest.strip('"*').strip()
            if len(rest) > 40:
                prompts.append((title, rest))
    return prompts


def _parse_scorecard(text: str) -> list:
    """Parse copy_scorecard.md into a list of variant dicts."""
    import re
    variants = []
    # Split on variant blocks — LLM uses ###, **, or bare "Variant N" interchangeably
    blocks = re.split(r'\n(?=(?:#{0,4}|\*{0,4})\s*Variant \d)', text.strip())
    for block in blocks:
        if not block.strip():
            continue
        v = {"variant": None, "angle": "", "headline": "", "body": "",
             "scores": {}, "status": "PASS", "flag_reason": "", "rewrite": None}

        # Variant number and angle — strip leading #/*, trailing *
        m = re.match(r'(?:#{0,4}|\*{0,4})\s*Variant (\d+)\s*[—-]\s*(.+)', block)
        if m:
            v["variant"] = int(m.group(1))
            v["angle"] = m.group(2).strip().rstrip('*').strip()

        # Scores — format-agnostic. Matches the dimension name then the FIRST "X/10"
        # on the SAME line, so it handles every layout the evaluator uses:
        #   "Brand Voice: 9/10"  |  "Brand Voice Compliance:** 9.5/10"  |  "| Brand Voice | 7/10 |"
        # Staying on one line avoids bleeding into the flag-reason line (e.g.
        # "Status: FLAG — Specificity 6/10: ...") which appears later in the block.
        for dim, key in [
            ("Brand Voice", "brand_voice"), ("Platform Fit", "platform_fit"),
            ("Specificity", "specificity"), ("Luxury Signal", "luxury_signal")
        ]:
            sm = re.search(rf'{re.escape(dim)}\b[^\n]*?(\d+(?:\.\d+)?)\s*/\s*10', block, re.IGNORECASE)
            if sm:
                v["scores"][key] = round(float(sm.group(1)))

        # Status
        if re.search(r'\bFLAG\b', block, re.IGNORECASE):
            v["status"] = "FLAG"
            fr = re.search(r'FLAG\s*[—-]\s*(.+)', block)
            if fr:
                v["flag_reason"] = fr.group(1).strip()

        # Headline / Body from scorecard (some scorecards include them)
        hm = re.search(r'Headline:\s*(.+)', block)
        bm = re.search(r'Body:\s*(.+)', block)
        if hm:
            v["headline"] = hm.group(1).strip()
        if bm:
            v["body"] = bm.group(1).strip()

        if v["variant"] is not None:
            variants.append(v)

    return variants


def _merge_rewrites(variants: list, rewrites_text: str) -> None:
    """Merge rewritten copy into variant dicts where rewrites exist."""
    import re
    if not rewrites_text or "No rewrites needed" in rewrites_text:
        return
    # LLM may use ###, **, or bare "Variant N"
    blocks = re.split(r'\n(?=(?:#{0,4}|\*{0,4})\s*Variant \d)', rewrites_text)
    for block in blocks:
        m = re.match(r'(?:#{0,4}|\*{0,4})\s*Variant (\d+)', block)
        if not m:
            continue
        num = int(m.group(1))
        hm = re.search(r'Headline:\s*(.+?)(?:\s*\[[\*\d\s]+chars[\*\s]*\])?\s*$', block, re.MULTILINE)
        bm = re.search(r'Body:\s*(.+?)(?:\s*\[[\*\d\s]+chars[\*\s]*\])?\s*$', block, re.MULTILINE)
        for v in variants:
            if v["variant"] == num:
                if hm or bm:
                    v["rewrite"] = {
                        "headline": hm.group(1).strip() if hm else "",
                        "body": bm.group(1).strip() if bm else "",
                    }


def _build_logo_section_html(run_id: str = "") -> str:
    """Persistent brand-logo bar rendered above the tab bar — always visible."""
    has_logo = _BRAND_LOGO_PATH.exists()
    logo_preview = (
        '<img src="/brand-logo" style="max-height:42px;max-width:140px;'
        'object-fit:contain;border-radius:4px;" alt="Brand logo">'
        if has_logo else ""
    )
    logo_action = (
        '<button class="btn btn-ghost btn-sm" style="color:var(--danger);" '
        'onclick="removeBrandLogo()">Remove</button>'
        if has_logo else ""
    )
    apply_btn = (
        f'<button class="btn btn-ghost btn-sm" onclick="applyLogoToImages(\'{run_id}\')" '
        f'title="Stamp logo onto images already in this run">Apply to existing images</button>'
        if has_logo and run_id else ""
    )
    logo_hint = ("Logo active — placed bottom-right on every generated image."
                 if has_logo else "No logo uploaded yet.")
    return f"""
    <div style="background:var(--paper);border:1px solid var(--line);border-radius:10px;
                padding:12px 18px;margin-bottom:16px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
      <div style="font-size:0.82rem;font-weight:600;color:var(--ink);white-space:nowrap;">Brand logo</div>
      <div style="display:flex;align-items:center;gap:10px;flex:1;">
        {logo_preview}
        <span style="font-size:0.8rem;color:var(--ink-soft);">{logo_hint}</span>
        <span id="logo-status" style="font-size:0.78rem;color:var(--ink-soft);"></span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
        <label class="btn btn-ghost btn-sm" style="cursor:pointer;display:inline-flex;align-items:center;">
          {'Replace logo' if has_logo else 'Upload logo'}
          <input type="file" accept="image/png,image/jpeg,image/webp" style="display:none;"
                 onchange="uploadBrandLogo(this)">
        </label>
        {apply_btn}
        {logo_action}
      </div>
    </div>"""


def _build_visuals_html(run_id: str, image_prompts: list, existing_images: list,
                        ideogram_key: str, replicate_token: str, together_key: str = "",
                        images_dir: Path | None = None) -> str:
    """Build the full HTML for the visuals tab — images, generate button, prompts."""

    def _type_label(i):
        return "Social banner" if i <= 3 else "Lifestyle photo"

    html = ""

    # Show already-generated images
    logo_backup_dir = (images_dir / ".logo_backup") if images_dir else None
    if existing_images:
        import re as _img_re
        html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-bottom:22px;">'
        for fname in existing_images:
            _m = _img_re.match(r'image_(\d+)(?:_v(\d+))?\.png$', fname)
            prompt_num = int(_m.group(1)) if _m else 0
            version_num = int(_m.group(2)) if (_m and _m.group(2)) else None
            title = image_prompts[prompt_num - 1][0] if 0 < prompt_num <= len(image_prompts) else fname
            version_label = f" · v{version_num}" if version_num else ""
            has_logo_backup = bool(
                logo_backup_dir and (logo_backup_dir / fname).exists()
            )
            revert_btn = (
                f'<button class="btn btn-ghost btn-sm" style="color:var(--ink-soft);" '
                f'onclick="revertLogo(\'{run_id}\', {prompt_num})">Remove logo</button>'
                if has_logo_backup else ""
            )
            html += f"""
            <div style="background:var(--paper);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:var(--shadow);">
              <img src="/image/{run_id}/{fname}" alt="{_esc(title)}" title="Click to view full size"
                   onclick="openLightbox('/image/{run_id}/{fname}', this.alt)"
                   style="width:100%;display:block;border-bottom:1px solid var(--line);cursor:zoom-in;">
              <div style="padding:10px 12px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;">
                <span style="font-size:0.8rem;color:var(--ink-soft);">{_esc(title)}{_esc(version_label)}</span>
                <div style="display:flex;align-items:center;gap:6px;">
                  {revert_btn}
                  <span class="badge badge-gold">{_type_label(prompt_num)}</span>
                </div>
              </div>
            </div>"""
        html += "</div>"

    # Is an image service connected? (kept non-technical for the operator)
    backend_ready = bool(ideogram_key or replicate_token or together_key)
    if backend_ready:
        backend_note = ("Tick the images you want to create, pick a quality for each, then click "
                        "Create selected. Higher quality takes a little longer.")
    else:
        backend_note = "Image creation isn't connected yet. Ask your developer to set up an image service."

    # Which prompt numbers already have a BASE image on disk (image_N.png only, not versioned).
    import re as _pnum_re
    existing_nums = set()
    for fname in existing_images:
        _bm = _pnum_re.fullmatch(r'image_(\d+)\.png', fname)
        if _bm:
            existing_nums.add(int(_bm.group(1)))

    btn_disabled = "" if backend_ready else "disabled"

    if not image_prompts:
        html += '<p style="color:var(--muted);font-size:0.9rem;">No image descriptions found for this campaign.</p>'
        return html

    # Per-image picker: checkbox (pre-ticked only for images not yet created) + a
    # quality selector, with the full description tucked into a collapsible.
    html += f"""
    <div style="margin-bottom:14px;">
      <button id="gen-btn" class="btn" {btn_disabled} onclick="generateImages('{run_id}')">Create selected</button>
      <button class="btn btn-ghost btn-sm" style="margin-left:8px;" onclick="imgSelectAll(true)">Select all</button>
      <button class="btn btn-ghost btn-sm" onclick="imgSelectAll(false)">Clear</button>
      <span id="gen-status" style="margin-left:12px;font-size:0.85rem;color:var(--ink-soft);"></span>
      <div style="margin-top:8px;font-size:0.82rem;color:var(--muted);">{backend_note}</div>
    </div>
    <div style="margin-bottom:22px;">"""

    # Best-for-text QUALITY is the default; banners especially benefit.
    speed_opts = (
        '<option value="QUALITY" selected>Best quality (text-safe)</option>'
        '<option value="DEFAULT">Balanced</option>'
        '<option value="TURBO">Fastest</option>'
    )
    # 4:5 is Meta's recommended feed ratio; 1:1 is the versatile multi-placement square.
    ratio_opts = (
        '<option value="4x5" selected>4:5 — Feed (recommended)</option>'
        '<option value="1x1">1:1 — Square</option>'
        '<option value="16x9">16:9 — Wide</option>'
        '<option value="9x16">9:16 — Story/Reel</option>'
    )

    for i, (ptitle, prompt_text) in enumerate(image_prompts, 1):
        has_img = i in existing_nums
        checked = "" if has_img else "checked"
        esc_prompt = _esc(prompt_text.strip())
        # Action picker: shown when an existing image is checked. Replace overwrites; New version
        # saves alongside (image_N_v2.png etc); Skip does nothing for this prompt.
        replace_confirm = ""
        if has_img:
            replace_confirm = f"""
            <div class="img-replace-row" style="display:none;align-items:center;gap:8px;
                 margin-top:8px;padding:8px 10px;background:var(--paper-warm);
                 border:1px solid var(--line);border-radius:8px;font-size:0.82rem;color:var(--ink);">
              <span>Already generated —</span>
              <label style="display:flex;align-items:center;gap:5px;cursor:pointer;">
                <input type="radio" class="img-action" name="img-action-{i}" value="replace" checked
                       style="cursor:pointer;"> Replace
              </label>
              <label style="display:flex;align-items:center;gap:5px;cursor:pointer;">
                <input type="radio" class="img-action" name="img-action-{i}" value="new_version"
                       style="cursor:pointer;"> New version
              </label>
            </div>"""
        html += f"""
        <div class="img-row" data-prompt="{i}" data-has-img="{'1' if has_img else '0'}"
             style="background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:10px;">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;flex:1 1 240px;">
              <input type="checkbox" class="img-sel" {checked}
                     onchange="imgCheckChanged(this)"
                     style="width:16px;height:16px;cursor:pointer;">
              <span style="font-size:0.88rem;color:var(--ink);">Image {i} — {_esc(ptitle)}</span>
            </label>
            <span class="badge badge-gold">{_type_label(i)}</span>
            {'<span class="badge badge-muted" style="margin-left:6px;">already created</span>' if has_img else ''}
            <label style="font-size:0.8rem;color:var(--ink-soft);display:flex;align-items:center;gap:6px;">
              Quality
              <select class="img-speed"
                style="padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);font-size:0.8rem;">
                {speed_opts}
              </select>
            </label>
            <label style="font-size:0.8rem;color:var(--ink-soft);display:flex;align-items:center;gap:6px;">
              Shape
              <select class="img-ratio"
                style="padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);font-size:0.8rem;">
                {ratio_opts}
              </select>
            </label>
          </div>
          {replace_confirm}
          <details class="adv" style="margin-top:8px;">
            <summary style="font-size:0.8rem;color:var(--ink-soft);cursor:pointer;">Edit image description</summary>
            <div style="margin-top:8px;">
              <textarea class="img-prompt-edit"
                style="width:100%;box-sizing:border-box;min-height:110px;padding:10px;
                       border:1px solid var(--line);border-radius:8px;
                       background:var(--paper-warm);color:var(--ink);
                       font-size:0.82rem;line-height:1.6;resize:vertical;
                       font-family:inherit;"
                data-original="{esc_prompt}">{esc_prompt}</textarea>
              <div style="display:flex;align-items:center;gap:8px;margin-top:6px;">
                <button class="btn btn-ghost btn-sm"
                  onclick="revertPrompt(this)">Revert to original</button>
                <button class="btn btn-ghost btn-sm"
                  onclick="copyFromData(this)" data-copy="{esc_prompt}">Copy</button>
                <span class="img-edited-badge" style="display:none;font-size:0.78rem;
                      color:var(--gold);font-style:italic;">edited</span>
              </div>
            </div>
          </details>
        </div>"""
    html += "</div>"

    return html


_AUDIENCE_PANEL_JS = r"""
<script>
(function(){
  const RUN_ID = "__RUN_ID__";
  let AUD = __AUDIENCE_JSON__;

  function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

  function summary(){
    let geo = AUD.city && AUD.city_key ? (AUD.city + " +" + (AUD.radius_km||25) + "km")
              : "India (whole country)";
    let bits = [geo, "Age " + (AUD.age_min||28) + "–" + (AUD.age_max||65)];
    if ((AUD.interests||[]).length) bits.push(AUD.interests.length + " interest" + (AUD.interests.length!=1?"s":""));
    if ((AUD.behaviours||[]).length) bits.push(AUD.behaviours.length + " behaviour" + (AUD.behaviours.length!=1?"s":""));
    return bits.join(" · ");
  }

  function chips(kind){
    const arr = AUD[kind] || [];
    if (!arr.length) return '<span style="font-size:0.8rem;color:var(--muted);">None yet — add some below.</span>';
    return arr.map((it,i) =>
      '<span style="display:inline-flex;align-items:center;gap:6px;background:var(--paper-warm);'
      +'border:1px solid var(--line);border-radius:999px;padding:4px 6px 4px 12px;margin:0 6px 6px 0;'
      +'font-size:0.8rem;color:var(--ink);">' + esc(it.name)
      +'<button onclick="audRemove(\''+kind+'\','+i+')" title="Remove" '
      +'style="border:none;background:var(--line);color:var(--ink-soft);border-radius:50%;width:18px;height:18px;'
      +'line-height:1;cursor:pointer;font-size:0.75rem;">×</button></span>'
    ).join("");
  }

  function nriChips(){
    const arr = AUD.nri_countries||[];
    let out = arr.map((c,i)=>
      '<span style="display:inline-flex;align-items:center;gap:5px;background:var(--paper-warm);'
      +'border:1px solid var(--line);border-radius:999px;padding:3px 6px 3px 10px;margin:0 5px 5px 0;'
      +'font-size:0.78rem;font-weight:600;color:var(--ink);letter-spacing:0.04em;">'+esc(c)
      +'<button onclick="audRemoveCountry('+i+')" title="Remove" '
      +'style="border:none;background:var(--line);color:var(--ink-soft);border-radius:50%;width:16px;height:16px;'
      +'line-height:1;cursor:pointer;font-size:0.72rem;">×</button></span>'
    ).join('');
    out += '<span style="display:inline-flex;align-items:center;gap:4px;margin-top:2px;">'
      +'<input id="aud-nri-input" maxlength="2" placeholder="+ ISO" '
      +'style="width:48px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;'
      +'background:var(--paper);color:var(--ink);font-size:0.78rem;text-transform:uppercase;" '
      +'onkeydown="if(event.key===\'Enter\')audAddCountry()">'
      +'<button onclick="audAddCountry()" style="border:1px solid var(--line);background:var(--paper-warm);'
      +'color:var(--ink);border-radius:6px;padding:3px 8px;font-size:0.75rem;cursor:pointer;">Add</button></span>';
    return out;
  }

  function geoRow(){
    if (AUD.city && AUD.city_key){
      return '<div style="font-size:0.88rem;color:var(--ink);margin-bottom:4px;"><strong>'+esc(AUD.city)+'</strong>'
        + (AUD.region? ', '+esc(AUD.region):'') + '</div>'
        + '<label style="font-size:0.8rem;color:var(--ink-soft);">Radius around city: '
        + '<input type="number" min="17" max="80" value="'+(AUD.radius_km||25)+'" '
        + 'onchange="audField(\'radius_km\', parseInt(this.value)||25)" '
        + 'style="width:64px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;'
        + 'background:var(--paper);color:var(--ink);"> km</label>';
    }
    return '<div style="font-size:0.85rem;color:var(--warn);"><strong>India — whole country</strong> '
      + '(no specific city resolved). Add a city below for tighter targeting.</div>';
  }

  window.audField = function(f, v){ AUD[f]=v; paint(true); };
  window.audRemove = function(kind, i){ AUD[kind].splice(i,1); paint(); };
  window.audRemoveCountry = function(i){ (AUD.nri_countries=AUD.nri_countries||[]).splice(i,1); paint(); };
  window.audAddCountry = function(){
    const el = document.getElementById('aud-nri-input');
    const raw = (el.value||'').trim().toUpperCase();
    if (raw.length===2){ AUD.nri_countries=AUD.nri_countries||[]; if(!AUD.nri_countries.includes(raw)) AUD.nri_countries.push(raw); el.value=''; paint(); }
  };

  window.audSearch = function(kind){
    const q = document.getElementById('aud-q-'+kind).value.trim();
    const box = document.getElementById('aud-res-'+kind);
    if (q.length < 2){ box.innerHTML=''; return; }
    const type = kind==='interests' ? 'interest' : (kind==='behaviours'?'behaviour':'city');
    box.innerHTML = '<span style="font-size:0.78rem;color:var(--muted);">Searching…</span>';
    fetch('/audience-search?type='+type+'&q='+encodeURIComponent(q))
      .then(r=>r.json()).then(d=>{
        const res = d.results||[];
        if (!res.length){ box.innerHTML='<span style="font-size:0.78rem;color:var(--muted);">No matches.</span>'; return; }
        box.innerHTML = res.map(x=>{
          const id = kind==='cities'? x.key : x.id;
          const payload = encodeURIComponent(JSON.stringify(kind==='cities'
            ? {key:x.key, name:x.name, region:x.region||''}
            : {id:String(x.id), name:x.name}));
          return '<button onclick="audPick(\''+kind+'\',\''+payload+'\')" '
            +'style="display:block;width:100%;text-align:left;border:none;border-bottom:1px solid var(--line);'
            +'background:var(--paper);color:var(--ink);padding:7px 10px;cursor:pointer;font-size:0.82rem;font-family:inherit;">'
            + esc(x.name) + '</button>';
        }).join("");
      }).catch(()=>{ box.innerHTML='<span style="font-size:0.78rem;color:var(--danger);">Search failed.</span>'; });
  };

  window.audPick = function(kind, payload){
    const it = JSON.parse(decodeURIComponent(payload));
    if (kind==='cities'){ AUD.city=it.name; AUD.city_key=it.key; AUD.region=it.region||''; }
    else {
      AUD[kind] = AUD[kind]||[];
      if (!AUD[kind].some(x=>String(x.id)===String(it.id))) AUD[kind].push(it);
    }
    document.getElementById('aud-q-'+kind).value='';
    document.getElementById('aud-res-'+kind).innerHTML='';
    paint();
  };

  window.audSave = function(){
    const st = document.getElementById('aud-save-status');
    st.textContent = 'Saving…';
    AUD.age_min = parseInt(document.getElementById('aud-age-min').value)||28;
    AUD.age_max = parseInt(document.getElementById('aud-age-max').value)||65;
    const ed = document.getElementById('aud-end').value;
    AUD.end_time = ed ? (ed + 'T23:59:00+0530') : '';
    fetch('/audience/'+RUN_ID, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(AUD)})
      .then(r=>r.json()).then(d=>{ st.textContent='Saved ✓'; setTimeout(()=>st.textContent='',2000); })
      .catch(()=>{ st.textContent='Save failed'; });
  };

  function searchRow(kind, label, ph){
    return '<div style="margin-top:6px;"><div style="font-size:0.8rem;color:var(--ink-soft);margin-bottom:4px;">'+label+'</div>'
      + '<div style="margin-bottom:6px;">'+chips(kind)+'</div>'
      + '<div style="display:flex;gap:6px;">'
      + '<input id="aud-q-'+kind+'" placeholder="'+ph+'" oninput="audSearch(\''+kind+'\')" '
      + 'style="flex:1;padding:6px 9px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);font-size:0.82rem;"></div>'
      + '<div id="aud-res-'+kind+'" style="border:1px solid var(--line);border-top:none;border-radius:0 0 6px 6px;'
      + 'max-height:160px;overflow:auto;"></div></div>';
  }

  function paint(skipInputs){
    document.getElementById('aud-summary').textContent = summary();
    document.getElementById('aud-geo').innerHTML = geoRow();
    document.getElementById('aud-nri').innerHTML = nriChips();
    document.getElementById('aud-int').innerHTML = searchRow('interests','Interests','Type to search Meta interests…');
    document.getElementById('aud-beh').innerHTML = searchRow('behaviours','Behaviours','e.g. frequent travellers…');
    document.getElementById('aud-city-search').innerHTML =
      '<input id="aud-q-cities" placeholder="Change city…" oninput="audSearch(\'cities\')" '
      + 'style="width:200px;margin-top:6px;padding:6px 9px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);font-size:0.82rem;">'
      + '<div id="aud-res-cities" style="max-width:260px;border:1px solid var(--line);border-top:none;max-height:160px;overflow:auto;"></div>';
  }

  document.addEventListener('DOMContentLoaded', paint);
  if (document.readyState !== 'loading') paint();
})();
</script>
"""


def _audience_panel_html(run_id: str, audience: dict) -> str:
    """Editable Audience panel for the pre-deploy view (city geo, age, interests, behaviours)."""
    import json as _json
    age_lo = audience.get("age_min", 28)
    age_hi = audience.get("age_max", 65)
    end_val = (audience.get("end_time", "") or "")[:10]  # YYYY-MM-DD for the date input
    shell = (
        '<div style="background:var(--paper-warm);border:1px solid var(--line);border-radius:12px;'
        'padding:18px 20px;margin-bottom:20px;">'
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;">'
        '<div><div class="eyebrow" style="margin-bottom:4px;">Who this reaches</div>'
        '<div id="aud-summary" style="font-size:0.92rem;color:var(--ink);font-weight:600;"></div></div>'
        '<div style="display:flex;align-items:center;gap:10px;">'
        '<span id="aud-save-status" style="font-size:0.8rem;color:var(--green-mid);"></span>'
        '<button class="btn btn-ghost" onclick="audSave()" style="white-space:nowrap;">Save audience</button>'
        '</div></div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:14px;">'
        # left column: location + age
        '<div>'
        '<div class="eyebrow" style="margin-bottom:6px;">Location</div>'
        '<div id="aud-geo"></div>'
        '<div id="aud-city-search"></div>'
        '<div class="eyebrow" style="margin:14px 0 4px;">NRI / overseas countries</div>'
        '<div style="font-size:0.75rem;color:var(--muted);margin-bottom:5px;">ISO-2 codes (AE, US, GB, SG…) — reach diaspora buyers</div>'
        '<div id="aud-nri"></div>'
        '<div class="eyebrow" style="margin:14px 0 6px;">Age</div>'
        f'<input id="aud-age-min" type="number" min="18" max="65" value="{age_lo}" '
        'style="width:60px;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);"> '
        '<span style="color:var(--muted);">to</span> '
        f'<input id="aud-age-max" type="number" min="18" max="65" value="{age_hi}" '
        'style="width:60px;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);">'
        '<div class="eyebrow" style="margin:14px 0 6px;">End date <span style="text-transform:none;color:var(--muted);">(optional)</span></div>'
        f'<input id="aud-end" type="date" value="{end_val}" '
        'style="padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);">'
        '</div>'
        # right column: interests + behaviours
        '<div>'
        '<div id="aud-int"></div>'
        '<div id="aud-beh" style="margin-top:14px;"></div>'
        '</div>'
        '</div>'
        '<div style="margin-top:12px;font-size:0.78rem;color:var(--muted);">'
        'Edits are saved to this campaign. Ads also run on Instagram under your page handle.</div>'
        '</div>'
    )
    script = (_AUDIENCE_PANEL_JS
              .replace("__RUN_ID__", run_id)
              .replace("__AUDIENCE_JSON__", _json.dumps(audience)))
    return shell + script


def _build_deploy_html(run_id: str, run: dict, brief: dict) -> str:  # noqa: C901
    """Build the Deploy tab — Facebook-style ad mock-up per variant, pre and post-deploy."""
    import os
    from pathlib import Path as _Path

    meta_ads   = run.get("meta_ads", [])
    dep_errors = run.get("meta_deploy_errors", [])
    dry_run    = os.getenv("DRY_RUN", "true").lower() == "true"

    rf_str = run.get("review_folder")
    rf     = _Path(rf_str) if rf_str else None

    # Load copy once (used for both preview and post-deploy cards).
    # Effective copy = user edits > AI rewrites > AI original, with added/deleted applied.
    meta_copy = _effective_meta(rf) if rf else {}

    _CTA = {"GET_QUOTE": "Get Quote", "CONTACT_US": "Contact Us",
            "LEARN_MORE": "Learn More", "SIGN_UP": "Sign Up"}
    lp     = brief.get("landing_page_url", "https://pikorua.in/")
    budget = int(brief.get("daily_budget_inr", 1000))
    cta    = brief.get("cta", "GET_QUOTE")
    cta_lbl = _CTA.get(cta, cta.replace("_", " ").title())
    prop   = brief.get("property_name", "Pikorua Campaign")
    page_name = brief.get("company_name", "").strip() or "Pikorua Realty"
    age_lo = 28
    age_hi = 65

    def _copy(v):
        c = meta_copy.get(v, {})
        return c.get("headline", ""), c.get("body", "")

    def _effective_img_num(v):
        """Return the image number to use for variant v (assigned > same-num > None)."""
        if not rf:
            return None
        edits = _load_edits(rf)
        assigned = edits.get("meta", {}).get(str(v), {}).get("image_num")
        if assigned and (rf / "images" / f"image_{assigned}.png").exists():
            return assigned
        if (rf / "images" / f"image_{v}.png").exists():
            return v
        return None

    def _has_img(v):
        return _effective_img_num(v) is not None

    # Initials for page avatar (up to 2 words)
    _page_initials = "".join(w[0] for w in page_name.split()[:2]).upper() or "PR"

    def _ad_card(v, headline, body_text, badge_html, struct_html):
        """Render one Facebook-style ad mock-up card."""
        img_num = _effective_img_num(v)
        if img_num is not None:
            img = (f'<img src="/image/{run_id}/image_{img_num}.png" '
                   f'alt="Ad image variant {v}" '
                   f'style="width:100%;display:block;">')
        else:
            img = ('<div style="width:100%;padding-top:52%;position:relative;background:var(--cream);">'
                   '<div style="position:absolute;inset:0;display:flex;align-items:center;'
                   'justify-content:center;font-size:0.75rem;color:var(--muted);">'
                   'No image &mdash; generate in Image Prompts tab first</div></div>')

        hl = (f'<p style="margin:0 0 5px;font-size:0.95rem;font-weight:700;color:#1c1e21;line-height:1.3;">'
              f'{_esc(headline)}</p>'
              if headline else
              '<p style="margin:0 0 5px;font-size:0.85rem;color:var(--muted);">(no headline in ad copy)</p>')

        bd = (f'<p style="margin:0 0 10px;font-size:0.85rem;color:#606770;line-height:1.55;">'
              f'{_esc(body_text)}</p>'
              if body_text else '')

        return (
            f'<div style="margin-bottom:32px;">'
            # variant header row
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'margin-bottom:10px;">'
            f'<span style="font-size:0.7rem;letter-spacing:0.12em;text-transform:uppercase;'
            f'color:var(--muted);">Version {v}</span>'
            f'{badge_html}</div>'
            # facebook card
            f'<div style="background:#fff;border:1px solid #dddfe2;border-radius:8px;'
            f'overflow:hidden;max-width:480px;box-shadow:0 1px 4px rgba(0,0,0,0.08);">'
            # page header
            f'<div style="padding:10px 14px;display:flex;align-items:center;gap:10px;">'
            f'<div style="width:40px;height:40px;border-radius:50%;background:#1a3050;'
            f'display:flex;align-items:center;justify-content:center;font-size:0.68rem;'
            f'color:#f7f5f0;font-weight:bold;letter-spacing:0.05em;flex-shrink:0;">{_page_initials}</div>'
            f'<div><div style="font-size:0.88rem;font-weight:600;color:#1c1e21;">'
            f'{_esc(page_name)}</div>'
            f'<div style="font-size:0.72rem;color:#65676b;">Sponsored &middot; &#127760;</div>'
            f'</div></div>'
            # image
            + img +
            # copy + cta
            f'<div style="padding:12px 14px;">'
            f'<div style="font-size:0.7rem;color:#8a8d91;text-transform:uppercase;'
            f'letter-spacing:0.05em;margin-bottom:4px;">'
            f'{_esc(lp.replace("https://","").replace("http://","").rstrip("/"))}</div>'
            + hl + bd +
            f'<div style="border-top:1px solid #e4e6eb;padding-top:10px;'
            f'display:flex;justify-content:flex-end;">'
            f'<button style="background:#1877f2;color:#fff;border:none;padding:7px 18px;'
            f'border-radius:6px;font-size:0.85rem;font-weight:600;cursor:default;'
            f'font-family:inherit;">{_esc(cta_lbl)}</button>'
            f'</div></div></div>'
            # campaign structure panel
            + struct_html +
            f'</div>'
        )

    # ── PRE-DEPLOY view ──────────────────────────────────────────────────────
    if not meta_ads:
        selected = run.get("selected_variants") or sorted(meta_copy.keys())
        sel_str  = ", ".join(f"Version {v}" for v in selected) if selected else "none selected"

        # Resolve + seed the editable audience (city geo + age + interests).
        from pikorua_adflow.tools import meta_targeting as _mt
        audience = _effective_audience(rf, brief) if rf else {}
        aud_label = _mt.audience_summary(audience) if audience else "India · Age 28–65"
        audience_panel = _audience_panel_html(run_id, audience) if rf else ""

        dry_note = ""
        if dry_run:
            dry_note = (
                '<div style="margin-bottom:16px;padding:12px 16px;background:var(--warn-soft);'
                'border:1px solid #e6d28a;border-radius:10px;font-size:0.85rem;color:var(--warn);">'
                '<strong>Preview mode</strong> — clicking the button below just shows you exactly how '
                'your ads will appear. Nothing is published and no money is spent. Live publishing '
                'can be switched on by an admin when you\'re ready to go live.</div>'
            )

        # Surface the last deploy attempt's errors — otherwise a failed publish
        # silently re-renders this view with no explanation.
        err_note = ""
        if dep_errors:
            def _err_row(e):
                msg = str(e.get("error", "Something went wrong — see developer log."))
                # If message still looks like raw JSON / API error, replace with generic
                if msg.startswith("POST ") or msg.startswith("{"):
                    msg = "Couldn't connect to Meta — the technical detail has been saved to the developer log."
                if e.get("fixable"):
                    action = ('<div style="margin-top:4px;font-size:0.8rem;color:var(--warn);">'
                              '&#8594; You can fix this — adjust the setting and try again.</div>')
                else:
                    action = ('<div style="margin-top:4px;font-size:0.8rem;color:var(--ink-soft);">'
                              '&#8594; Saved to developer log — no action needed from you.</div>')
                return (f'<div style="margin-top:10px;padding:10px 12px;background:rgba(0,0,0,0.04);'
                        f'border-radius:8px;">'
                        f'<strong>Version {_esc(str(e.get("variant","?")))}</strong><br>'
                        f'<span style="color:var(--ink);">{_esc(msg)}</span>'
                        f'{action}</div>')
            rows = "".join(_err_row(e) for e in dep_errors)
            err_note = (
                '<div style="margin-bottom:16px;padding:14px 16px;background:var(--danger-soft);'
                'border:1px solid #e6b3ab;border-radius:10px;font-size:0.85rem;color:var(--danger);">'
                '<strong>Last publish attempt didn\'t go through — nothing was created.</strong>'
                + rows + '</div>'
            )
        dry_note = err_note + dry_note

        # settings bar
        settings_bar = (
            f'<div style="background:var(--paper-warm);border:1px solid var(--line);border-radius:10px;'
            f'padding:16px 18px;margin-bottom:20px;display:flex;flex-wrap:wrap;'
            f'gap:12px;align-items:center;justify-content:space-between;">'
            f'<div>'
            f'<div class="eyebrow" style="margin-bottom:5px;">Publish settings</div>'
            f'<div style="font-size:0.85rem;color:var(--ink);">'
            f'<strong>{_esc(sel_str)}</strong>'
            f'<span style="color:var(--muted);margin-left:2px;"> &nbsp;&middot;&nbsp; '
            f'₹{budget}/day per ad &nbsp;&middot;&nbsp; Button: {_esc(cta_lbl)} '
            f'&nbsp;&middot;&nbsp; Goal: collect enquiries</span></div>'
            f'<div style="font-size:0.78rem;color:var(--muted);margin-top:3px;">'
            f'After enquiry, people see: {_esc(lp)}</div>'
            f'</div>'
            f'<div style="display:flex;align-items:center;gap:12px;flex-shrink:0;">'
            f'<button id="deploy-btn" class="btn" onclick="deployToMeta(\'{run_id}\')"'
            f' style="white-space:nowrap;">Preview &amp; publish</button>'
            f'<span id="deploy-status" style="font-size:0.82rem;color:var(--ink-soft);"></span>'
            f'</div></div>'
        )

        previews = ""
        for v in selected:
            h, b = _copy(v)
            struct = (
                f'<div style="margin-top:10px;padding:12px 14px;background:var(--paper-warm);'
                f'border:1px solid var(--line);border-radius:10px;font-size:0.8rem;'
                f'color:var(--ink-soft);line-height:1.9;">'
                f'<div class="eyebrow" style="margin-bottom:4px;">What will be set up</div>'
                f'<div><strong>Ad</strong> &nbsp;{_esc(prop)} &#8212; V{v} &nbsp;&middot;&nbsp; '
                f'collect enquiries &nbsp;&middot;&nbsp; <span class="badge badge-muted">starts paused</span></div>'
                f'<div><strong>Audience</strong> &nbsp;₹{budget}/day &nbsp;&middot;&nbsp; '
                f'{_esc(aud_label)}</div>'
                f'</div>'
            )
            badge = '<span style="font-size:0.74rem;color:var(--muted);font-style:italic;">Preview</span>'
            previews += _ad_card(v, h, b, badge, struct)

        return dry_note + audience_panel + settings_bar + previews

    # ── POST-DEPLOY view ─────────────────────────────────────────────────────
    if dry_run:
        top_note = (
            '<div style="margin-bottom:16px;padding:12px 16px;background:var(--warn-soft);'
            'border:1px solid #e6d28a;border-radius:10px;font-size:0.85rem;color:var(--warn);">'
            '<strong>This is a preview</strong> — below is exactly how your ads will look and what '
            'will be set up. Nothing has been published yet. An admin can switch on live publishing '
            'when you\'re ready, then you can publish for real.</div>'
        )
    else:
        top_note = (
            '<div style="margin-bottom:16px;padding:12px 16px;background:var(--green-soft);'
            'border:1px solid #a9cbb4;border-radius:10px;font-size:0.85rem;color:var(--green);">'
            '&#10003;&nbsp;<strong>Your ads are set up on Facebook &amp; Instagram — and paused.</strong> '
            'They won\'t spend anything until you switch them on in Meta Ads Manager.</div>'
        )

    # Warn if Meta forced us to drop any locations that need a special declaration.
    dropped_locs = run.get("meta_dropped_locations", [])
    if dropped_locs and not dry_run:
        locs = ", ".join(_esc(l) for l in dropped_locs)
        top_note += (
            '<div style="margin-bottom:16px;padding:12px 16px;background:var(--warn-soft);'
            'border:1px solid #e6d28a;border-radius:10px;font-size:0.85rem;color:var(--warn);">'
            f'<strong>Heads up — {locs} was removed from targeting.</strong> '
            f'Meta requires a one-time regulatory declaration to advertise in '
            f'{"these locations" if len(dropped_locs) > 1 else "this location"}, '
            'which has to be done by hand. Your campaign was published to all other '
            f'locations. To include {"them" if len(dropped_locs) > 1 else "it"}, make '
            'the declaration in Meta Ads Manager and add the location to the ad set there.</div>'
        )

    cards = ""
    for result in meta_ads:
        v  = result.get("variant", "?")
        h, b = _copy(v)

        if result.get("dry_run"):
            wd      = result.get("would_create", {}) or {}
            cr      = wd.get("creative", {}) or {}
            ad_info = wd.get("adset", {}) or {}
            # prefer copy from ad_copy.md; fall back to would_create creative
            if not h: h = cr.get("headline", "")
            if not b: b = cr.get("body", "")
            bgt = ad_info.get("daily_budget_inr", budget)
            badge = ('<span style="background:var(--warn-soft);color:var(--warn);border:1px solid #e6d28a;'
                     'padding:3px 10px;border-radius:999px;font-size:0.72rem;font-weight:600;">'
                     'Preview</span>')
            struct = (
                f'<div style="margin-top:10px;padding:12px 14px;background:var(--paper-warm);'
                f'border:1px solid var(--line);border-radius:10px;font-size:0.8rem;'
                f'color:var(--ink-soft);line-height:1.9;">'
                f'<div class="eyebrow" style="margin-bottom:4px;">What will be set up</div>'
                f'<div><strong>Ad</strong> &nbsp;{_esc(prop)} &#8212; V{v} &nbsp;&middot;&nbsp; '
                f'collect enquiries &nbsp;&middot;&nbsp; <span class="badge badge-muted">starts paused</span></div>'
                f'<div><strong>Audience</strong> &nbsp;₹{bgt}/day &nbsp;&middot;&nbsp; '
                f'{_esc(ad_info.get("geo") or "India")}</div>'
                f'<div><strong>Button</strong> &nbsp;{_esc(cta_lbl)}</div>'
                f'<div><strong>After enquiry</strong> &nbsp;{_esc(lp)}</div>'
                f'</div>'
            )
        else:
            cid  = result.get("campaign_id", "—")
            asid = result.get("adset_id", "—")
            aid  = result.get("ad_id", "—")
            badge = ('<span style="background:var(--green-soft);color:var(--green);border:1px solid #a9cbb4;'
                     'padding:3px 10px;border-radius:999px;font-size:0.72rem;">&#10003; Paused</span>')
            struct = (
                f'<div style="margin-top:10px;padding:12px 14px;background:var(--green-soft);'
                f'border:1px solid #bcd6c4;border-radius:10px;font-size:0.8rem;'
                f'color:var(--green);line-height:1.9;">'
                f'<div class="eyebrow" style="color:var(--green-mid);margin-bottom:4px;">Set up on Meta — paused</div>'
                f'<div>Your ad is ready in Meta Ads Manager and will not spend until you switch it on.</div>'
                f'<details style="margin-top:6px;"><summary style="cursor:pointer;font-size:0.76rem;color:var(--green-mid);">Reference IDs</summary>'
                f'<div style="font-size:0.72rem;color:var(--ink-soft);margin-top:4px;">'
                f'Campaign {_esc(cid)} &middot; Ad set {_esc(asid)} &middot; Ad {_esc(aid)}</div></details>'
                f'</div>'
            )

        cards += _ad_card(v, h, b, badge, struct)

    for err in dep_errors:
        v = err.get("variant", "?")
        msg = str(err.get("error", "Something went wrong — see developer log."))
        if msg.startswith("POST ") or msg.startswith("{"):
            msg = "Couldn't connect to Meta — the technical detail has been saved to the developer log."
        if err.get("fixable"):
            hint = '<div style="margin-top:4px;font-size:0.8rem;color:var(--warn);">&#8594; You can fix this — adjust the setting and try again.</div>'
        else:
            hint = '<div style="margin-top:4px;font-size:0.8rem;color:var(--ink-soft);">&#8594; Saved to developer log — no action needed from you.</div>'
        cards += (
            f'<div style="margin-bottom:16px;padding:14px 16px;background:var(--danger-soft);'
            f'border:1px solid #e6bdb6;border-radius:10px;font-size:0.85rem;color:var(--danger);">'
            f'<strong>Version {v} couldn\'t be published.</strong><br>'
            f'<span style="color:var(--ink);font-size:0.85rem;">{_esc(msg)}</span>'
            f'{hint}</div>'
        )

    return top_note + cards


_IDEOGRAM_SPEEDS = {"TURBO", "DEFAULT", "QUALITY"}
# Ad-friendly ratios we expose. 4x5 = Meta feed (recommended), 1x1 = square
# (multi-placement), 16x9 = wide/landscape. Values are Ideogram v3 aspect_ratio codes.
_IDEOGRAM_RATIOS = {"4x5", "1x1", "16x9", "9x16"}

# Two-mode prompt hygiene.  We let Ideogram bake in the ad TEXT (headline + a
# locality/price line) on banners — that creative freedom is the point — but we
# NEVER let it render the brand logo/wordmark (it can't spell "PIKORUA" and invents
# "CHEDANA"-style gibberish), and we never let pixel-dimension or font-name/point-size
# specs leak in (they fight the aspect ratio or get rendered as literal garbled text).
#
# _PROMPT_STRIP_COMMON is applied in BOTH modes: it kills logo/wordmark/brand-name
# placement, pixel dims, 4K/8K, literal point sizes, and font-name references.
_PROMPT_STRIP_COMMON = re.compile(
    r"""(?ix)
    \b\d{3,4}\s*[x×]\s*\d{3,4}\s*px?\b      # 1200x628px, 1080x1080
    | \b\d+K\b                                 # 4K, 8K
    | [^.]*\b(logo|wordmark|word\s*mark|brand\s*mark|emblem|monogram|watermark
        |company\s*name|brand\s*text|brand\s*name|brand\s*logo|PIKORUA|PIKURUA
        |include\s+(?:the\s+)?brand|add\s+(?:the\s+)?brand|brand\s+instruction
        |brand\s+corner|brand\s+mark\s+instruction)\b[^.]*\.?
    | [^.]*\b\d{1,3}\s*pt\b[^.]*\.?          # sentences with font size specs (32pt, 72pt, 88pt)
    | [^.]*\b(Cormorant|Garamond|Didot|Helvetica|Futura|Bodoni|sans.serif|serif\s+at\s+\d)\b[^.]*\.?
    """,
    re.VERBOSE,
)

# _PROMPT_STRIP_SCENE_EXTRA is applied ONLY to text-free scene prompts (4–5).  It
# additionally removes any instruction to render words/prices/overlays at all.
_PROMPT_STRIP_SCENE_EXTRA = re.compile(
    r"""(?ix)
    [^.]*\b(locality\s+(?:name|detail)|text\s+overlay|overlay\s+approach
        |gradient[^.]*\bfor\s+text\b|detail\s+below|headline\s+and\s+\w+\s+detail
        |info\s+line|price\s+(?:below|beneath)|font\s+name)\b[^.]*\.?
    """,
    re.VERBOSE,
)

# Appended to text-free scene prompts — forbids ALL invented text and brand marks.
_PROMPT_GUARD_SCENE = (
    " Do not render any company logo, brand wordmark, emblem, monogram, or watermark. "
    "Do not invent brand names. Add no text, captions, labels, or signage unless "
    "text is explicitly specified in this prompt."
)

_HEADLINE_PREFIX_RE = re.compile(r'^\[HEADLINE:"([^"]+)"\]\s*')


def _composite_logo(image_path: Path, logo_path: Path) -> None:
    """
    Place the brand logo in the bottom-right corner of the image at image_path.

    Sizing: the logo is scaled so its longest side is 18% of the image's shortest
    side, preserving aspect ratio exactly — no stretch, no crop.  A 6% padding gap
    is kept from the right and bottom edges.  The image canvas and pixel dimensions
    are never changed.
    """
    from PIL import Image as _PILImage
    base = _PILImage.open(image_path).convert("RGBA")
    logo = _PILImage.open(logo_path).convert("RGBA")

    W, H = base.size
    lw, lh = logo.size

    # Target: logo longest side = 18% of the image's shortest dimension.
    max_logo_px = int(min(W, H) * 0.18)
    scale = max_logo_px / max(lw, lh)
    new_lw = max(1, round(lw * scale))
    new_lh = max(1, round(lh * scale))
    logo = logo.resize((new_lw, new_lh), _PILImage.LANCZOS)

    pad = int(min(W, H) * 0.02)   # 2% inset from each edge — snug corner
    x = W - new_lw - pad
    y = H - new_lh - pad

    # Paste using the logo's own alpha as mask — no white box, no background.
    base.paste(logo, (x, y), mask=logo)

    # Save back as PNG to preserve the alpha pipeline; then convert to RGBA-free
    # PNG if the source was opaque (keeps file size reasonable).
    base = base.convert("RGB")   # final ad images are opaque
    base.save(image_path, format="PNG")


def _tidy_prompt(text: str) -> str:
    """Collapse the empty-sentence debris left behind after regex stripping."""
    text = re.sub(r"\s*\.(?:\s*\.)+", ".", text)
    text = re.sub(r"\s+([.,])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _sanitize_image_prompt(text: str, is_banner: bool = False,
                           locality: str = "", price_cr: str = "") -> str:
    """Clean a brief prompt before it reaches Ideogram.

    Banner mode (is_banner=True): Ideogram composes the full ad — it bakes in the
    headline AND a locality/price line as integrated typography.  We strip only the
    things it can't handle (logo/wordmark, pixel dims, font names, point sizes) and
    inject the exact text strings so spelling is anchored, while leaving the crew's
    creative placement language intact.  The logo is composited by PIL afterward, so
    we reserve the bottom-right corner as clean negative space.

    Scene mode (is_banner=False): a pure text-free photograph (prompts 4–5) — strip
    ALL text/overlay/brand instructions and forbid invented text entirely."""
    m = _HEADLINE_PREFIX_RE.match(text or "")
    headline = m.group(1).strip().rstrip(".") if m else None
    body = _HEADLINE_PREFIX_RE.sub("", text or "")
    cleaned = _PROMPT_STRIP_COMMON.sub("", body)

    if not is_banner:
        cleaned = _PROMPT_STRIP_SCENE_EXTRA.sub("", cleaned)
        return _tidy_prompt(cleaned) + _PROMPT_GUARD_SCENE

    cleaned = _tidy_prompt(cleaned)

    # Build the secondary locality·price line Ideogram must render verbatim.
    parts = [p for p in [(locality or "").strip(),
                         (f"₹{price_cr} Cr" if price_cr else "")] if p]
    secondary = "  ·  ".join(parts)

    guard = (
        ' Treat this as a finished, art-directed luxury advertisement: build the '
        'photograph first, then lay refined editorial typography over it with real '
        'negative space and hierarchy — never a cheap solid colour bar, never builder-ad '
        'styling.')
    if headline:
        guard += (f' Render the headline "{headline}" as the dominant text element in an '
                  f'elegant serif.')
    if secondary:
        guard += (f' Beneath it render a smaller secondary line reading exactly "{secondary}". '
                  'This secondary line must be clearly legible — set it against a darker or '
                  'higher-contrast part of the scene (a soft shadow, a deeper-toned surface) so '
                  'it never washes out against a pale background; it should read at a glance, '
                  'though it stays quieter than the headline.')
    guard += (' Spell every word and number letter-for-letter, exactly as written. '
              'Do NOT render any company logo, brand wordmark, emblem, monogram, watermark, '
              'or brand name; keep the bottom-right corner clean, empty negative space so a '
              'logo can be composited there afterward.')
    return cleaned + guard

def _call_ideogram_v3(prompt: str, key: str, speed: str = "QUALITY", aspect: str = "4x5") -> bytes:
    """Ideogram v3 — multipart/form-data. Better photorealism for scene-only prompts."""
    speed = speed.upper() if speed else "QUALITY"
    if speed not in ("TURBO", "DEFAULT", "QUALITY"):
        speed = "QUALITY"
    # Ideogram v3 expects the bare "NxN" ratio (e.g. "4x5"), NOT the old "ASPECT_4_5"
    # enum — sending the enum returns HTTP 400 and silently drops the prompt.
    _V3_RATIOS = {"1x1", "4x5", "5x4", "16x9", "9x16", "2x3", "3x2", "3x4", "4x3"}
    clean_aspect = (aspect or "4x5").lower().replace(":", "x")
    aspect_code = clean_aspect if clean_aspect in _V3_RATIOS else "4x5"

    import urllib.request, json, time
    boundary = "IdeogramV3Boundary"
    parts = []
    for name, value in [("prompt", prompt), ("aspect_ratio", aspect_code), ("rendering_speed", speed)]:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )
    parts.append(f"--{boundary}--\r\n")
    body = "".join(parts).encode("utf-8")

    req = urllib.request.Request(
        "https://api.ideogram.ai/v1/ideogram-v3/generate",
        data=body,
        headers={"Api-Key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    data = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            is_rate_limit = e.code == 429 or (e.code == 403 and "1010" in detail)
            if is_rate_limit and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Ideogram v3 request failed [{e.code}]: {detail}") from e

    img_url = data["data"][0]["url"]
    img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(img_req, timeout=60) as img_resp:
        return img_resp.read()


def _call_ideogram(prompt: str, key: str, speed: str = "QUALITY", aspect: str = "4x5") -> bytes:
    """
    Ideogram 4.0 API — standard JSON payload.
    Converts simple aspect ratio strings (e.g., '4x5', '16x9') to v4 resolution strings.
    """
    speed = speed.upper() if speed else "QUALITY"
    
    _RESOLUTION_MAP = {
        "1x1": "2048x2048",
        "4x5": "1792x2240",
        "16x9": "2560x1440", 
        "9x16": "1440x2560"   
    }
    clean_aspect = aspect.lower().replace(":", "x") if aspect else "4x5"
    
    # 2. Get the exact resolution string (fallback to "1024x1280" if not found)
    resolution = _RESOLUTION_MAP.get(clean_aspect, "1024x1280")

    # 3. Build v4 JSON payload
    payload = {
        "text_prompt": prompt,
        "resolution": resolution,
        "rendering_speed": speed,
    }

    import urllib.request
    import json
    import time
    
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://api.ideogram.ai/v1/ideogram-v4/generate",
        data=body,
        headers={
            "Api-Key": key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    # Retry logic with backoff
    data = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            is_rate_limit = e.code == 429 or (e.code == 403 and "1010" in detail)
            if is_rate_limit and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Ideogram image request failed [{e.code}]: {detail}") from e

    img_url = data["data"][0]["url"]

    # Download ephemeral image
    img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(img_req, timeout=60) as img_resp:
            return img_resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Ideogram image download failed [{e.code}]: {detail}") from e

def _image_backend(i: int, ideogram_key: str, replicate_token: str,
                   together_key: str) -> tuple[str, str]:
    """
    Return (backend_name, tier) for prompt index i (1-based), or ("", "") if no
    image service is connected.

    Ideogram 3 is the production backend and handles ALL prompts (it renders
    text-in-image banners AND photorealistic scenes). Replicate Flux is used for
    the render prompts (4-5) only if a token is explicitly set. Together AI is an
    optional fallback if its key is set.
    """
    if i > 3 and replicate_token:
        return "replicate", "paid"
    if ideogram_key:
        return "ideogram", "paid"
    if together_key:
        return "together", "free"
    return "", ""


class ImageGenReq(BaseModel):
    prompts: list[int] | None = None          # which prompt numbers to (re)generate; None/empty = all
    alongside: list[int] = Field(default_factory=list)  # generate new version WITHOUT overwriting existing
    speed: str = "QUALITY"                     # fallback rendering speed (Ideogram only)
    speeds: dict[int, str] = Field(default_factory=dict)  # per-prompt speed overrides, keyed by prompt number
    ratio: str = "4x5"                         # fallback aspect ratio (Meta feed default)
    ratios: dict[int, str] = Field(default_factory=dict)  # per-prompt aspect-ratio overrides
    custom_prompts: dict[int, str] = Field(default_factory=dict)  # user-edited prompt text, keyed by prompt number


@app.post("/generate-images/{run_id}")
def generate_images(run_id: str, payload: ImageGenReq | None = None):
    """
    Generate images for a completed run.

    Body (all optional): {prompts:[1,3], speed:"QUALITY", speeds:{1:"TURBO"}}.
      - prompts: only these prompt numbers are generated (and force-overwritten,
        since the operator explicitly asked for them). Omit/empty = all prompts,
        skipping any that already exist.
      - speed / speeds: Ideogram rendering speed — TURBO (fast/cheap), DEFAULT,
        or QUALITY (best banner text). `speeds` overrides `speed` per prompt.

    Backend priority (per prompt):
      1. Ideogram 3.0 (all prompts) — primary, requires IDEOGRAM_API_KEY
      2. Replicate Flux (render prompts 4–5) — if REPLICATE_API_TOKEN set
      3. Together AI FLUX.1 — optional fallback if TOGETHER_API_KEY set
    Images saved to review_folder/images/image_N.png.
    """
    import os
    payload = payload or ImageGenReq()
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = _runs[run_id]
    if run["status"] != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or no review folder.")

    ideogram_key    = os.getenv("IDEOGRAM_API_KEY", "")
    replicate_token = os.getenv("REPLICATE_API_TOKEN", "")
    together_key    = os.getenv("TOGETHER_API_KEY", "")

    review_folder = Path(run["review_folder"])
    visual_path = review_folder / "visual_brief.md"
    if not visual_path.exists():
        raise HTTPException(status_code=400, detail="visual_brief.md not found in review folder.")

    visual_text = visual_path.read_text(encoding="utf-8")
    image_prompts = _parse_image_prompts(visual_text)
    if not image_prompts:
        raise HTTPException(status_code=400, detail="No image prompts found in visual_brief.md.")

    images_dir = review_folder / "images"
    images_dir.mkdir(exist_ok=True)

    # Which prompts to act on. An explicit list means "(re)generate exactly these,
    # overwriting" — the operator picked them on purpose. No list = all, but skip any
    # image that already exists (the "create the rest" default).
    alongside_set = {p for p in (payload.alongside or []) if 1 <= p <= len(image_prompts)}
    selected = {p for p in (payload.prompts or []) if 1 <= p <= len(image_prompts)}
    explicit = bool(selected)
    if not explicit:
        selected = set(range(1, len(image_prompts) + 1))

    brief = run.get("brief", {})
    locality  = brief.get("locality", "")
    price_cr  = str(brief.get("price_cr", "")).strip()

    import re as _re
    results = []
    errors = []
    for i, (_, prompt_text) in enumerate(image_prompts, 1):
        is_alongside = i in alongside_set
        if i not in selected and not is_alongside:
            continue
        if is_alongside:
            # Find next available versioned filename: image_N_v2.png, image_N_v3.png, …
            k = 2
            while (images_dir / f"image_{i}_v{k}.png").exists():
                k += 1
            out_path = images_dir / f"image_{i}_v{k}.png"
        else:
            out_path = images_dir / f"image_{i}.png"
        if out_path.exists() and not explicit and not is_alongside:
            results.append({"prompt": i, "status": "already_exists", "file": str(out_path)})
            continue
        backend, _ = _image_backend(i, ideogram_key, replicate_token, together_key)
        if not backend:
            errors.append({"prompt": i, "backend": "none", "fixable": False,
                           "error": "No image service is connected yet. Your developer needs to "
                                    "add an Ideogram API key to generate images."})
            continue
        speed = payload.speeds.get(i) or payload.speed
        aspect = payload.ratios.get(i) or payload.ratio
        # Allow caller to supply a hand-edited prompt; default to the parsed brief text.
        effective_prompt = payload.custom_prompts.get(i) or prompt_text
        is_banner = effective_prompt.strip().startswith("[HEADLINE:")
        sanitized = _sanitize_image_prompt(effective_prompt, is_banner, locality, price_cr)
        try:
            if is_banner and ideogram_key:
                # v4 for text banners — better text rendering; DEFAULT = balanced quality
                v4_speed = speed if speed in ("TURBO", "DEFAULT") else "DEFAULT"
                img_bytes = _call_ideogram(sanitized, ideogram_key, v4_speed, aspect)
            elif ideogram_key:
                # v3 for pure scene/photo prompts — better photorealism, more natural
                img_bytes = _call_ideogram_v3(sanitized, ideogram_key, speed, aspect)
            else:
                raise RuntimeError("No image service connected — add IDEOGRAM_API_KEY to .env")
            out_path.write_bytes(img_bytes)
            # Banner text (headline + locality + price) is rendered by Ideogram as part
            # of the composition — no PIL footer bar. We only composite the real logo,
            # which Ideogram can't spell. The prompt reserves the bottom-right corner.
            # Composite the brand logo bottom-right if one has been uploaded.
            if _BRAND_LOGO_PATH.exists():
                try:
                    logo_backup_dir = out_path.parent / ".logo_backup"
                    logo_backup_dir.mkdir(exist_ok=True)
                    logo_backup = logo_backup_dir / out_path.name
                    import shutil as _shutil
                    _shutil.copy2(out_path, logo_backup)
                    _composite_logo(out_path, _BRAND_LOGO_PATH)
                except Exception:
                    pass
            results.append({"prompt": i, "status": "generated", "backend": backend, "file": str(out_path)})
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log(f"Image generation — prompt {i} ({backend})", exc)
            errors.append({"prompt": i, "backend": backend, "error": friendly["message"],
                           "fixable": friendly["fixable"]})

    return {"run_id": run_id, "generated": results, "errors": errors}


class AudienceSave(BaseModel):
    city: str = ""
    city_key: str | None = None
    region: str = ""
    country: str = "IN"
    radius_km: int = 25
    age_min: int = 28
    age_max: int = 65
    interests: list[dict] = Field(default_factory=list)   # [{id, name}]
    behaviours: list[dict] = Field(default_factory=list)   # [{id, name}]
    nri_countries: list[str] = Field(default_factory=list) # ISO-2 codes, e.g. ["AE","US","GB"]
    end_time: str = ""                                     # ISO 8601, optional


@app.get("/audience/{run_id}")
def get_audience(run_id: str):
    """Current ad-set audience for a run (seeds the curated default on first call)."""
    run = _require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = _effective_audience(review_folder, run.get("brief", {}))
    from pikorua_adflow.tools import meta_targeting as _mt
    return {"run_id": run_id, "audience": audience, "summary": _mt.audience_summary(audience)}


@app.post("/audience/{run_id}")
def save_audience(run_id: str, payload: AudienceSave):
    """Persist the user-edited audience for a run."""
    run = _require_complete(run_id)
    review_folder = Path(run["review_folder"])
    audience = payload.model_dump()
    _save_audience(review_folder, audience)
    from pikorua_adflow.tools import meta_targeting as _mt
    return {"run_id": run_id, "audience": audience, "summary": _mt.audience_summary(audience)}


@app.get("/audience-search")
def audience_search(q: str, type: str = "interest"):
    """Typeahead proxy to Meta's read-only Targeting Search (for the add-chip UI)."""
    from pikorua_adflow.tools import meta_targeting as _mt
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        return {"results": [], "error": "META_ACCESS_TOKEN not set"}
    q = (q or "").strip()
    if len(q) < 2:
        return {"results": []}
    try:
        if type == "city":
            return {"results": _mt.search_cities(q, token)}
        if type == "behaviour":
            return {"results": _mt.search_behaviours(q, token)}
        return {"results": _mt.search_interests(q, token)}
    except Exception as exc:
        return {"results": [], "error": str(exc)}


@app.post("/deploy-to-meta/{run_id}")
def deploy_to_meta(run_id: str):
    """
    Deploy selected variants to Meta Ads as OUTCOME_LEADS campaigns (all PAUSED).
    Uses the effective copy (user edits > AI rewrites > AI original) and any
    uploaded/generated image. Stores returned ad IDs in the run dict under meta_ads.
    DRY_RUN=true (default): skips API calls and returns a payload preview.
    """
    run = _require_complete(run_id)
    review_folder = Path(run["review_folder"])
    brief = run.get("brief", {})

    # Effective copy already folds in user edits, rewrites, added & deleted versions.
    meta_copy = _effective_meta(review_folder)

    selected = run.get("selected_variants") or sorted(meta_copy.keys())
    # Never try to publish a version the user deleted.
    selected = [v for v in selected if v in meta_copy]
    if not selected:
        raise HTTPException(status_code=400, detail="No ad copy variants found in review folder.")

    from pikorua_adflow.tools.meta_tool import deploy_ad, create_campaign

    campaign_name = brief.get("property_name", "Pikorua Campaign")
    city = brief.get("city", "India")
    landing_page_url = brief.get("landing_page_url", "https://pikorua.in/")
    daily_budget_inr = int(brief.get("daily_budget_inr", 1000))
    cta = brief.get("cta", "GET_QUOTE")

    # Resolve the ad-set audience (city geo + age + interests/behaviours) once for
    # all variants. Seeds the curated default if the user never opened the panel.
    from pikorua_adflow.tools import meta_targeting as _mt
    audience = _effective_audience(review_folder, brief)
    targeting_spec = _mt.build_targeting_spec(audience)
    audience_label = _mt.audience_summary(audience)
    end_time = audience.get("end_time", "")

    # Create one shared campaign for all variants (skipped in dry-run — deploy_ad handles that).
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    shared_campaign_id = ""
    if not dry_run:
        _token = os.getenv("META_ACCESS_TOKEN", "")
        _account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
        try:
            shared_campaign_id = create_campaign(
                campaign_name=campaign_name,
                token=_token,
                ad_account_id=_account_id,
            )
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log("Meta deploy — create campaign", exc)
            return {"run_id": run_id, "deployed": [], "errors": [{"variant": None, "error": friendly["message"], "fixable": friendly["fixable"]}], "dropped_locations": []}

    results = []
    errors = []
    for variant_num in selected:
        copy = meta_copy.get(variant_num, {})
        headline = copy.get("headline", "")
        body_text = copy.get("body", "")

        # Use the user-assigned image for this variant, falling back to image_{num}.png
        _v_edits = _load_edits(review_folder).get("meta", {}).get(str(variant_num), {})
        _assigned = _v_edits.get("image_num")
        if _assigned and (review_folder / "images" / f"image_{_assigned}.png").exists():
            image_path = review_folder / "images" / f"image_{_assigned}.png"
        elif (review_folder / "images" / f"image_{variant_num}.png").exists():
            image_path = review_folder / "images" / f"image_{variant_num}.png"
        else:
            image_path = None

        try:
            result = deploy_ad(
                variant=variant_num,
                headline=headline,
                body=body_text,
                image_path=image_path,
                campaign_name=campaign_name,
                city=city,
                landing_page_url=landing_page_url,
                daily_budget_inr=daily_budget_inr,
                cta=cta,
                targeting_spec=targeting_spec,
                audience_label=audience_label,
                end_time=end_time,
                campaign_id=shared_campaign_id,
            )
            results.append(result)
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log(f"Meta deploy — variant {variant_num}", exc)
            errors.append({"variant": variant_num, "error": friendly["message"],
                           "fixable": friendly["fixable"]})

    # All variants failed — clean up the shared campaign so nothing is left orphaned.
    if shared_campaign_id and not results:
        from pikorua_adflow.tools.meta_tool import _delete
        _delete(shared_campaign_id, _token)

    # Aggregate any locations Meta made us drop (e.g. Singapore's universal ads
    # declaration) so the operator can re-add them in Ads Manager once declared.
    dropped = sorted({loc for r in results for loc in r.get("dropped_locations", [])})

    # Only persist meta_ads when there are real results — an empty list would
    # cause the pre-deploy view to render again on reload, hiding the errors.
    if results:
        _runs[run_id]["meta_ads"] = results
    if errors:
        _runs[run_id]["meta_deploy_errors"] = errors
    if dropped:
        _runs[run_id]["meta_dropped_locations"] = dropped
    else:
        _runs[run_id].pop("meta_dropped_locations", None)
    _save_runs()

    return {"run_id": run_id, "deployed": results, "errors": errors,
            "dropped_locations": dropped}


@app.get("/image/{run_id}/{filename}")
def serve_image(run_id: str, filename: str):
    """Serve a generated image file from a run's images/ subfolder."""
    import re
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Run not found.")
    run = _runs[run_id]
    if not run.get("review_folder"):
        raise HTTPException(status_code=404, detail="No review folder for this run.")
    # Safety: only allow image_N.png and image_N_vK.png filenames to prevent path traversal
    if not re.fullmatch(r'image_\d+(?:_v\d+)?\.png', filename):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    img_path = Path(run["review_folder"]) / "images" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(content=img_path.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/approve/{run_id}")
def approve_run(run_id: str, req: ApproveRequest = None):
    """
    Mark a completed run as approved and store it in Qdrant vector memory.
    req.selected_variants: variant numbers chosen for launch (empty = approve all).
    """
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run = _runs[run_id]
    if run["status"] != "complete":
        raise HTTPException(status_code=400, detail="Only completed runs can be approved.")
    if not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="No review folder found for this run.")

    selected = (req.selected_variants if req else []) or []

    from pikorua_adflow.tools.memory_tool import approve_and_store
    review_folder = Path(run["review_folder"])
    message = approve_and_store(
        run_id=run_id,
        brief=run.get("brief", {}),
        review_folder=review_folder,
        scorecard_summary=run.get("copy_scorecard_summary"),
    )

    _runs[run_id]["approved"] = True
    _runs[run_id]["selected_variants"] = selected  # [] means all
    _save_runs()
    return {"status": "approved", "run_id": run_id, "message": message, "selected_variants": selected}


@app.delete("/run/{run_id}")
def delete_run(run_id: str):
    """Remove a run from the registry. Blocked for runs that are currently in progress."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    status = _runs[run_id].get("status", "")
    if status.startswith("running_") or status == "queued":
        raise HTTPException(status_code=400, detail="Cannot delete a run that is currently in progress.")
    del _runs[run_id]
    _save_runs()
    return {"status": "deleted", "run_id": run_id}


@app.post("/rerun/{run_id}")
def rerun_campaign(run_id: str):
    """Re-queue a failed run using its original brief."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = _runs[run_id]
    if run.get("status") != "failed":
        raise HTTPException(status_code=400, detail="Only failed runs can be re-run.")

    brief_data = run.get("brief", {})
    brief = CampaignBrief(**brief_data)

    new_run_id = str(uuid.uuid4())[:8]
    _runs[new_run_id] = {
        "status": "queued",
        "brief": brief_data,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "review_folder": None,
        "rerun_of": run_id,
    }
    _save_runs()

    thread = threading.Thread(target=_run_pipeline, args=(new_run_id, brief), daemon=True)
    thread.start()

    return {"status": "queued", "run_id": new_run_id, "rerun_of": run_id}


@app.post("/upload-crm-audience")
def upload_crm_audience(req: CRMAudienceRequest):
    """
    Upload qualified CRM leads to Meta as a Custom Audience + Lookalike.
    Requires META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in environment.
    Phase 3 only — safe to call, returns 503 if token missing.
    """
    import os
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN not set — Phase 3 prerequisite.")

    ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").replace("act_", "")
    if not ad_account_id:
        raise HTTPException(status_code=503, detail="META_AD_ACCOUNT_ID not set in .env.")

    from pikorua_adflow.tools.meta_audience_tool import upload_crm_lookalike, upload_crm_split_audiences

    if req.split:
        result = upload_crm_split_audiences(
            ad_account_id=ad_account_id,
            target_countries=req.target_countries,
        )
    else:
        result = upload_crm_lookalike(
            ad_account_id=ad_account_id,
            target_countries=req.target_countries,
        )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@app.get("/runs/json")
def list_runs_json():
    """Lists all runs as JSON (most recent first)."""
    return sorted(_runs.items(), key=lambda x: x[1]["created_at"], reverse=True)


@app.get("/runs", response_class=HTMLResponse)
def list_runs():
    """Renders a simple HTML history page of all runs this session."""
    rows = sorted(_runs.items(), key=lambda x: x[1]["created_at"], reverse=True)

    def status_badge(s):
        cls = {
            "complete": "badge-ok", "failed": "badge-danger",
            "running_stage1": "badge-warn", "running_stage2": "badge-warn",
            "running_stage3": "badge-warn", "queued": "badge-muted",
        }.get(s, "badge-muted")
        labels = {
            "complete": "Ready", "failed": "Failed",
            "running_stage1": "Researching", "running_stage2": "Writing",
            "running_stage3": "Polishing", "queued": "Starting",
        }
        label = labels.get(s, s.replace("_", " ").title())
        return f'<span class="badge {cls}">{label}</span>'

    active_run_ids = json.dumps([rid for rid, r in rows if r.get("status") not in ("complete", "failed")])

    run_rows = ""
    for run_id, run in rows:
        brief = run.get("brief", {})
        scorecard = run.get("copy_scorecard_summary", "")
        scorecard_html = f'<div style="font-size:0.78rem;color:var(--ink-soft);margin-top:4px;">{scorecard}</div>' if scorecard else ""
        folder_html = ""
        approved = run.get("approved", False)
        approve_cell = ""
        if run.get("status") == "complete":
            if approved:
                approve_cell = '<span style="color:var(--green);font-size:0.82rem;">&#10003; Approved</span>'
            else:
                approve_cell = (
                    f'<button onclick="approveRun(\'{run_id}\')" id="approve-{run_id}" '
                    f'class="btn btn-sm">Approve</button>'
                )
        rerun_cell = ""
        if run.get("status") == "failed":
            rerun_cell = (
                f'<button onclick="rerunCampaign(\'{run_id}\')" id="rerun-{run_id}" '
                f'class="btn btn-ghost btn-sm">Try again</button>'
            )
        status_val = run.get("status", "")
        is_running = status_val.startswith("running_") or status_val == "queued"
        delete_cell = "" if is_running else (
            f'<button onclick="deleteRun(\'{run_id}\')" '
            f'title="Remove from list" '
            f'style="background:none;border:none;color:var(--muted);font-size:1rem;'
            f'cursor:pointer;padding:2px 6px;line-height:1;" '
            f'onmouseover="this.style.color=\'var(--danger)\'" '
            f'onmouseout="this.style.color=\'var(--muted)\'">&times;</button>'
        )
        view_cell = ('<a href="/results/' + run_id + '">Open &rarr;</a>'
                     if run.get("status") == "complete" else "")
        run_rows += f"""
        <tr id="row-{run_id}">
          <td>
            <div style="font-weight:600;color:var(--ink);">{brief.get('property_name','—')}</div>
            <span style="font-size:0.78rem;color:var(--muted);">{brief.get('city','')} · ₹{brief.get('price_cr','')} Cr · {brief.get('platform','')}</span>
            {scorecard_html}
            {folder_html}
          </td>
          <td id="status-{run_id}">{status_badge(run.get('status',''))}</td>
          <td style="font-size:0.82rem;color:var(--ink-soft);white-space:nowrap;">
            {run.get('created_at','')[:16].replace('T',' ')}
          </td>
          <td>{approve_cell}{rerun_cell}</td>
          <td>{view_cell}</td>
          <td style="text-align:center;">{delete_cell}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  {_theme_fouc()}
  <title>PIKORUA — My Campaigns</title>
  <link rel="stylesheet" href="/brand.css"/>
</head><body>
  {_topbar('runs')}
  <div class="wrap-wide">
  <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;margin-bottom:1.6rem;flex-wrap:wrap;">
    <div>
      <div class="eyebrow">Your work</div>
      <h1 style="margin:0.1rem 0 0;">My campaigns</h1>
    </div>
    <a href="/portal" class="btn">+ New campaign</a>
  </div>

  <div class="card" style="padding:1.1rem 1.3rem;margin-bottom:1.4rem;display:flex;
      flex-wrap:wrap;gap:1rem;align-items:center;justify-content:space-between;">
    <div>
      <div style="font-weight:600;color:var(--ink);font-size:0.95rem;">Find more buyers like your past leads</div>
      <div style="font-size:0.82rem;color:var(--muted);max-width:48ch;">Use your existing enquiry list to find similar people on Facebook &amp; Instagram.</div>
    </div>
    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;">
      <div style="display:flex;gap:8px;">
        <button id="crm-split" class="btn btn-sm" onclick="uploadCRMAudiences(true)">Find similar buyers</button>
        <button id="crm-all" class="btn btn-ghost btn-sm" onclick="uploadCRMAudiences(false)">All leads</button>
      </div>
      <div id="crm-result" style="font-size:0.78rem;color:var(--ink-soft);text-align:right;max-width:340px;"></div>
    </div>
  </div>

  <table>
    <thead><tr>
      <th>Campaign</th><th>Status</th><th>Started</th><th>Approve</th><th>Details</th><th></th>
    </tr></thead>
    <tbody>{run_rows if run_rows else '<tr><td colspan="6" style="padding:1.4rem;color:var(--muted);">No campaigns yet. <a href="/portal">Create your first one &rarr;</a></td></tr>'}</tbody>
  </table>
  <p style="margin-top:1.4rem;font-size:0.9rem;">
    <a href="/portal">&#8592; Create a new campaign</a>
  </p>
  </div>
  <script>
    async function rerunCampaign(runId) {{
      const btn = document.getElementById('rerun-' + runId);
      btn.disabled = true;
      btn.textContent = 'Starting...';
      try {{
        const res = await fetch('/rerun/' + runId, {{method: 'POST'}});
        const data = await res.json();
        if (res.ok) {{
          btn.replaceWith(Object.assign(document.createElement('span'), {{
            textContent: '↪ Restarted',
            style: 'font-size:0.82rem;color:var(--ink-soft);'
          }}));
          setTimeout(() => location.reload(), 800);
        }} else {{
          btn.disabled = false;
          btn.textContent = 'Try again';
          alert('Error: ' + (data.detail || 'Unknown error'));
        }}
      }} catch(e) {{
        btn.disabled = false;
        btn.textContent = 'Try again';
        alert('Request failed: ' + e.message);
      }}
    }}
    async function approveRun(runId) {{
      const btn = document.getElementById('approve-' + runId);
      btn.disabled = true;
      btn.textContent = 'Saving...';
      try {{
        const res = await fetch('/approve/' + runId, {{method: 'POST'}});
        const data = await res.json();
        if (res.ok) {{
          btn.replaceWith(Object.assign(document.createElement('span'), {{
            textContent: '✓ Approved',
            style: 'color:var(--green);font-size:0.82rem;'
          }}));
        }} else {{
          btn.disabled = false;
          btn.textContent = 'Approve';
          alert('Error: ' + (data.detail || 'Unknown error'));
        }}
      }} catch(e) {{
        btn.disabled = false;
        btn.textContent = 'Approve';
        alert('Request failed: ' + e.message);
      }}
    }}
    async function deleteRun(runId) {{
      if (!confirm('Remove this campaign from the list? (Your files are kept on disk.)')) return;
      try {{
        const res = await fetch('/run/' + runId, {{method: 'DELETE'}});
        if (res.ok) {{
          const row = document.getElementById('row-' + runId);
          if (row) row.remove();
        }} else {{
          const data = await res.json();
          alert('Error: ' + (data.detail || 'Unknown error'));
        }}
      }} catch(e) {{
        alert('Request failed: ' + e.message);
      }}
    }}
    async function uploadCRMAudiences(split) {{
      const splitBtn = document.getElementById('crm-split');
      const allBtn   = document.getElementById('crm-all');
      const result   = document.getElementById('crm-result');
      const activeBtn = split ? splitBtn : allBtn;
      const origText  = activeBtn.textContent;
      [splitBtn, allBtn].forEach(b => b && (b.disabled = true));
      activeBtn.textContent = 'Uploading…';
      result.textContent = '';
      try {{
        const res = await fetch('/upload-crm-audience', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{split: split}})
        }});
        const data = await res.json();
        if (res.ok) {{
          result.style.color = 'var(--green)';
          if (split) {{
            const good = data.good_leads_count || 0;
            const bad  = data.bad_leads_count  || 0;
            const lal  = data.good_leads_lookalike_id ? '✓ similar-buyers audience ready' : (data.lookalike_error || '');
            const exc  = data.bad_leads_audience_id  ? '✓ excluded cold leads' : (data.bad_leads_note || data.bad_leads_error || '');
            result.innerHTML = (
              `<strong>Done</strong> · ${{data.total_leads}} leads processed<br>` +
              `Promising buyers: ${{good}} · ${{lal}}<br>` +
              `Cold leads: ${{bad}} · ${{exc}}`
            );
          }} else {{
            result.textContent = `✓ ${{data.leads_uploaded}} leads synced · similar-buyers audience ${{data.lookalike_audience_id ? 'ready' : 'n/a'}}`;
          }}
          [splitBtn, allBtn].forEach(b => b && (b.disabled = false));
          if (split) splitBtn.textContent = 'Find similar buyers';
          else allBtn.textContent = 'All leads';
        }} else {{
          [splitBtn, allBtn].forEach(b => b && (b.disabled = false));
          activeBtn.textContent = origText;
          result.style.color = 'var(--danger)';
          result.textContent = 'Error: ' + (data.detail || 'Unknown error');
        }}
      }} catch(e) {{
        [splitBtn, allBtn].forEach(b => b && (b.disabled = false));
        activeBtn.textContent = origText;
        result.style.color = 'var(--danger)';
        result.textContent = 'Request failed: ' + e.message;
      }}
    }}

    // Auto-update status badges for running runs without full page reload
    (function() {{
      const active = {active_run_ids};
      if (!active.length) return;
      function badge(s) {{
        const cls = {{complete:'badge-ok',failed:'badge-danger',running_stage1:'badge-warn',
          running_stage2:'badge-warn',running_stage3:'badge-warn',queued:'badge-muted'}};
        const labels = {{complete:'Ready',failed:'Failed',running_stage1:'Researching',
          running_stage2:'Writing',running_stage3:'Polishing',queued:'Starting'}};
        return `<span class="badge ${{cls[s]||'badge-muted'}}">${{labels[s]||s}}</span>`;
      }}
      const poll = setInterval(async () => {{
        try {{
          const rows = await fetch('/runs/json').then(r => r.json());
          let needReload = false;
          rows.forEach(([id, run]) => {{
            if (!active.includes(id)) return;
            const cell = document.getElementById('status-' + id);
            if (cell) cell.innerHTML = badge(run.status);
            if (run.status === 'complete' || run.status === 'failed') needReload = true;
          }});
          if (needReload) {{ clearInterval(poll); window.location.reload(); }}
        }} catch(e) {{}}
      }}, 4000);
    }})();
  </script>
</body></html>"""
    return HTMLResponse(content=html)
