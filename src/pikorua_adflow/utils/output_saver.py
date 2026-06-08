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

    (folder / "ad_copy.md").write_text(str(content_result), encoding="utf-8")

    # Copy evaluator outputs into the review folder if they were written this run
    for filename in ("copy_scorecard.md", "copy_rewrites.md"):
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
