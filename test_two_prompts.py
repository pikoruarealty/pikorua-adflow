"""
Comparison test: raw prompt vs pipeline-processed prompt — both sent to gpt-image-1.
Saves results to test_outputs/ with full prompt text alongside each image.
"""

import os
import sys
import textwrap
from pathlib import Path
from datetime import datetime

# ── API key ──────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    sys.exit("OPENAI_API_KEY not found in .env")

# ── Output dir ────────────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).parent / "test_outputs"
OUT_DIR.mkdir(exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# ── Import pipeline functions ─────────────────────────────────────────────────
from pikorua_adflow.api.services.image_service import (
    call_gpt_image_1,
    build_gpt_image_prompt,
    sanitize_image_prompt,
)

# ─────────────────────────────────────────────────────────────────────────────
# IMAGE 1 — RAW PROMPT (sent exactly as the user typed it)
# ─────────────────────────────────────────────────────────────────────────────
RAW_PROMPT = textwrap.dedent("""\
Exterior establishing shot of a luxury apartment building in Nehrunagar, Ahmedabad, soaring against a deep indigo dusk skyline. Sony A7R V, 85mm f/4, positioned at a three-quarter angle across the street showcasing two prominent faces of the building.

Warm interior lights glow from within, creating a vibrant lived-in atmosphere. Mature trees line a quiet street in the foreground, adding depth. Elegant light trails from passing cars weave around the base of the building. The facade features floor-to-ceiling glazing with slim dark mullions, fluted stone spandrels, and sophisticated timber balconies. Soft atmospheric haze gently blurs the distant skyline.

Treat this as a finished luxury real estate advertisement, not merely a photograph.

FIRST create a world-class architectural photograph.
THEN transform it into a premium real estate marketing campaign creative.

Design language:
Ultra-premium developer advertisement, luxury property brochure aesthetic, high-end Indian real estate campaign, Lodha / Shivalik / Iscon / Swati style marketing creative.

Layout structure:

• Large hero property image occupying most of the canvas.
• Elegant gold-accent border framing the entire composition.
• Strong visual hierarchy with editorial typography.
• Large location name as the primary headline.
• Secondary location descriptor.
• Dedicated luxury pricing panel.
• Premium "Sample Apartment Ready" or equivalent showcase badge.
• Bottom information strip containing property highlights.
• Information modules should feel designed by an agency, not automatically placed text.

Typography hierarchy:

Top Eyebrow:
"THE ADDRESS YOU NEVER WANTED TO LEAVE."

Primary Headline:
"NEHRUNAGAR"

Secondary:
"AHMEDABAD"

Lifestyle Headline:
"Live in the Heart of Ahmedabad."

Pricing Module:
"₹3 Cr"
"ONWARDS"

Location Marker:
"NEHRUNAGAR, AHMEDABAD"

Bottom Information Band:

Left Module:
"3 & 4"
"BHK RESIDENCES"

Center Module:
"STARTING AT"
"₹3 Cr"
"ONWARDS"

Right Module:
"100%"
"CHEQUE PAYMENT ONLY"

Center Floating Badge:
"SAMPLE APARTMENT"
"READY"
"VISIT TODAY"

Design treatment:

• Premium serif typography.
• Gold and ivory color palette.
• Dark navy, charcoal and gold luxury branding aesthetic.
• Structured spacing.
• Clear grid alignment.
• Marketing-agency level composition.
• Every text element perfectly legible.
• Real estate brochure quality.
• High-end luxury developer advertisement.
• No logos.
• No watermarks.
• No random decorative clutter.
• Professional sales campaign aesthetic.



Make the aspect ratio 3:4\
""")

print("=" * 60)
print("IMAGE 1 — Sending RAW prompt to gpt-image-1 …")
print(f"Prompt length: {len(RAW_PROMPT)} chars")
print("=" * 60)

img1_bytes = call_gpt_image_1(RAW_PROMPT, OPENAI_API_KEY, aspect="3:4", quality="high")
img1_path = OUT_DIR / f"{stamp}_1_raw.png"
img1_path.write_bytes(img1_bytes)
(OUT_DIR / f"{stamp}_1_raw_prompt.txt").write_text(RAW_PROMPT, encoding="utf-8")
print(f"[IMAGE 1 SAVED] {img1_path}")

# ─────────────────────────────────────────────────────────────────────────────
# IMAGE 2 — PIPELINE-PROCESSED PROMPT
# ─────────────────────────────────────────────────────────────────────────────

# The brief represents what the campaign crew would have populated for Nehrunagar
BRIEF = {
    "locality": "Nehrunagar",
    "city": "Ahmedabad",
    "price_cr": "3",
    "config": "3 & 4 BHK",
    "sample_ready": True,
    "usps": ["100% Cheque Payment Only"],
    "rera_verified": False,
    "verified_awards": [],
    "verified_certifications": [],
    "verified_landmarks": [],
    "possession_date": None,
}

# The visual_prompter LLM's structured output (scene_prose + creative choices)
ENTRY = {
    "scene_prose": (
        "Exterior establishing shot of a luxury apartment building in Nehrunagar, Ahmedabad, "
        "soaring against a deep indigo dusk skyline. Sony A7R V, 85mm f/4, positioned at a "
        "three-quarter angle across the street showcasing two prominent faces of the building. "
        "Warm interior lights glow from within, creating a vibrant lived-in atmosphere. Mature "
        "trees line the foreground, adding depth. Elegant light trails from passing cars weave "
        "around the base. The facade features floor-to-ceiling glazing with slim dark mullions, "
        "fluted stone spandrels, and sophisticated timber balconies."
    ),
    "headline": "Live in the Heart of Ahmedabad.",
    "eyebrow": "THE ADDRESS YOU NEVER WANTED TO LEAVE.",
    "palette_tag": "navy_gold",
    "tone_tag": "dark_luxury",
    "scene_tag": "exterior_establishing_shot",
    "logo_corner": "bottom_left",
}

VARIANT_KEY = "exterior_establishing_shot"

# Step 1: assemble via build_gpt_image_prompt()
assembled = build_gpt_image_prompt(ENTRY, BRIEF, VARIANT_KEY)

# Step 2: sanitize (assembled=True skips sample-ready / price enforcement
#         since those are already baked in deterministically)
sanitized = sanitize_image_prompt(assembled, BRIEF, assembled=True)

print()
print("=" * 60)
print("IMAGE 2 — Sending PIPELINE-PROCESSED prompt to gpt-image-1 …")
print(f"Assembled prompt length:  {len(assembled)} chars")
print(f"Sanitized prompt length:  {len(sanitized)} chars")
print("=" * 60)
print()
print("── ASSEMBLED PROMPT ────────────────────────────────────────")
print(assembled)
print()
print("── AFTER SANITIZER ─────────────────────────────────────────")
print(sanitized)
print()

img2_bytes = call_gpt_image_1(sanitized, OPENAI_API_KEY, aspect="3:4", quality="high")
img2_path = OUT_DIR / f"{stamp}_2_pipeline.png"
img2_path.write_bytes(img2_bytes)
(OUT_DIR / f"{stamp}_2_pipeline_assembled.txt").write_text(assembled, encoding="utf-8")
(OUT_DIR / f"{stamp}_2_pipeline_sanitized.txt").write_text(sanitized, encoding="utf-8")
print(f"[IMAGE 2 SAVED] {img2_path}")

print()
print("=" * 60)
print("RESULTS")
print("=" * 60)
print(f"  Image 1 (raw):      {img1_path}")
print(f"  Image 2 (pipeline): {img2_path}")
print(f"  Prompts saved to:   {OUT_DIR}")
print("=" * 60)
