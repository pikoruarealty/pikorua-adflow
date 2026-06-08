"""
FastAPI portal endpoint — Task 1.6.
Allows Pikorua team to launch campaigns without terminal access.
"""
# TODO (Task 1.6): implement POST /launch-campaign endpoint
# Accepts: property_name, platform, goal, budget_inr, city, property_type, price_cr
# Returns: {status, run_id, review_folder}
# Do NOT add auth or user management — this is an internal tool only.

from fastapi import FastAPI

app = FastAPI(title="Pikorua Campaign Portal")


@app.get("/health")
def health():
    return {"status": "ok"}


# @app.post("/launch-campaign")
# def launch_campaign(brief: CampaignBrief):
#     ...  # implement in Task 1.6
