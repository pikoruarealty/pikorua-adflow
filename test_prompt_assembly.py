"""
Manual test for the Session 30 image pipeline.

Run from the pikorua-adflow directory:
    python test_prompt_assembly.py

This prints the fully assembled gpt-image-1 prompt for each of the 5 variants
using a sample Nehrunagar brief. Zero API cost — pure Python.

To also generate one image (costs OpenAI credits), set GENERATE=1:
    GENERATE=1 python test_prompt_assembly.py
"""

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

from pikorua_adflow.api.services.image_service import (
    build_gpt_image_prompt,
    sanitize_image_prompt,
    PALETTE_CONFIGS,
    AD_STRUCTURES,
    call_gpt_image_1,
)

# ── Sample brief — replace with real values to test a specific property ──────
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

# ── Sample LLM outputs — these are what the visual_prompter crew task produces ─
# Each entry = one variant's creative choices from the LLM (60-80 word scene prose
# + constrained picks). In a real run, these come from visual_prompts.json.
SAMPLE_ENTRIES = [
    {
        "variant_key": "architectural_perspective",
        "prompt_num": 1,
        "scene_prose": (
            "Interior corridor vanishing point, Sony A7R IV, 24mm tilt-shift, f/8, ISO 200. "
            "Late afternoon rake light cuts through floor-to-ceiling glazing, casting long "
            "geometric shadows across polished concrete. Warm 3200K tungsten glow from recessed "
            "strips contrasts with cool daylight beyond the glass. Brushed nickel handrail. "
            "Shallow depth of field draws the eye to the distant horizon. One micro lens flare."
        ),
        "headline": "Precision Lives Here.",
        "eyebrow": "NEHRUNAGAR, AHMEDABAD",
        "palette_tag": "navy_gold",
        "tone_tag": "dark_luxury",
        "logo_corner": "bottom-right",
    },
    {
        "variant_key": "lifestyle_moment",
        "prompt_num": 2,
        "scene_prose": (
            "Canon EOS R5, 85mm f/1.4, ISO 800, golden hour. A woman in a linen kurta "
            "stands at a full-height window, back to camera, holding a cup of chai, gazing "
            "at the city below. Warm amber light halos her silhouette. Interior wall in "
            "textured ivory plaster. Long shadow from the window frame across the terrazzo floor. "
            "Slight grain. Soft bokeh on the distant skyline."
        ),
        "headline": "A City at Your Feet.",
        "eyebrow": "",
        "palette_tag": "ivory_warmth",
        "tone_tag": "bright_aspirational",
        "logo_corner": "bottom-left",
    },
    {
        "variant_key": "iconic_representation",
        "prompt_num": 3,
        "scene_prose": (
            "Nikon Z9, 105mm macro, f/2.8, ISO 100, studio-controlled. A single Bianco Carrara "
            "marble slab edge, photographed at 45 degrees. Surface veining catches raking light "
            "from a single off-axis softbox at 2700K. Extreme negative space: deep charcoal "
            "void occupies 70% of the frame. One natural inclusion in the stone. Shadow is "
            "crisp and directional. Whisper of chromatic aberration at the edge."
        ),
        "headline": "Where Material Meets Intention.",
        "eyebrow": "",
        "palette_tag": "charcoal_gold",
        "tone_tag": "dark_luxury",
        "logo_corner": "top-right",
    },
    {
        "variant_key": "exterior_establishing_shot",
        "prompt_num": 4,
        "scene_prose": (
            "Fujifilm GFX 100S, 32mm f/5.6, ISO 400, blue hour. Three-quarter angle view of "
            "a contemporary high-rise tower. Deep indigo sky, residual orange glow on the "
            "horizon. Warm amber light glowing from occupied units. Street-level motion blur: "
            "headlight and taillight trails. Urban foreground: palms silhouetted against the "
            "building base. Lens flare from a streetlamp at frame edge."
        ),
        "headline": "Rise Above Ahmedabad.",
        "eyebrow": "NOW OPEN FOR PREVIEW",
        "palette_tag": "slate_cream",
        "tone_tag": "dark_luxury",
        "logo_corner": "bottom-right",
    },
    {
        "variant_key": "interior_signature_moment",
        "prompt_num": 5,
        "scene_prose": (
            "Leica SL2, 28mm f/4, ISO 200. Empty living room, no people. Late afternoon "
            "diagonal light slices across a large-format marble floor, creating a near-black "
            "shadow band. Full-height glazing reveals a twilight city panorama. A single "
            "white orchid in a ceramic vase on the windowsill — the only object. Shadow "
            "from the orchid stretches dramatically across the floor. Quiet, cinematic."
        ),
        "headline": "The Silence of Exceptional Rooms.",
        "eyebrow": "",
        "palette_tag": "burgundy_gold",
        "tone_tag": "dark_luxury",
        "logo_corner": "bottom-left",
    },
]


def main():
    print("=" * 80)
    print("SESSION 30 IMAGE PIPELINE — PROMPT ASSEMBLY TEST")
    print("=" * 80)

    assembled_prompts = []

    for entry in SAMPLE_ENTRIES:
        n = entry["prompt_num"]
        vk = entry["variant_key"]
        palette = entry["palette_tag"]
        tone = entry["tone_tag"]

        print(f"\n{'-'*80}")
        print(f"VARIANT {n}: {vk.upper().replace('_', ' ')}")
        print(f"  palette={palette}  tone={tone}  corner={entry['logo_corner']}")
        print(f"{'-'*80}")

        assembled = build_gpt_image_prompt(entry, BRIEF, vk)
        sanitized = sanitize_image_prompt(assembled, BRIEF, assembled=True)

        print(sanitized)
        assembled_prompts.append((n, sanitized))

    print(f"\n{'=' * 80}")
    print(f"Assembly complete: {len(assembled_prompts)} prompts ready for gpt-image-1")
    print(f"{'=' * 80}")

    # ── Generate images (costs OpenAI credits) ───────────────────────────────
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        print("\nNo OPENAI_API_KEY found — skipping image generation.")
        return

    out_dir = Path(__file__).parent / "test_images"
    out_dir.mkdir(exist_ok=True)

    # CLI args: "1 3" → only those variants; no args → all 5
    requested = [int(x) for x in sys.argv[1:] if x.isdigit() and 1 <= int(x) <= 5]
    to_generate = [(n, p) for n, p in assembled_prompts if not requested or n in requested]

    print(f"\nGenerating {len(to_generate)} image(s) with gpt-image-1 (quality=high, 4:5) ...")
    for n, prompt in to_generate:
        out_path = out_dir / f"test_v{n}.png"
        print(f"  Variant {n} ... ", end="", flush=True)
        try:
            img_bytes = call_gpt_image_1(prompt, openai_key, aspect="4x5", quality="high")
            out_path.write_bytes(img_bytes)
            print(f"saved -> {out_path}")
        except Exception as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
