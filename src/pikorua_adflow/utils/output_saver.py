"""
Saves crew outputs to a timestamped folder under outputs/pending_review/.
Each run gets its own folder so nothing is overwritten.
"""
import json
import os
import re
import pathlib
from datetime import datetime


# Mapping from @task method name → variant_key (canonical order).
# Main 5: 4 lifestyle (2 static + 2 dynamic) + 1 interior. Exterior is opt-in 6th.
_VISUAL_TASK_TO_VARIANT = {
    "lifestyle_private_retreat_task": "lifestyle_private_retreat",
    "lifestyle_social_home_task": "lifestyle_social_home",
    "lifestyle_dynamic_a_task": "lifestyle_dynamic_a",
    "lifestyle_dynamic_b_task": "lifestyle_dynamic_b",
    "interior_signature_moment_task": "interior_signature_moment",
    "exterior_establishing_shot_task": "exterior_establishing_shot",
    # legacy key — kept for visual_prompts.json entries written before restructure
    "lifestyle_city_connection_task": "lifestyle_city_connection",
}
_VARIANT_ORDER = [
    "lifestyle_private_retreat",
    "lifestyle_social_home",
    "lifestyle_dynamic_a",
    "lifestyle_dynamic_b",
    "interior_signature_moment",
    "exterior_establishing_shot",
]

AD_COPY_TASKS = {
    "write_meta_ads", "write_google_ads", "write_whatsapp_script",
    "write_email", "format_for_api",
}
AD_COPY_DESC_HINTS = (
    "write_meta", "write_google", "write_whatsapp", "write_email",
    "format_for_api", "meta ad copy", "google search ad",
    "whatsapp business", "email for",
)


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


def _extract_pydantic_or_json(task_out) -> dict | None:
    """Try pydantic output first; fall back to parsing JSON from the raw string."""
    pydantic_out = getattr(task_out, "pydantic", None)
    if pydantic_out is not None:
        try:
            return pydantic_out.model_dump()
        except Exception:
            pass
    raw = getattr(task_out, "raw", "") or str(task_out)
    # Strip markdown fences if the model wrapped its JSON anyway
    raw_clean = re.sub(r"```(?:json)?\s*", "", raw).strip("`").strip()
    # Find the first {...} block
    m = re.search(r"\{[\s\S]*\}", raw_clean)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def save_for_review(content_result, audience_result=None) -> pathlib.Path:
    folder = get_review_folder()
    outputs_root = pathlib.Path(__file__).parent.parent.parent.parent / "outputs"

    if audience_result is not None:
        (folder / "persona.md").write_text(str(audience_result), encoding="utf-8")

    ad_copy_sections = []
    # Collect visual outputs keyed by variant_key; preserve canonical order
    visual_by_variant: dict[str, dict] = {}

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

            elif name in _VISUAL_TASK_TO_VARIANT:
                variant_key = _VISUAL_TASK_TO_VARIANT[name]
                parsed = _extract_pydantic_or_json(task_out)
                if parsed:
                    visual_by_variant[variant_key] = parsed

    if ad_copy_sections:
        (folder / "ad_copy.md").write_text(
            "\n\n---\n\n".join(ad_copy_sections), encoding="utf-8"
        )
    else:
        (folder / "ad_copy.md").write_text(str(content_result), encoding="utf-8")

    if visual_by_variant:
        # Build ordered list of visual_prompts.json entries
        entries = []
        for i, vk in enumerate(_VARIANT_ORDER, 1):
            if vk in visual_by_variant:
                entry = {"variant_key": vk, "prompt_num": i}
                entry.update(visual_by_variant[vk])
                entries.append(entry)

        # Enforce batch distinctness: no two ads share a palette or recipe (and thus
        # bottom-band layout). Design language stays dynamic; only collisions are nudged.
        try:
            from pikorua_adflow.crews.content_crew.task_composer import dedupe_visual_batch
            entries = dedupe_visual_batch(entries)
        except Exception:
            pass

        json_str = json.dumps(entries, indent=2, ensure_ascii=False)
        (folder / "visual_prompts.json").write_text(json_str, encoding="utf-8")
        (outputs_root / "visual_prompts.json").write_text(json_str, encoding="utf-8")
    elif os.getenv("LAZY_IMAGE_PROMPTS", "1") == "1":
        # Lazy mode: visual_prompter tasks were skipped. Write placeholder entries so
        # the portal knows how many slots exist and can show "Write prompt & Generate".
        # Each entry has variant_key + prompt_num but no scene_prose — the UI detects
        # has_prompt=False and shows the lazy-generate button.
        _main_variants = [vk for vk in _VARIANT_ORDER if vk != "exterior_establishing_shot"]
        entries = [
            {"variant_key": vk, "prompt_num": i}
            for i, vk in enumerate(_main_variants, 1)
        ]
        json_str = json.dumps(entries, indent=2, ensure_ascii=False)
        (folder / "visual_prompts.json").write_text(json_str, encoding="utf-8")
        (outputs_root / "visual_prompts.json").write_text(json_str, encoding="utf-8")

    # Copy evaluator + CRM outputs into the review folder if written this run
    for filename in (
        "targeting_brief.md", "copy_scorecard.md", "copy_rewrites.md",
        "crm_insights.md", "crm_signal.md", "targeting_selection.json",
    ):
        src = outputs_root / filename
        if src.exists():
            (folder / filename).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

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
