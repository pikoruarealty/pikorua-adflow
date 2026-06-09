"""
FastAPI portal endpoint — Task 1.6.
Allows Pikorua team to launch campaigns without terminal access.

Run with:
    uvicorn pikorua_adflow.api.main:app --reload --port 8000

Then open: http://localhost:8000  (redirects to portal form)
Or POST directly to: http://localhost:8000/launch-campaign
"""

import sys
import uuid
import threading
from pathlib import Path
from datetime import datetime, date

# Must set up dotenv and litellm before any crew imports
from dotenv import load_dotenv
load_dotenv()
import litellm
litellm.drop_params = True
litellm.num_retries = 6          # retry up to 6x on 429/5xx
litellm.request_timeout = 120    # 2 min per request before timeout

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
import json
from fastapi.staticfiles import StaticFiles
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
    buyer_type: str = Field("HNI/NRI", description="Target buyer segment: 'HNI', 'NRI', or 'HNI/NRI'")
    nri_geographies: str = Field("", description="NRI diaspora locations if relevant, e.g. 'UAE, US, UK'")
    campaign_duration_days: int = Field(30, gt=0, description="Campaign flight duration in days")


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

    # Run CRM analysis before crew kickoff — graceful if file missing
    crm_insights = crm_analyse()

    locality_str = f", {brief.locality}" if brief.locality else ""
    nri_str = f" NRI target geographies: {brief.nri_geographies}." if brief.nri_geographies else ""
    inputs = {
        "platform": brief.platform,
        "product": (
            f"Pikorua — Luxury Real Estate Consultancy. Property: {brief.property_name}, "
            f"a {brief.property_type} in {brief.city}{locality_str} at ₹{brief.price_cr} Cr."
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
        "persona": "No persona data — audience crew has not run yet.",
        "trends": "No trend data — audience crew has not run yet.",
        "targeting": "No targeting data — audience crew has not run yet.",
        "crm_insights": crm_insights,
        "today": date.today().strftime("%B %d, %Y"),
    }

    _runs[run_id]["status"] = "running_stage1"

    audience_output = None
    try:
        audience_result = AudienceCrew().crew().kickoff(inputs=inputs)
        audience_output = str(audience_result)
        inputs["persona"] = audience_output
        inputs["trends"] = "See persona output above for extracted trend hooks."
        # Load targeting brief from file if the agent wrote it; fall back to crew output
        targeting_path = Path(__file__).parent.parent.parent.parent / "outputs" / "targeting_brief.md"
        if targeting_path.exists():
            inputs["targeting"] = targeting_path.read_text(encoding="utf-8")
        else:
            inputs["targeting"] = audience_output
        import time; time.sleep(8)  # brief pause to let RPM window reset before Stage 2 burst
        _runs[run_id]["status"] = "running_stage2"
    except Exception as exc:
        _runs[run_id]["stage1_warning"] = str(exc)
        _runs[run_id]["status"] = "running_stage2"

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


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


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
        "created_at": datetime.utcnow().isoformat(),
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
    ad_copy_text    = read("ad_copy.md")
    persona_text    = read("persona.md")
    targeting_text  = read("targeting_brief.md")
    visual_text     = read("visual_brief.md")

    variants = _parse_scorecard(scorecard_text)
    _merge_rewrites(variants, rewrites_text)

    # Parse ad copy sections from ad_copy.md
    ad_sections = _parse_ad_copy(ad_copy_text)

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

    # Build variant cards HTML
    variant_cards_html = ""
    for v in variants:
        num = v.get("variant", "?")
        angle = v.get("angle", "")
        status = v.get("status", "PASS")
        scores = v.get("scores", {})
        flag_reason = v.get("flag_reason", "")
        rewrite = v.get("rewrite")

        # Original copy from ad_copy sections
        orig = ad_sections.get("meta", {}).get(num, {})
        headline = orig.get("headline") or v.get("headline", "")
        body = orig.get("body") or v.get("body", "")

        status_colour = "#c0392b" if status == "FLAG" else "#2a7a4a"
        status_bg     = "#fdf3f2" if status == "FLAG" else "#f2faf5"
        card_border   = "#e8c4c0" if status == "FLAG" else "#c8e6d4"

        score_bars = ""
        avg_score = None
        if scores:
            avg_score = round(sum(scores.values()) / len(scores), 1)
            dim_labels = {"brand_voice": "Brand Voice", "platform_fit": "Platform Fit",
                          "specificity": "Specificity", "luxury_signal": "Luxury Signal"}
            for key, label in dim_labels.items():
                val = scores.get(key, 0)
                bar_w = val * 10
                bar_colour = "#2a7a4a" if val >= 7 else ("#e67e22" if val >= 5 else "#c0392b")
                score_bars += f"""
                <div style="margin-bottom:6px;">
                  <div style="display:flex;justify-content:space-between;font-size:0.72rem;color:#5a5040;margin-bottom:2px;">
                    <span>{label}</span><span style="color:{bar_colour};font-weight:bold;">{val}/10</span>
                  </div>
                  <div style="background:#e8e4dc;border-radius:2px;height:5px;">
                    <div style="background:{bar_colour};width:{bar_w}%;height:5px;border-radius:2px;"></div>
                  </div>
                </div>"""

        avg_html = f'<span style="font-size:1.1rem;font-weight:bold;color:#1a1a1a;">{avg_score}/10</span>' if avg_score else ""

        flag_html = ""
        if status == "FLAG":
            flag_html = f'<div style="background:#fdf3f2;border-left:3px solid #c0392b;padding:8px 12px;margin:10px 0;font-size:0.8rem;color:#7a2020;"><strong>FLAG</strong> &mdash; {_esc(flag_reason)}</div>'

        rewrite_html = ""
        if rewrite:
            rh = rewrite.get("headline", "")
            rb = rewrite.get("body", "")
            rewrite_html = f"""
            <div style="margin-top:12px;padding:12px;background:#f2faf5;border-left:3px solid #2a7a4a;border-radius:0 3px 3px 0;">
              <div style="font-size:0.7rem;letter-spacing:0.1em;text-transform:uppercase;color:#2a7a4a;margin-bottom:6px;">Rewritten</div>
              {f'<div style="font-size:0.9rem;font-weight:bold;color:#1a1a1a;margin-bottom:4px;">{_esc(rh)}</div>' if rh else ""}
              {f'<div style="font-size:0.85rem;color:#3a3028;line-height:1.5;">{_esc(rb)}</div>' if rb else ""}
            </div>"""

        copy_html = ""
        if headline or body:
            copy_html = f"""
            <div style="margin-bottom:10px;">
              {f'<div style="font-size:0.7rem;letter-spacing:0.1em;text-transform:uppercase;color:#8a7d6e;margin-bottom:4px;">Headline</div><div style="font-size:0.95rem;font-weight:bold;color:#1a1a1a;margin-bottom:8px;">{_esc(headline)}</div>' if headline else ""}
              {f'<div style="font-size:0.7rem;letter-spacing:0.1em;text-transform:uppercase;color:#8a7d6e;margin-bottom:4px;">Body</div><div style="font-size:0.85rem;color:#3a3028;line-height:1.6;">{_esc(body)}</div>' if body else ""}
            </div>"""

        copy_btn = f"""<button onclick="copyFromData(this)" data-copy="{_esc(headline)} — {_esc(body)}"
          style="background:none;border:1px solid #e0dbd0;padding:3px 10px;font-size:0.72rem;
          color:#8a7d6e;cursor:pointer;border-radius:2px;margin-top:6px;">Copy</button>"""

        variant_cards_html += f"""
        <div style="background:#fff;border:1px solid {card_border};border-radius:4px;padding:20px;margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
            <div>
              <span style="font-size:0.7rem;letter-spacing:0.12em;text-transform:uppercase;color:#8a7d6e;">Variant {num}</span>
              <div style="font-size:1rem;color:#1a1a1a;margin-top:2px;">{_esc(angle)}</div>
            </div>
            <div style="text-align:right;">
              <span style="background:{status_bg};color:{status_colour};padding:2px 10px;border-radius:2px;font-size:0.72rem;letter-spacing:0.06em;">{status}</span>
              <div style="margin-top:4px;">{avg_html}</div>
            </div>
          </div>
          {copy_html}
          {flag_html}
          {rewrite_html}
          {copy_btn}
          <div style="margin-top:14px;padding-top:12px;border-top:1px solid #f0ede6;">
            {score_bars}
          </div>
        </div>"""

    # Other copy sections (Google, WhatsApp, Email)
    other_copy_html = ""
    section_labels = [
        ("google", "Google Ads"),
        ("whatsapp", "WhatsApp Script"),
        ("email", "Email"),
    ]
    for key, label in section_labels:
        text = ad_sections.get(key, "")
        if text:
            other_copy_html += f"""
            <div style="margin-bottom:24px;">
              <h3 style="font-size:0.78rem;letter-spacing:0.12em;text-transform:uppercase;
                color:#5a5040;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e0dbd0;">{label}</h3>
              <pre style="white-space:pre-wrap;font-family:'Georgia',serif;font-size:0.85rem;
                color:#3a3028;line-height:1.7;background:#fdfcf9;padding:14px;
                border:1px solid #e0dbd0;border-radius:3px;">{_esc(text.strip())}</pre>
            </div>"""

    # Persona section
    persona_html = ""
    if persona_text:
        persona_html = f"""<pre style="white-space:pre-wrap;font-family:'Georgia',serif;font-size:0.84rem;
          color:#3a3028;line-height:1.7;background:#fdfcf9;padding:14px;
          border:1px solid #e0dbd0;border-radius:3px;">{_esc(persona_text.strip())}</pre>"""

    # Targeting brief section
    targeting_html = ""
    if targeting_text:
        targeting_html = f"""<pre style="white-space:pre-wrap;font-family:'Georgia',serif;font-size:0.82rem;
          color:#3a3028;line-height:1.7;background:#fdfcf9;padding:14px;
          border:1px solid #e0dbd0;border-radius:3px;">{_esc(targeting_text.strip())}</pre>"""

    scorecard_summary = run.get("copy_scorecard_summary", "")
    folder_short = str(review_folder).split("pending_review")[-1].lstrip("/\\")

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>Pikorua — Run {run_id}</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
    body{{font-family:'Georgia',serif;background:#f7f5f0;color:#1a1a1a;padding:2rem;}}
    .logo{{font-size:0.72rem;letter-spacing:0.2em;text-transform:uppercase;color:#8a7d6e;}}
    h1{{font-size:1.4rem;font-weight:normal;margin:0.3rem 0 0.2rem;}}
    h2{{font-size:0.85rem;letter-spacing:0.14em;text-transform:uppercase;color:#5a5040;
        margin:2rem 0 1rem;padding-bottom:6px;border-bottom:2px solid #e0dbd0;}}
    .meta-row{{font-size:0.8rem;color:#8a7d6e;margin-bottom:1.5rem;}}
    .tab-bar{{display:flex;gap:4px;margin-bottom:1.5rem;flex-wrap:wrap;}}
    .tab{{padding:6px 16px;border:1px solid #e0dbd0;background:#fff;color:#5a5040;
          font-size:0.78rem;cursor:pointer;border-radius:2px;font-family:'Georgia',serif;}}
    .tab.active{{background:#1a1a1a;color:#f7f5f0;border-color:#1a1a1a;}}
    .panel{{display:none;}}.panel.active{{display:block;}}
    .copy-notice{{position:fixed;bottom:1.5rem;right:1.5rem;background:#1a1a1a;color:#f7f5f0;
      padding:8px 16px;border-radius:3px;font-size:0.8rem;opacity:0;transition:opacity 0.3s;pointer-events:none;}}
    a{{color:#3a3028;}}
    @media(max-width:700px){{body{{padding:1rem;}}}}
  </style>
</head>
<body>
  <div class="logo">Pikorua Realty</div>
  <h1>Run {run_id}</h1>
  <div class="meta-row">
    {_esc(brief.get('property_name',''))} &nbsp;·&nbsp;
    {_esc(brief.get('city',''))} &nbsp;·&nbsp;
    ₹{_esc(str(brief.get('price_cr','')))} Cr &nbsp;·&nbsp;
    {_esc(brief.get('platform',''))} &nbsp;·&nbsp;
    {_esc(brief.get('property_type',''))}
    {f'&nbsp;·&nbsp;<strong style="color:#2a7a4a;">{_esc(scorecard_summary)}</strong>' if scorecard_summary else ""}
    <br><span style="font-size:0.73rem;color:#b0a898;">outputs/pending_review/{_esc(folder_short)}</span>
  </div>

  <div class="tab-bar">
    <button class="tab active" onclick="showTab('meta')">Meta Ads</button>
    <button class="tab" onclick="showTab('other')">Google · WhatsApp · Email</button>
    <button class="tab" onclick="showTab('visuals')">Image Prompts</button>
    <button class="tab" onclick="showTab('audience')">Audience &amp; Targeting</button>
  </div>

  <div id="tab-meta" class="panel active">
    <h2>Meta Ad Variants — Scorecard</h2>
    {variant_cards_html if variant_cards_html else '<p style="color:#8a7d6e;font-size:0.85rem;">No scorecard data found.</p>'}
  </div>

  <div id="tab-other" class="panel">
    <h2>Other Channels</h2>
    {other_copy_html if other_copy_html else '<p style="color:#8a7d6e;font-size:0.85rem;">No copy data found.</p>'}
  </div>

  <div id="tab-visuals" class="panel">
    <h2>Image Generation</h2>
    <p style="font-size:0.8rem;color:#8a7d6e;margin-bottom:1rem;">
      Prompts 1–3: Ideogram 3 (social banner, text on image) &nbsp;·&nbsp;
      Prompts 4–5: Flux 2 Pro (photorealistic render, no text)
    </p>
    {_build_visuals_html(run_id, image_prompts, existing_images, ideogram_key, replicate_token, together_key)}
  </div>

  <div id="tab-audience" class="panel">
    <h2>Buyer Persona</h2>
    {persona_html if persona_html else '<p style="color:#8a7d6e;font-size:0.85rem;">No persona data found.</p>'}
    <h2>Targeting Brief</h2>
    {targeting_html if targeting_html else '<p style="color:#8a7d6e;font-size:0.85rem;">No targeting brief found.</p>'}
  </div>

  <p style="margin-top:2rem;font-size:0.8rem;color:#8a7d6e;">
    <a href="/runs">&#8592; All runs</a>
  </p>
  <div class="copy-notice" id="copy-notice">Copied</div>

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
    async function generateImages(runId) {{
      const btn = document.getElementById('gen-btn');
      const status = document.getElementById('gen-status');
      btn.disabled = true;
      btn.textContent = 'Generating… (this may take 1–2 minutes)';
      status.textContent = '';
      try {{
        const res = await fetch('/generate-images/' + runId, {{method: 'POST'}});
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
            btn.textContent = 'Generate Images';
            const errDetails = data.errors.map(e => 'Prompt ' + e.prompt + ': ' + e.error).join(' | ');
            status.textContent = 'No images generated.' + (errDetails ? ' ' + errDetails : '');
          }}
        }} else {{
          btn.disabled = false;
          btn.textContent = 'Generate Images';
          status.textContent = 'Error: ' + (data.detail || 'Unknown error');
        }}
      }} catch(e) {{
        btn.disabled = false;
        btn.textContent = 'Generate Images';
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


def _parse_ad_copy(text: str) -> dict:
    """
    Parse ad_copy.md into sections keyed by channel.
    Returns dict with keys: meta (dict of variant_num -> {headline, body}),
    google, whatsapp, email (strings).
    """
    import re
    result = {"meta": {}, "google": "", "whatsapp": "", "email": ""}
    if not text:
        return result

    # Split into h2 sections
    sections = re.split(r'\n## ', "\n" + text)
    for section in sections:
        if not section.strip():
            continue
        lines = section.strip().splitlines()
        header = lines[0].strip().lower()
        body = "\n".join(lines[1:]).strip()

        if "meta" in header or "write meta" in header:
            # Parse individual variants. The copywriter is inconsistent about marker
            # placement — handle both "**1. Angle: X**" and "1. **Angle: X**".
            variant_blocks = re.split(r'\n(?=\*{0,2}\d+\.)', body)
            for block in variant_blocks:
                block = block.strip()
                nm = re.match(r'\*{0,2}(\d+)\.', block)
                if not nm:
                    continue
                num = int(nm.group(1))
                hm = re.search(r'Headline:\s*(.+?)(?:\s*\[\d+\s*chars\])?\s*$', block, re.MULTILINE | re.IGNORECASE)
                bm = re.search(r'Body:\s*(.+?)(?:\s*\[\d+\s*chars\])?\s*$', block, re.MULTILINE | re.IGNORECASE)
                result["meta"][num] = {
                    "headline": hm.group(1).strip() if hm else "",
                    "body": bm.group(1).strip() if bm else "",
                }
        elif "google" in header:
            result["google"] = body
        elif "whatsapp" in header:
            result["whatsapp"] = body
        elif "email" in header:
            result["email"] = body

    return result


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
        # Extract the quoted prompt text
        qm = re.search(r'"([^"]{40,})"', block, re.DOTALL)
        if qm:
            prompts.append((title, qm.group(1)))
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

        # Scores
        for dim, key in [
            ("Brand Voice", "brand_voice"), ("Platform Fit", "platform_fit"),
            ("Specificity", "specificity"), ("Luxury Signal", "luxury_signal")
        ]:
            sm = re.search(rf'{dim}[:\s]+(\d+)/10', block, re.IGNORECASE)
            if sm:
                v["scores"][key] = int(sm.group(1))

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
        hm = re.search(r'Headline:\s*(.+)', block)
        bm = re.search(r'Body:\s*(.+)', block)
        for v in variants:
            if v["variant"] == num:
                if hm or bm:
                    v["rewrite"] = {
                        "headline": hm.group(1).strip() if hm else "",
                        "body": bm.group(1).strip() if bm else "",
                    }


def _build_visuals_html(run_id: str, image_prompts: list, existing_images: list,
                        ideogram_key: str, replicate_token: str, together_key: str = "") -> str:
    """Build the full HTML for the visuals tab — images, generate button, prompts."""
    import os
    html = ""

    # Show already-generated images
    if existing_images:
        html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;margin-bottom:20px;">'
        for i, fname in enumerate(existing_images, 1):
            title = image_prompts[i - 1][0] if i <= len(image_prompts) else f"Prompt {i}"
            backend, _ = _image_backend(i, ideogram_key, replicate_token, together_key)
            badge_labels = {"ideogram": "Ideogram 3", "replicate": "Flux 2 Pro",
                            "together": "Together FLUX", "pollinations": "Pollinations"}
            badge = badge_labels.get(backend, backend)
            html += f"""
            <div style="background:#fff;border:1px solid #e0dbd0;border-radius:4px;overflow:hidden;">
              <img src="/image/{run_id}/{fname}" alt="{_esc(title)}"
                   style="width:100%;display:block;border-bottom:1px solid #e0dbd0;">
              <div style="padding:8px 12px;display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:0.78rem;color:#5a5040;">{_esc(title)}</span>
                <span style="background:#f0ede6;color:#3a2850;padding:2px 6px;border-radius:2px;font-size:0.68rem;">{badge}</span>
              </div>
            </div>"""
        html += "</div>"

    # Determine active backend for display
    pollinations_token = os.getenv("POLLINATIONS_TOKEN", "")
    if ideogram_key or replicate_token:
        backend_note = "Using Ideogram 3 (prompts 1–3) + Replicate Flux Pro (4–5) — paid"
    elif together_key:
        backend_note = "Using Together AI FLUX.1-schnell — free tier"
    elif pollinations_token:
        backend_note = "Using Pollinations.ai — model: sana — 0.01 pollen/hr (text won't render in banners)"
    else:
        backend_note = ("⚠ No image backend configured. Pollinations' free no-key tier was "
                        "retired (returns 402). Set TOGETHER_API_KEY (free) or POLLINATIONS_TOKEN "
                        "(free from auth.pollinations.ai) in .env and restart.")

    missing = len(image_prompts) - len(existing_images)
    if not existing_images:
        btn_label = "Generate Images"
        btn_style = "background:#1a1a1a;"
    elif missing > 0:
        btn_label = f"Generate Missing ({missing} remaining)"
        btn_style = "background:#3a3028;"
    else:
        btn_label = "Regenerate All"
        btn_style = "background:#5a5040;"

    html += f"""
    <div style="margin-bottom:20px;">
      <button id="gen-btn" onclick="generateImages('{run_id}')"
        style="{btn_style}color:#f7f5f0;border:none;padding:8px 20px;
        font-size:0.82rem;cursor:pointer;border-radius:2px;font-family:'Georgia',serif;">
        {btn_label}
      </button>
      <span id="gen-status" style="margin-left:12px;font-size:0.8rem;color:#8a7d6e;"></span>
      <div style="margin-top:6px;font-size:0.75rem;color:#8a7d6e;">{backend_note}</div>
      <div style="margin-top:4px;font-size:0.72rem;color:#b0a898;">
        Upgrade: set TOGETHER_API_KEY (free) or IDEOGRAM_API_KEY + REPLICATE_API_TOKEN (paid) in .env and restart.
      </div>
    </div>"""

    # Prompt cards below
    if image_prompts:
        html += '<h3 style="font-size:0.75rem;letter-spacing:0.1em;text-transform:uppercase;color:#8a7d6e;margin-bottom:10px;">Prompts</h3>'
        for i, (ptitle, prompt_text) in enumerate(image_prompts, 1):
            tool = "Flux 2 Pro" if i > 3 else "Ideogram 3"
            tool_colour = "#1a3050" if i > 3 else "#3a2850"
            html += f"""
            <div style="background:#fff;border:1px solid #e0dbd0;border-radius:4px;padding:16px;margin-bottom:12px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-size:0.85rem;color:#1a1a1a;">Prompt {i} — {_esc(ptitle)}</span>
                <span style="background:#f0ede6;color:{tool_colour};padding:2px 8px;border-radius:2px;font-size:0.7rem;">{tool}</span>
              </div>
              <div style="font-size:0.8rem;color:#5a5040;line-height:1.6;font-family:monospace;
                background:#f7f5f0;padding:10px;border-radius:3px;">{_esc(prompt_text.strip())}</div>
              <button onclick="copyFromData(this)" data-copy="{_esc(prompt_text.strip())}"
                style="background:none;border:1px solid #e0dbd0;padding:3px 10px;font-size:0.72rem;
                color:#8a7d6e;cursor:pointer;border-radius:2px;margin-top:8px;">Copy prompt</button>
            </div>"""
    else:
        html += '<p style="color:#8a7d6e;font-size:0.85rem;">No image prompts found in visual_brief.md.</p>'

    return html


def _call_pollinations(prompt: str) -> bytes:
    """
    Pollinations.ai — free, no API key needed.
    Returns JPEG bytes. Good for dev; no text-in-image support.
    """
    import os, urllib.request, urllib.parse
    token = os.getenv("POLLINATIONS_TOKEN", "")
    url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
           f"?model=sana&width=1200&height=628&nologo=true")
    if token:
        url += f"&token={urllib.parse.quote(token)}"
    # Pollinations now returns 403 to the default "Python-urllib/3.x" user agent
    # (browser UA gets past that) and 402 Payment Required to anonymous requests —
    # the free no-key tier was retired. A free token from auth.pollinations.ai is
    # required; without one this raises and the UI surfaces the 402.
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "image/*",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def _call_together_flux(prompt: str, key: str) -> bytes:
    """
    Together AI FLUX.1-schnell — free tier (3 months unlimited + $25 credits on signup).
    Better quality than Pollinations; needs a free Together AI account.
    """
    import urllib.request, base64
    payload = json.dumps({
        "model": "black-forest-labs/FLUX.1-schnell-Free",
        "prompt": prompt,
        "width": 1200,
        "height": 628,
        "steps": 4,
        "response_format": "b64_json",
    }).encode()
    req = urllib.request.Request(
        "https://api.together.xyz/v1/images/generations",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    b64 = data["data"][0]["b64_json"]
    return base64.b64decode(b64)


def _call_ideogram(prompt: str, key: str) -> bytes:
    """Ideogram 3 API — paid production option. Best for text-in-image banners."""
    import urllib.request
    payload = json.dumps({
        "image_request": {
            "prompt": prompt,
            "aspect_ratio": "ASPECT_16_9",
            "model": "V_3",
            "magic_prompt_option": "OFF",
        }
    }).encode()
    req = urllib.request.Request(
        "https://api.ideogram.ai/generate",
        data=payload,
        headers={"Api-Key": key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    img_url = data["data"][0]["url"]
    with urllib.request.urlopen(img_url, timeout=60) as img_resp:
        return img_resp.read()


def _call_replicate_flux(prompt: str, token: str) -> bytes:
    """Replicate Flux 2 Pro — paid production option. Best photorealistic renders."""
    import urllib.request, time
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }
    payload = json.dumps({
        "input": {"prompt": prompt, "aspect_ratio": "16:9", "output_format": "png"}
    }).encode()
    req = urllib.request.Request(
        "https://api.replicate.com/v1/models/black-forest-labs/flux-pro/predictions",
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        pred = json.loads(resp.read())

    poll_url = pred.get("urls", {}).get("get") or f"https://api.replicate.com/v1/predictions/{pred['id']}"
    for _ in range(60):
        if pred.get("status") in ("succeeded", "failed", "canceled"):
            break
        time.sleep(3)
        poll_req = urllib.request.Request(poll_url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(poll_req, timeout=30) as r:
            pred = json.loads(r.read())

    if pred.get("status") != "succeeded":
        raise RuntimeError(f"Replicate prediction failed: {pred.get('error', pred.get('status'))}")

    output = pred["output"]
    img_url = output if isinstance(output, str) else output[0]
    with urllib.request.urlopen(img_url, timeout=60) as img_resp:
        return img_resp.read()


def _image_backend(i: int, ideogram_key: str, replicate_token: str,
                   together_key: str) -> tuple[str, str]:
    """
    Return (backend_name, tier) for prompt index i (1-based).
    Priority: paid keys > Together AI > Pollinations (always available).
    """
    if i <= 3 and ideogram_key:
        return "ideogram", "paid"
    if i > 3 and replicate_token:
        return "replicate", "paid"
    if together_key:
        return "together", "free"
    return "pollinations", "free"


@app.post("/generate-images/{run_id}")
def generate_images(run_id: str):
    """
    Generate images for all 5 prompts in a completed run.
    Backend priority (per prompt):
      1. Paid: Ideogram (1–3) / Replicate (4–5) — if keys set
      2. Free: Together AI FLUX.1-schnell — if TOGETHER_API_KEY set
      3. Free fallback: Pollinations.ai — no key needed, always available
    Images saved to review_folder/images/image_N.png (or .jpg for Pollinations).
    """
    import os
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

    results = []
    errors = []
    for i, (title, prompt_text) in enumerate(image_prompts, 1):
        out_path = images_dir / f"image_{i}.png"
        if out_path.exists():
            results.append({"prompt": i, "status": "already_exists", "file": str(out_path)})
            continue
        backend, tier = _image_backend(i, ideogram_key, replicate_token, together_key)
        try:
            if backend == "ideogram":
                img_bytes = _call_ideogram(prompt_text, ideogram_key)
            elif backend == "replicate":
                img_bytes = _call_replicate_flux(prompt_text, replicate_token)
            elif backend == "together":
                img_bytes = _call_together_flux(prompt_text, together_key)
            else:
                img_bytes = _call_pollinations(prompt_text)
            out_path.write_bytes(img_bytes)
            results.append({"prompt": i, "status": "generated", "backend": backend, "file": str(out_path)})
        except Exception as exc:
            errors.append({"prompt": i, "backend": backend, "error": str(exc)})

    return {"run_id": run_id, "generated": results, "errors": errors}


@app.get("/image/{run_id}/{filename}")
def serve_image(run_id: str, filename: str):
    """Serve a generated image file from a run's images/ subfolder."""
    import re
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Run not found.")
    run = _runs[run_id]
    if not run.get("review_folder"):
        raise HTTPException(status_code=404, detail="No review folder for this run.")
    # Safety: only allow image_N.png filenames to prevent path traversal
    if not re.fullmatch(r'image_\d+\.png', filename):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    img_path = Path(run["review_folder"]) / "images" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(content=img_path.read_bytes(), media_type="image/png")


@app.post("/approve/{run_id}")
def approve_run(run_id: str):
    """
    Mark a completed run as approved and store it in Qdrant vector memory.
    Only works for runs with status 'complete' that have a review folder.
    """
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run = _runs[run_id]
    if run["status"] != "complete":
        raise HTTPException(status_code=400, detail="Only completed runs can be approved.")
    if not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="No review folder found for this run.")

    from pikorua_adflow.tools.memory_tool import approve_and_store
    review_folder = Path(run["review_folder"])
    message = approve_and_store(
        run_id=run_id,
        brief=run.get("brief", {}),
        review_folder=review_folder,
        scorecard_summary=run.get("copy_scorecard_summary"),
    )

    _runs[run_id]["approved"] = True
    _save_runs()
    return {"status": "approved", "run_id": run_id, "message": message}


class CRMAudienceRequest(BaseModel):
    min_stage: str = Field("site_visit", description="Minimum funnel stage to include: contacted, site_visit, negotiating, converted")


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

    from pikorua_adflow.tools.meta_audience_tool import upload_crm_lookalike
    result = upload_crm_lookalike(ad_account_id=ad_account_id, min_stage=req.min_stage)

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
        colours = {
            "complete": "#2a4030", "failed": "#5a2820",
            "running_stage1": "#2d5038", "running_stage2": "#2d5038", "queued": "#5a5040",
        }
        bg = {"complete": "#f0f4ee", "failed": "#fdf0ee",
              "running_stage1": "#eef4f0", "running_stage2": "#eef4f0", "queued": "#f0ede6"}
        c = colours.get(s, "#333")
        b = bg.get(s, "#eee")
        label = s.replace("_", " ").title()
        return f'<span style="background:{b};color:{c};padding:2px 8px;border-radius:2px;font-size:0.75rem;">{label}</span>'

    active_run_ids = json.dumps([rid for rid, r in rows if r.get("status") not in ("complete", "failed")])

    run_rows = ""
    for run_id, run in rows:
        brief = run.get("brief", {})
        scorecard = run.get("copy_scorecard_summary", "")
        scorecard_html = f'<div style="font-size:0.75rem;color:#5a5040;margin-top:4px;">{scorecard}</div>' if scorecard else ""
        folder = run.get("review_folder", "") or ""
        folder_html = f'<div style="font-family:monospace;font-size:0.72rem;color:#8a7d6e;margin-top:2px;">{folder}</div>' if folder else ""
        approved = run.get("approved", False)
        approve_cell = ""
        if run.get("status") == "complete":
            if approved:
                approve_cell = '<span style="color:#2a4030;font-size:0.78rem;">✓ Stored in memory</span>'
            else:
                approve_cell = (
                    f'<button onclick="approveRun(\'{run_id}\')" id="approve-{run_id}" '
                    f'style="background:#2a4030;color:#f7f5f0;border:none;padding:4px 12px;'
                    f'font-size:0.78rem;cursor:pointer;border-radius:2px;">Approve</button>'
                )
        run_rows += f"""
        <tr>
          <td style="padding:10px 12px;font-family:monospace;font-size:0.82rem;">{run_id}</td>
          <td style="padding:10px 12px;font-size:0.85rem;">
            {brief.get('property_name','—')}<br>
            <span style="font-size:0.75rem;color:#8a7d6e;">{brief.get('city','')} · ₹{brief.get('price_cr','')} Cr · {brief.get('platform','')}</span>
          </td>
          <td style="padding:10px 12px;" id="status-{run_id}">{status_badge(run.get('status',''))}</td>
          <td style="padding:10px 12px;font-size:0.82rem;color:#5a5040;">
            {run.get('created_at','')[:16].replace('T',' ')}
          </td>
          <td style="padding:10px 12px;">
            {scorecard_html}
            {folder_html}
          </td>
          <td style="padding:10px 12px;">{approve_cell}</td>
          <td style="padding:10px 12px;">
            {'<a href="/results/' + run_id + '" style="font-size:0.8rem;">View →</a>' if run.get("status") == "complete" else ""}
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
  <meta charset="UTF-8"/>
  <title>Pikorua — Campaign Runs</title>
  <style>
    body {{font-family:'Georgia',serif;background:#f7f5f0;color:#1a1a1a;padding:2rem;}}
    .logo {{font-size:0.75rem;letter-spacing:0.2em;text-transform:uppercase;color:#8a7d6e;}}
    h1 {{font-size:1.4rem;font-weight:normal;margin:0.3rem 0 1.5rem;}}
    table {{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e0dbd0;}}
    th {{text-align:left;padding:8px 12px;font-size:0.72rem;letter-spacing:0.08em;
         text-transform:uppercase;color:#5a5040;border-bottom:1px solid #e0dbd0;background:#fdfcf9;}}
    tr:not(:last-child) td {{border-bottom:1px solid #f0ede6;}}
    tr:hover td {{background:#fdfcf9;}}
    a {{color:#3a3028;font-size:0.82rem;}}
  </style>
</head><body>
  <div class="logo">Pikorua Realty</div>
  <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:1.5rem;">
    <h1 style="margin:0;">Campaign Runs <span style="font-size:0.85rem;color:#8a7d6e;">— this session only</span></h1>
    <div>
      <button id="crm-global" onclick="uploadCRMLookalike()"
        style="background:#1a3050;color:#f7f5f0;border:none;padding:5px 14px;font-size:0.78rem;cursor:pointer;border-radius:2px;">
        Upload CRM Lookalike
      </button>
      <div style="font-size:0.68rem;color:#8a7d6e;margin-top:3px;text-align:right;">Phase 3 — requires META_ACCESS_TOKEN</div>
    </div>
  </div>
  <table>
    <thead><tr>
      <th>Run ID</th><th>Property</th><th>Status</th><th>Started</th><th>Scorecard / Output</th><th>Memory</th><th>Detail</th>
    </tr></thead>
    <tbody>{run_rows if run_rows else '<tr><td colspan="5" style="padding:16px;color:#8a7d6e;">No runs yet this session.</td></tr>'}</tbody>
  </table>
  <p style="margin-top:1rem;font-size:0.8rem;color:#8a7d6e;">
    <a href="/portal">&#8592; Launch new campaign</a>
  </p>
  <script>
    async function approveRun(runId) {{
      const btn = document.getElementById('approve-' + runId);
      btn.disabled = true;
      btn.textContent = 'Storing...';
      try {{
        const res = await fetch('/approve/' + runId, {{method: 'POST'}});
        const data = await res.json();
        if (res.ok) {{
          btn.replaceWith(Object.assign(document.createElement('span'), {{
            textContent: '✓ Stored in memory',
            style: 'color:#2a4030;font-size:0.78rem;'
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
    async function uploadCRMLookalike() {{
      const btn = document.getElementById('crm-global');
      btn.disabled = true;
      btn.textContent = 'Uploading...';
      try {{
        const res = await fetch('/upload-crm-audience', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{min_stage: 'site_visit'}})
        }});
        const data = await res.json();
        if (res.ok) {{
          btn.replaceWith(Object.assign(document.createElement('span'), {{
            textContent: `✓ ${{data.leads_uploaded}} leads uploaded`,
            style: 'color:#1a3050;font-size:0.78rem;'
          }}));
        }} else {{
          btn.disabled = false;
          btn.textContent = 'Upload CRM Lookalike';
          alert('CRM upload error: ' + (data.detail || 'Unknown error'));
        }}
      }} catch(e) {{
        btn.disabled = false;
        btn.textContent = 'Upload CRM Lookalike';
        alert('Request failed: ' + e.message);
      }}
    }}

    // Auto-update status badges for running runs without full page reload
    (function() {{
      const active = {active_run_ids};
      if (!active.length) return;
      function badge(s) {{
        const colours = {{complete:'#2a4030',failed:'#5a2820',running_stage1:'#2d5038',running_stage2:'#2d5038',queued:'#5a5040'}};
        const bg = {{complete:'#f0f4ee',failed:'#fdf0ee',running_stage1:'#eef4f0',running_stage2:'#eef4f0',queued:'#f0ede6'}};
        const label = s.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());
        return `<span style="background:${{bg[s]||'#eee'}};color:${{colours[s]||'#333'}};padding:2px 8px;border-radius:2px;font-size:0.75rem;">${{label}}</span>`;
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
