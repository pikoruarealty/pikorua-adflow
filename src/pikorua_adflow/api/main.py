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

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(
    title="Pikorua Campaign Portal",
    description="Internal tool — launches AI-generated ad campaigns for Pikorua Realty team.",
    version="1.0.0",
)

# In-memory run registry — good enough for an internal single-user tool
_runs: dict[str, dict] = {}


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

    sys.stdout.reconfigure(encoding="utf-8")

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
    except Exception as exc:
        _runs[run_id]["status"] = "failed"
        _runs[run_id]["error"] = str(exc)


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

    run_rows = ""
    for run_id, run in rows:
        brief = run.get("brief", {})
        scorecard = run.get("copy_scorecard_summary", "")
        scorecard_html = f'<div style="font-size:0.75rem;color:#5a5040;margin-top:4px;">{scorecard}</div>' if scorecard else ""
        folder = run.get("review_folder", "") or ""
        folder_html = f'<div style="font-family:monospace;font-size:0.72rem;color:#8a7d6e;margin-top:2px;">{folder}</div>' if folder else ""
        run_rows += f"""
        <tr>
          <td style="padding:10px 12px;font-family:monospace;font-size:0.82rem;">{run_id}</td>
          <td style="padding:10px 12px;font-size:0.85rem;">
            {brief.get('property_name','—')}<br>
            <span style="font-size:0.75rem;color:#8a7d6e;">{brief.get('city','')} · ₹{brief.get('price_cr','')} Cr · {brief.get('platform','')}</span>
          </td>
          <td style="padding:10px 12px;">{status_badge(run.get('status',''))}</td>
          <td style="padding:10px 12px;font-size:0.82rem;color:#5a5040;">
            {run.get('created_at','')[:16].replace('T',' ')}
          </td>
          <td style="padding:10px 12px;">
            {scorecard_html}
            {folder_html}
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
  <h1>Campaign Runs <span style="font-size:0.85rem;color:#8a7d6e;">— this session only</span></h1>
  <table>
    <thead><tr>
      <th>Run ID</th><th>Property</th><th>Status</th><th>Started</th><th>Scorecard / Output</th>
    </tr></thead>
    <tbody>{run_rows if run_rows else '<tr><td colspan="5" style="padding:16px;color:#8a7d6e;">No runs yet this session.</td></tr>'}</tbody>
  </table>
  <p style="margin-top:1rem;font-size:0.8rem;color:#8a7d6e;">
    <a href="/portal">&#8592; Launch new campaign</a>
  </p>
</body></html>"""
    return HTMLResponse(content=html)
