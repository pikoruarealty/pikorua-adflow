"""
Test the visual_prompter LLM call in isolation — no full crew, minimal cost.

This calls the LLM exactly once (or N times for N variants) using the same
task description that compose_description() produces, then validates the JSON
output and runs it through build_gpt_image_prompt().

Cost: ~1-2 LLM calls per variant (gemini-2.5-flash = ~$0.001 each).
Run from the pikorua-adflow directory:

    python test_visual_prompter_llm.py
    python test_visual_prompter_llm.py 2          # variant 2 only (lifestyle)
    python test_visual_prompter_llm.py 1 3 5      # variants 1, 3, 5
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

# Load .env so CREATIVE_MODEL + OPENROUTER_API_KEY are available
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

import litellm
from pikorua_adflow.crews.content_crew.task_composer import (
    VisualPromptOutput,
    compose_description,
    list_variants,
)
from pikorua_adflow.api.services.image_service import (
    build_gpt_image_prompt,
    sanitize_image_prompt,
)

# ── Brief — edit to match the property you want to test ─────────────────────
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

# Fake ad copy for the LLM to pick a headline from (normally produced by copy crew)
COPY_CONTEXT = """
The ad copy variants below are from the copy crew. Pick one headline.

Variant 1: headline="Precision Lives Here." / body="High-rise living redefined for Ahmedabad."
Variant 2: headline="A City at Your Feet." / body="Every morning, a panoramic reminder of what you've achieved."
Variant 3: headline="Where Material Meets Intention." / body="Spaces built to the exacting standard of those who occupy them."
Variant 4: headline="Rise Above Ahmedabad." / body="The city's most anticipated address — now open for preview."
Variant 5: headline="The Silence of Exceptional Rooms." / body="In a truly premium apartment, the room speaks before the brochure does."
"""


def _crew_inputs_substituted(raw_desc: str) -> str:
    """Replace CrewAI {template} placeholders with actual brief values."""
    return (
        raw_desc
        .replace("{property_type}", BRIEF["property_type"])
        .replace("{city}", BRIEF["city"])
        .replace("{locality}", BRIEF["locality"])
        .replace("{price_cr}", str(BRIEF["price_cr"]))
        .replace("{sample_ready}", str(BRIEF["sample_ready"]))
        .replace("{reference_images}", "None uploaded.")
    )


def test_variant(variant_key: str, variant_num: int) -> dict | None:
    print(f"\n{'='*70}")
    print(f"TESTING LLM: Variant {variant_num} — {variant_key.upper().replace('_', ' ')}")
    print(f"{'='*70}")

    task_desc = compose_description(variant_key)
    task_desc = _crew_inputs_substituted(task_desc)

    system_msg = (
        "You are a luxury real estate creative director. "
        "You will be given a task description. Follow it exactly. "
        "Return ONLY valid JSON — no markdown, no preamble, no explanation."
    )
    user_msg = task_desc + f"\n\nAvailable ad copy (pick one headline from this):\n{COPY_CONTEXT}"

    model = os.getenv("CREATIVE_MODEL", "gemini/gemini-2.5-flash")
    print(f"Model: {model}")

    try:
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=600,
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"LLM call failed: {e}")
        return None

    # Strip markdown fences if the model wrapped anyway
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    print(f"\nRaw LLM output:")
    print(raw[:600])

    # Validate JSON
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\nJSON PARSE ERROR: {e}")
        return None

    # Validate against Pydantic model
    try:
        output = VisualPromptOutput(**parsed)
    except Exception as e:
        print(f"\nPydantic validation error: {e}")
        return None

    print(f"\nValidated fields:")
    print(f"  scene_prose ({len(output.scene_prose.split())} words): {output.scene_prose[:100]}...")
    print(f"  headline: {output.headline}")
    print(f"  eyebrow: {output.eyebrow!r}")
    print(f"  palette_tag: {output.palette_tag}")
    print(f"  scene_tag: {output.scene_tag}")
    print(f"  tone_tag: {output.tone_tag}")
    print(f"  logo_corner: {output.logo_corner}")

    # Now assemble the full gpt-image-1 prompt
    entry = output.model_dump()
    entry["prompt_num"] = variant_num
    entry["variant_key"] = variant_key

    assembled = build_gpt_image_prompt(entry, BRIEF, variant_key)
    sanitized = sanitize_image_prompt(assembled, BRIEF, assembled=True)

    print(f"\nAssembled + sanitized prompt ({len(sanitized)} chars):")
    print(sanitized[:500] + "...")

    return entry


def main():
    variants = list_variants()
    variant_map = {i + 1: vk for i, vk in enumerate(variants)}

    # Parse CLI args: "1 3 5" → test variants 1, 3, 5
    requested = sys.argv[1:]
    if requested:
        nums = [int(x) for x in requested if x.isdigit() and 1 <= int(x) <= 5]
    else:
        nums = list(range(1, 6))

    results = []
    for n in nums:
        vk = variant_map.get(n)
        if not vk:
            print(f"Variant {n} not found, skipping.")
            continue
        entry = test_variant(vk, n)
        if entry:
            results.append(entry)

    print(f"\n{'='*70}")
    print(f"SUMMARY: {len(results)}/{len(nums)} variants passed validation")
    if len(results) == len(nums):
        print("All tested variants produce valid scene_prose JSON -- new pipeline is working.")
    else:
        failed = len(nums) - len(results)
        print(f"{failed} variant(s) failed — check LLM output above for issues.")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
