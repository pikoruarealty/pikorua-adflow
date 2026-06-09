"""
Loads project context files from project_context/ and returns them as a combined
string for injection into agent backstories. Degrades gracefully if files are missing
or still placeholders — pipeline warns but does not crash.
"""
import pathlib
import logging

logger = logging.getLogger(__name__)

_PROJECT_CONTEXT = pathlib.Path(__file__).parent.parent.parent.parent / "project_context"

_PLACEHOLDER_MARKER = "awaiting client working session"

_FILES = [
    ("brand_voice.md", "BRAND VOICE"),
    ("pikorua_overview.md", "COMPANY OVERVIEW"),
    ("campaign_examples.md", "CAMPAIGN EXAMPLES (few-shot)"),
    ("data_audit.md", "META ADS PERFORMANCE BENCHMARKS & COMPETITOR ANALYSIS"),
]


def load_brand_voice() -> str:
    """Returns combined project context for injection into the copywriter agent backstory."""
    sections = []

    for filename, label in _FILES:
        path = _PROJECT_CONTEXT / filename
        if not path.exists():
            logger.warning("%s not found — agents will run without this context", filename)
            continue

        text = path.read_text(encoding="utf-8")

        if _PLACEHOLDER_MARKER in text:
            logger.warning(
                "%s is still a placeholder — fill it in after the client session before running production campaigns",
                filename,
            )

        sections.append(f"=== {label} ===\n{text}")

    return "\n\n".join(sections)