"""
test_pipeline_ideogram.py — Full ad pipeline test without running the crew.

Flow (mirrors what the real pipeline does):
  1. Hardcoded Nehrunagar brief (from the ChatGPT reference ad)
  2. One cheap LLM call per variant → scene_prose + headline + palette_tag etc.
  3. build_gpt_image_prompt() assembles the full structured ad brief (same as prod)
  4. sanitize_image_prompt() cleans it (same as prod)
  5. call_ideogram() generates the PNG (same as prod)
  6. composite_logo() stamps the logo (same as prod)
  7. Saves to test_output/

Cost: ~$0.01 LLM + ~$0.09 per image (Ideogram QUALITY) = ~$0.19 for 2 images

Usage:
    cd pikorua-adflow
    python test_pipeline_ideogram.py            # generates 2 variants
    python test_pipeline_ideogram.py --no-llm   # skip LLM, use hardcoded entries
    python test_pipeline_ideogram.py 1          # variant 1 only
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ── Bootstrap ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from pikorua_adflow.crews.content_crew.task_composer import (
    VisualPromptOutput,
    compose_description,
    list_variants,
)
from pikorua_adflow.api.services.image_service import (
    build_gpt_image_prompt,
    sanitize_image_prompt,
    call_ideogram,
)

# ── Nehrunagar brief (from the ChatGPT winning prompt) ───────────────────────
BRIEF = {
    "property_name": "Nehrunagar Residences",
    "property_type": "Apartment",
    "locality": "Nehrunagar",
    "city": "Ahmedabad",
    "config": "3 & 4 BHK",
    "price_cr": "3",
    "sample_ready": True,
    "usps": ["100% Cheque Payment Only"],
    "rera_verified": False,
    "verified_awards": False,
    "verified_certifications": False,
    "verified_landmarks": False,
}

# Fake copy for LLM to pick headlines from (normally crew produces this)
COPY_CONTEXT = """
Variant 1: headline="THE ADDRESS YOU NEVER WANTED TO LEAVE." / body="Premium 3 & 4 BHK residences in the heart of Nehrunagar."
Variant 2: headline="Rise Above Ahmedabad." / body="The city's most anticipated address — now open for preview."
Variant 3: headline="The Silence of Exceptional Rooms." / body="In a truly premium apartment, the room speaks before the brochure does."
Variant 4: headline="A City at Your Feet." / body="Every morning, a panoramic reminder of what you've achieved."
Variant 5: headline="Where Light Meets Architecture." / body="Spaces built to the exacting standard of those who occupy them."
"""

# ── Two variants to test ──────────────────────────────────────────────────────
# exterior → bordered_campaign (dark_luxury) → navy_gold — closest to ChatGPT ref
# lifestyle → immersive_fullbleed (bright_aspirational) → ivory_warmth — contrast
TEST_VARIANTS = [
    "exterior_establishing_shot",
    "lifestyle_moment",
]

# ── Fallback entries if --no-llm flag is passed ───────────────────────────────
HARDCODED_ENTRIES = {
    "exterior_establishing_shot": {
        "variant_key": "exterior_establishing_shot",
        "scene_prose": (
            "Sony A7R V, 85mm f/4 tilt-shift, three-quarter angle from street level, "
            "long exposure ISO 200 on tripod. Deep indigo blue-hour sky, 6400K tungsten "
            "warm interior light glowing through glazing, motion-blurred amber light trails "
            "from passing cars in foreground. Subtle atmospheric haze softens the distant "
            "city skyline behind the building.\n\n"
            "The building presents floor-to-ceiling glazing with slim dark aluminium mullions "
            "across every floor, fluted cream limestone spandrel panels between each level, "
            "and cantilevered concrete balconies with brushed steel railings. The base is clad "
            "in polished dark granite. A double-height landscaped entrance with mature palms "
            "and recessed ground uplighters frames the street-level arrival."
        ),
        "headline": "THE ADDRESS YOU NEVER WANTED TO LEAVE.",
        "eyebrow": "NEHRUNAGAR'S MOST ANTICIPATED ADDRESS",
        "palette_tag": "navy_gold",
        "scene_tag": "twilight_street_level_light_trails",
        "tone_tag": "dark_luxury",
        "logo_corner": "bottom-right",
    },
    "lifestyle_moment": {
        "variant_key": "lifestyle_moment",
        "scene_prose": (
            "Sony A7R V, 35mm f/2.8, shot from inside the apartment through the open "
            "balcony door, golden hour, ISO 400. Warm 4200K diffused sunlight floods across "
            "the balcony floor tiles. A faint lens flare catches the edge of the glass door "
            "frame. The city skyline glows softly hazed in the distance.\n\n"
            "A woman in a white linen kurta stands at the balcony railing, back to camera, "
            "holding a small cup, looking out over the city. The balcony floor is grey "
            "matt porcelain tile. The railing is frameless tempered glass with a brushed "
            "steel top rail. The apartment interior behind her shows a living room with "
            "warm-toned engineered oak flooring and a low linen sofa visible through the door."
        ),
        "headline": "A City at Your Feet.",
        "eyebrow": "",
        "palette_tag": "ivory_warmth",
        "scene_tag": "balcony_golden_hour_moment",
        "tone_tag": "bright_aspirational",
        "logo_corner": "bottom-left",
    },
}


def _llm_call(variant_key: str) -> dict | None:
    """One LLM call → VisualPromptOutput JSON for the given variant."""
    try:
        import litellm
    except ImportError:
        print("  litellm not installed — run: pip install litellm")
        return None

    # Use a cheap model for the test to preserve OpenRouter balance.
    # Falls back to CREATIVE_MODEL if the env override isn't set.
    model = os.getenv(
        "TEST_MODEL",
        os.getenv("CREATIVE_MODEL", "openrouter/google/gemini-2.5-flash"),
    )

    task_desc = compose_description(variant_key)
    task_desc = (
        task_desc
        .replace("{property_type}", BRIEF["property_type"])
        .replace("{city}", BRIEF["city"])
        .replace("{locality}", BRIEF["locality"])
        .replace("{price_cr}", str(BRIEF["price_cr"]))
        .replace("{sample_ready}", str(BRIEF["sample_ready"]))
        .replace("{reference_images}", "None uploaded.")
    )

    system_msg = (
        "You are a luxury real estate creative director. "
        "Follow the task description exactly. "
        "Return ONLY valid JSON — no markdown fences, no preamble."
    )
    user_msg = (
        task_desc
        + f"\n\nAvailable ad copy (pick one headline from this list):\n{COPY_CONTEXT}"
    )

    print(f"  LLM: {model}")
    try:
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=700,
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  LLM call failed: {e}")
        return None

    # Strip markdown fences if model wrapped anyway
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    raw = raw.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}\n  Raw output:\n{raw[:400]}")
        return None

    try:
        output = VisualPromptOutput(**parsed)
    except Exception as e:
        print(f"  Pydantic validation error: {e}")
        return None

    entry = output.model_dump()
    entry["variant_key"] = variant_key
    return entry


def run_variant(variant_key: str, idx: int, use_llm: bool, out_dir: Path) -> bool:
    print(f"\n{'─' * 68}")
    print(f"VARIANT {idx}: {variant_key.upper().replace('_', ' ')}")
    print(f"{'─' * 68}")

    # Step 1: get creative choices
    if use_llm:
        print("Step 1/3  Calling LLM for scene prose + creative choices…")
        entry = _llm_call(variant_key)
        if not entry:
            print("  FAILED — falling back to hardcoded entry")
            entry = HARDCODED_ENTRIES.get(variant_key)
            if not entry:
                print("  No fallback available, skipping.")
                return False
    else:
        print("Step 1/4  Using hardcoded entry (--no-llm)")
        entry = HARDCODED_ENTRIES.get(variant_key)
        if not entry:
            print(f"  No hardcoded entry for {variant_key}, skipping.")
            return False

    print(f"  palette_tag:  {entry.get('palette_tag')}")
    print(f"  tone_tag:     {entry.get('tone_tag')}")
    print(f"  scene_tag:    {entry.get('scene_tag')}")
    print(f"  logo_corner:  {entry.get('logo_corner')}")
    print(f"  headline:     {entry.get('headline')}")

    # Step 2: assemble full structured ad prompt
    print("\nStep 2/3  Assembling structured ad prompt…")
    raw_prompt = build_gpt_image_prompt(entry, BRIEF, variant_key)
    sanitized = sanitize_image_prompt(raw_prompt, BRIEF, assembled=True)

    print(f"\n{'·' * 68}")
    print("ASSEMBLED PROMPT (what goes to Ideogram):")
    print(f"{'·' * 68}")
    print(sanitized)
    print(f"{'·' * 68}\n")

    # Step 3: generate image
    ideogram_key = os.getenv("IDEOGRAM_API_KEY", "")
    if not ideogram_key:
        print("  IDEOGRAM_API_KEY not set — skipping image generation.")
        return False

    print("Step 3/3  Calling Ideogram (QUALITY, 4:5)… (~20-30s)")
    try:
        img_bytes = call_ideogram(sanitized, ideogram_key, speed="QUALITY", aspect="4x5")
    except Exception as e:
        print(f"  Ideogram failed: {e}")
        return False

    # Step 4: save + composite logo
    out_path = out_dir / f"test_{idx}_{variant_key}.png"
    out_path.write_bytes(img_bytes)
    print(f"\n  DONE → {out_path}")
    print(f"  Size: {len(img_bytes) // 1024} KB")
    return True


def main():
    args = sys.argv[1:]
    use_llm = "--no-llm" not in args
    nums = [int(a) for a in args if a.isdigit() and 1 <= int(a) <= len(TEST_VARIANTS)]
    variants_to_run = (
        [TEST_VARIANTS[n - 1] for n in nums] if nums else TEST_VARIANTS
    )

    out_dir = Path(__file__).parent / "test_output"
    out_dir.mkdir(exist_ok=True)

    print("\n" + "=" * 68)
    print("PIKORUA AD PIPELINE — IDEOGRAM TEST")
    print(f"Property:  {BRIEF['locality']}, {BRIEF['city']}")
    print(f"Config:    {BRIEF['config']}  |  Rs.{BRIEF['price_cr']} Cr  |  Sample: {BRIEF['sample_ready']}")
    print(f"Variants:  {', '.join(variants_to_run)}")
    print(f"LLM:       {'yes' if use_llm else 'no (hardcoded)'}")
    print(f"Output:    {out_dir}")
    print("=" * 68)

    passed = 0
    for i, vk in enumerate(variants_to_run, start=1):
        ok = run_variant(vk, i, use_llm, out_dir)
        if ok:
            passed += 1

    print(f"\n{'=' * 68}")
    print(f"RESULT: {passed}/{len(variants_to_run)} images generated")
    if passed:
        print(f"Images saved to: {out_dir}/")
    print("=" * 68)


if __name__ == "__main__":
    main()
