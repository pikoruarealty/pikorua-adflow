"""
Saves crew outputs to a timestamped folder under outputs/pending_review/.
Each run gets its own folder so nothing is overwritten.
"""
import json
import pathlib
from datetime import datetime


def get_review_folder() -> pathlib.Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder = (
        pathlib.Path(__file__).parent.parent.parent.parent
        / "outputs"
        / "pending_review"
        / timestamp
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_for_review(content_result, audience_result=None) -> pathlib.Path:
    folder = get_review_folder()
    outputs_root = pathlib.Path(__file__).parent.parent.parent.parent / "outputs"

    if audience_result is not None:
        (folder / "persona.md").write_text(str(audience_result), encoding="utf-8")

    # Extract per-task outputs from CrewAI result if available
    ad_copy_sections = []
    banner_prompts = ""
    render_prompts = ""
    AD_COPY_TASKS = {"write_meta_ads", "write_google_ads", "write_whatsapp_script", "write_email", "format_for_api"}
    BANNER_TASK = "generate_banner_prompts"
    RENDER_TASK = "generate_render_prompts"

    BANNER_DESC_HINTS = ("ideogram", "banner prompt", "generate_banner")
    RENDER_DESC_HINTS = ("flux", "render prompt", "generate_render", "exterior render")
    AD_COPY_DESC_HINTS = ("write_meta", "write_google", "write_whatsapp", "write_email", "format_for_api",
                          "meta ad copy", "google search ad", "whatsapp business", "email for")

    tasks_output = getattr(content_result, "tasks_output", None)
    if tasks_output:
        for task_out in tasks_output:
            name = (getattr(task_out, "name", "") or "").lower()
            desc = (getattr(task_out, "description", "") or "").lower()
            raw = getattr(task_out, "raw", "") or str(task_out)
            tag = name or desc

            if name in AD_COPY_TASKS or any(h in tag for h in AD_COPY_DESC_HINTS):
                label = (name or "ad copy").replace("_", " ").title()
                ad_copy_sections.append(f"## {label}\n\n{raw}")
            elif name == BANNER_TASK or any(h in tag for h in BANNER_DESC_HINTS):
                banner_prompts = raw
            elif name == RENDER_TASK or any(h in tag for h in RENDER_DESC_HINTS):
                render_prompts = raw

    if ad_copy_sections:
        (folder / "ad_copy.md").write_text("\n\n---\n\n".join(ad_copy_sections), encoding="utf-8")
    else:
        # Fallback: write the raw crew result (last task output)
        (folder / "ad_copy.md").write_text(str(content_result), encoding="utf-8")

    if banner_prompts or render_prompts:
        combined = "\n\n".join(filter(None, [banner_prompts, render_prompts]))
        (folder / "visual_brief.md").write_text(combined, encoding="utf-8")
        (outputs_root / "visual_brief.md").write_text(combined, encoding="utf-8")

    # Copy evaluator + CRM outputs into the review folder if written this run
    for filename in ("targeting_brief.md", "copy_scorecard.md", "copy_rewrites.md",
                     "crm_insights.md", "crm_signal.md"):
        src = outputs_root / filename
        if src.exists():
            (folder / filename).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    # Fallback: combine from output files if task-level capture missed them
    if not (banner_prompts or render_prompts):
        banner_src = outputs_root / "visual_brief.md"
        render_src = outputs_root / "render_prompts.md"
        parts = []
        if banner_src.exists():
            parts.append(banner_src.read_text(encoding="utf-8"))
        if render_src.exists():
            parts.append(render_src.read_text(encoding="utf-8"))
        if parts:
            combined = "\n\n".join(parts)
            (folder / "visual_brief.md").write_text(combined, encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  REVIEW REQUIRED")
    print(f"  Outputs saved to: {folder}")
    print(f"  Review all files before any deployment step.")
    print(f"{'='*60}\n")

    return folder


def save_json(data: dict, filename: str, folder: pathlib.Path) -> pathlib.Path:
    out = folder / filename
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
