"""
Thin wrapper around campaign_memory.py for use in crew tasks.
Agents don't call this directly — it's invoked by the pipeline after approval.
"""
import pathlib
from typing import Optional

from pikorua_adflow.utils.campaign_memory import (
    format_as_fewshot,
    retrieve_similar_campaigns,
    store_approved_campaign,
)


def approve_and_store(
    run_id: str,
    brief: dict,
    review_folder: pathlib.Path,
    scorecard_summary: Optional[str] = None,
) -> str:
    """Store an approved campaign in Qdrant. Returns a status message."""
    try:
        success = store_approved_campaign(run_id, brief, review_folder, scorecard_summary)
    except RuntimeError as exc:
        return f"Campaign {run_id} approved but vector memory skipped: {exc}"
    except Exception as exc:
        return f"Campaign {run_id} approved but vector memory failed unexpectedly: {exc}"
    if success:
        return f"Campaign {run_id} stored in vector memory."
    return f"Campaign {run_id} could not be stored — ad_copy.md not found in {review_folder}."


def get_fewshot_context(brief: dict) -> str:
    """
    Retrieve similar approved campaigns and format as few-shot prompt context.
    Returns empty string if vector store has no records yet.
    """
    campaigns = retrieve_similar_campaigns(
        property_name=brief.get("property_name", ""),
        property_type=brief.get("property_type", ""),
        city=brief.get("city", ""),
        buyer_type=brief.get("buyer_type", "HNI/NRI"),
        goal=brief.get("goal", "Lead Generation"),
    )
    return format_as_fewshot(campaigns)
