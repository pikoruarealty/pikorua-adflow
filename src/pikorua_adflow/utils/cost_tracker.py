"""
Tracks approximate LLM token usage and cost per pipeline run.
Populated in Phase 5 (Task 5.4) once real per-campaign cost data exists.
"""
# TODO (Task 5.4): wire in litellm usage callbacks to capture actual token counts
# and compute cost per model at runtime.

def log_run_cost(run_id: str, token_usage: dict) -> None:
    """Placeholder — implement in Task 5.4."""
    pass
