"""
Full end-to-end pipeline test:
  1. LLM (OpenRouter) writes scene_prose + creative choices  [uses OpenRouter credits]
  2. Python assembles the structured gpt-image-1 brief       [free]
  3. gpt-image-1 generates the image                        [uses OpenAI credits]

Run from pikorua-adflow directory:
    python test_full_pipeline.py          # variants 1, 2, 4
    python test_full_pipeline.py 1 3 5   # specific variants
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

# Load .env
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v

import litellm
from pikorua_adflow.crews.content_crew.task_composer import (
    VisualPromptOutput,
    compose_description,
    list_variants,
)
from pikorua_adflow.api.services.image_service import (
    build_gpt_image_prompt,
    sanitize_image_prompt,
    call_gpt_image_1,
)

# ── Property brief ────────────────────────────────────────────────────────────
BRIEF = {
    "property_name": "Nehrunagar Heights",
    "property_type": "Apartment",
    "locality": "NEHRUNAGAR",
    "city": "AHMEDABAD",
    "price_cr": "1.85",
    "config": "2 & 3 BHK",
    "sample_ready": True,
    "sample_ready_cta": "Sample Apartment Ready — Visit Today",
    "standout_feature": "Panoramic city views from high-floor units",
    "rera_verified": False,
    "verified_awards": False,
    "verified_certifications": False,
    "verified_landmarks": False,
}

# Ad copy for the LLM to pick headlines from (normally produced by the copy crew)
COPY_CONTEXT = """\
Variant 1: headline="Precision Lives Here." / body="High-rise living redefined for Ahmedabad."
Variant 2: headline="A City at Your Feet." / body="Every morning, a panoramic reminder of what you have achieved."
Variant 3: headline="Where Material Meets Intention." / body="Spaces built to the exacting standard of those who occupy them."
Variant 4: headline="Rise Above Ahmedabad." / body="The city's most anticipated address — now open for preview."
Variant 5: headline="The Silence of Exceptional Rooms." / body="In a truly premium apartment, the room speaks before the brochure does."
"""

VARIANT_KEYS = list_variants()
VARIANT_LABELS = {
    "architectural_perspective": "Architectural Perspective",
    "lifestyle_moment": "Lifestyle Moment",
    "iconic_representation": "Iconic Representation",
    "exterior_establishing_shot": "Exterior Establishing Shot",
    "interior_signature_moment": "Interior Signature Moment",
}


def _fill_placeholders(raw: str) -> str:
    return (
        raw
        .replace("{property_type}", BRIEF["property_type"])
        .replace("{city}", BRIEF["city"])
        .replace("{locality}", BRIEF["locality"])
        .replace("{price_cr}", str(BRIEF["price_cr"]))
        .replace("{sample_ready}", str(BRIEF["sample_ready"]))
        .replace("{reference_images}", "None uploaded.")
    )


def step1_llm_scene_prose(variant_key: str, variant_num: int) -> dict | None:
    """Call OpenRouter LLM to generate scene_prose + creative choices."""
    label = VARIANT_LABELS.get(variant_key, variant_key)
    print(f"\n[STEP 1] LLM generating scene_prose for variant {variant_num} ({label}) ...")

    task_desc = _fill_placeholders(compose_description(variant_key))
    user_msg = (
        task_desc
        + f"\n\nAd copy from this campaign (pick one headline exactly):\n{COPY_CONTEXT}"
    )

    model = os.getenv("CREATIVE_MODEL", "openrouter/anthropic/claude-sonnet-4-6")
    try:
        resp = litellm.completion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a luxury real estate creative director. "
                        "Follow the task exactly. Return ONLY valid JSON — "
                        "no markdown fences, no preamble, no explanation."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=600,
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  LLM call failed: {e}")
        return None

    # Strip markdown fences if the model wrapped anyway
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}\n  Raw output: {raw[:300]}")
        return None

    try:
        output = VisualPromptOutput(**parsed)
    except Exception as e:
        print(f"  Pydantic validation error: {e}")
        return None

    word_count = len(output.scene_prose.split())
    print(f"  scene_prose ({word_count}w): {output.scene_prose[:120]}...")
    print(f"  headline: {output.headline!r}")
    print(f"  palette: {output.palette_tag}  tone: {output.tone_tag}  corner: {output.logo_corner}")

    entry = output.model_dump()
    entry["prompt_num"] = variant_num
    entry["variant_key"] = variant_key
    return entry


def step2_assemble_prompt(entry: dict) -> str:
    """Assemble the structured gpt-image-1 brief from the LLM's creative choices."""
    print(f"\n[STEP 2] Assembling structured prompt ...")
    assembled = build_gpt_image_prompt(entry, BRIEF, entry["variant_key"])
    sanitized = sanitize_image_prompt(assembled, BRIEF, assembled=True)
    print(f"  Prompt length: {len(sanitized)} chars")
    return sanitized


def step3_generate_image(prompt: str, variant_num: int, out_dir: Path) -> Path | None:
    """Call gpt-image-1 and save the PNG."""
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        print(f"  No OPENAI_API_KEY — skipping image generation.")
        return None

    out_path = out_dir / f"pipeline_v{variant_num}.png"
    print(f"\n[STEP 3] Calling gpt-image-1 (quality=high, 4:5) ...")
    try:
        img_bytes = call_gpt_image_1(prompt, openai_key, aspect="4x5", quality="high")
        out_path.write_bytes(img_bytes)
        print(f"  Saved -> {out_path}")
        return out_path
    except Exception as e:
        print(f"  gpt-image-1 error: {e}")
        return None


def main():
    requested = [int(x) for x in sys.argv[1:] if x.isdigit() and 1 <= int(x) <= 5]
    if not requested:
        requested = [1, 2, 4]

    out_dir = Path(__file__).parent / "test_images"
    out_dir.mkdir(exist_ok=True)

    saved_paths = []
    for n in requested:
        variant_key = VARIANT_KEYS[n - 1]
        label = VARIANT_LABELS.get(variant_key, variant_key)
        print(f"\n{'='*70}")
        print(f"VARIANT {n}: {label.upper()}")
        print(f"{'='*70}")

        entry = step1_llm_scene_prose(variant_key, n)
        if not entry:
            print(f"  Skipping variant {n} — LLM step failed.")
            continue

        prompt = step2_assemble_prompt(entry)
        out_path = step3_generate_image(prompt, n, out_dir)
        if out_path:
            saved_paths.append((n, label, out_path))

    print(f"\n{'='*70}")
    print(f"DONE — {len(saved_paths)}/{len(requested)} images generated")
    for n, label, p in saved_paths:
        print(f"  V{n} {label}: {p}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
