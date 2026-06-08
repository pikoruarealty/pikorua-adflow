import sys
import time
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()
import litellm
litellm.drop_params = True

from pikorua_adflow.crews.content_crew.content_crew import ContentCrew
from pikorua_adflow.crews.audience_crew.audience_crew import AudienceCrew
from pikorua_adflow.utils.output_saver import save_for_review

# Campaign brief — will be replaced by FastAPI input in Task 1.6
inputs = {
    "platform": "Meta Ads",
    "product": "Pikorua — Luxury Real Estate Consultancy offering curated high-end residential and commercial properties across prime locations in India",
    "target_audience": "HNIs and NRIs aged 35-60, net worth 5Cr+, interested in premium apartments, villas, and investment-grade commercial spaces",
    "property_type": "sea-view apartment",
    "city": "Mumbai",
    "price_cr": "4.5",
    # Fallback values — overwritten by audience crew output at runtime
    "persona": "No persona data — audience crew has not run.",
    "trends": "No trend data — audience crew has not run.",
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
