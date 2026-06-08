"""
Loads brand_voice.md from project_context/. Returns empty string with a warning
if the file hasn't been filled in yet, so the pipeline degrades gracefully
rather than crashing during development.
"""
import pathlib
import logging

logger = logging.getLogger(__name__)

_PLACEHOLDER_MARKER = "STATUS: PLACEHOLDER"


def load_brand_voice() -> str:
    path = (
        pathlib.Path(__file__).parent.parent.parent.parent
        / "project_context"
        / "brand_voice.md"
    )
    if not path.exists():
        logger.warning("brand_voice.md not found at %s — agents will run without brand context", path)
        return ""

    text = path.read_text(encoding="utf-8")

    if _PLACEHOLDER_MARKER in text:
        logger.warning("brand_voice.md is still a placeholder — fill it in after the client session before running production campaigns")

    return text
