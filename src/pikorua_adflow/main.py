import sys
import time
from datetime import date
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()
import litellm
litellm.drop_params = True

from pikorua_adflow.crews.content_crew.content_crew import ContentCrew
from pikorua_adflow.crews.audience_crew.audience_crew import AudienceCrew
from pikorua_adflow.utils.output_saver import save_for_review

# Campaign brief — edit these values for a CLI run, or use the portal at /portal
inputs = {
    "platform": "Meta Ads",
    "product": "Pikorua — Luxury Real Estate Consultancy. Property: Oberoi Sky Heights, a sea-view apartment in Mumbai, Bandra West at ₹4.5 Cr.",
    "target_audience": "HNI/NRI buyers seeking premium sea-view apartment in Mumbai. Campaign goal: Lead Generation. Budget: ₹50,000. Duration: 30 days.",
    "property_type": "sea-view apartment",
    "city": "Mumbai",
    "locality": "Bandra West",
    "price_cr": "4.5",
    "goal": "Lead Generation",
    "buyer_type": "HNI/NRI",
    "nri_geographies": "UAE, US, UK",
    "campaign_duration_days": "30",
    # Fallback values — overwritten by audience crew output at runtime
    "persona": "No persona data — audience crew has not run.",
    "trends": "No trend data — audience crew has not run.",
    "targeting": "No targeting data — audience crew has not run.",
    "today": date.today().strftime("%B %d, %Y"),
}


def run():
    start = time.time()

    # Stage 1 — Audience intelligence (persona, competitors, trends)
    print("\n[Stage 1] Running audience intelligence crew...")
    audience_output = None
    try:
        audience_result = AudienceCrew().crew().kickoff(inputs=inputs)
        audience_output = str(audience_result)
        inputs["persona"] = audience_output
        inputs["trends"] = "See persona output above for extracted trend hooks."
        targeting_path = Path(__file__).parent.parent.parent.parent / "outputs" / "targeting_brief.md"
        if targeting_path.exists():
            inputs["targeting"] = targeting_path.read_text(encoding="utf-8")
        else:
            inputs["targeting"] = audience_output
        print("[Stage 1] Complete.")
    except Exception as e:
        print(f"[Stage 1] WARNING: Audience crew failed ({e}). Continuing with default context.")

    # Stage 2 — Content generation
    print("\n[Stage 2] Running content crew...")
    content_result = ContentCrew().crew().kickoff(inputs=inputs)
    print("[Stage 2] Complete.")

    # Human review checkpoint — pipeline stops here (Task 1.4)
    save_for_review(content_result, audience_result=audience_output)

    elapsed = time.time() - start
    print(f"Total runtime: {elapsed:.1f}s")


def kickoff():
    run()


def plot():
    ContentCrew().crew().plot()


def run_with_trigger():
    run()


if __name__ == "__main__":
    run()
