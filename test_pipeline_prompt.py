"""
Runs the full image prompt pipeline for Nehrunagar / exterior_establishing_shot:
  1. visual_prompter LLM call  (same task description the crew sends)
  2. build_gpt_image_prompt()
  3. sanitize_image_prompt()

Stops before calling gpt-image-1. Prints the final prompt.
"""

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import litellm

from pikorua_adflow.crews.content_crew.task_composer import (
    VisualPromptOutput,
    compose_description,
)
from pikorua_adflow.api.services.image_service import (
    build_gpt_image_prompt,
    sanitize_image_prompt,
)

# ── Property brief ────────────────────────────────────────────────────────────
PROPERTY = {
    "city": "Ahmedabad",
    "locality": "Nehrunagar",
    "price_cr": "3",
    "property_type": "luxury residential apartments",
    "sample_ready": True,
    "config": "3 & 4 BHK",
    "usps": ["100% Cheque Payment Only"],
}

# Sample headlines the crew's copy writer would have produced — the visual_prompter
# picks one from these (same as what ContentCrew passes as context).
SAMPLE_COPY_HEADLINES = [
    "Live in the Heart of Ahmedabad.",
    "Where Nehrunagar Becomes Home.",
    "Crafted for Those Who Expect the Best.",
    "The Address That Speaks Before You Do.",
    "A Residence That Defines the Neighbourhood.",
]

VARIANT_KEY = "exterior_establishing_shot"

# ── Build the task description exactly as ContentCrew does ───────────────────
task_desc = compose_description(
    VARIANT_KEY,
    prior_scene_tags=[],
    prior_tone_tags=[],
    prior_palette_tags=[],
)

# Fill template variables (CrewAI normally does this at kickoff)
task_desc = task_desc.replace("{city}", PROPERTY["city"])
task_desc = task_desc.replace("{locality}", PROPERTY["locality"])
task_desc = task_desc.replace("{price_cr}", PROPERTY["price_cr"])
task_desc = task_desc.replace("{sample_ready}", str(PROPERTY["sample_ready"]))
task_desc = task_desc.replace("{property_type}", PROPERTY["property_type"])
task_desc = task_desc.replace(
    "{reference_images}",
    "No reference images provided for this run.",
)

# ── Call the visual_prompter LLM ─────────────────────────────────────────────
MODEL = os.environ.get("MODEL", "openrouter/openai/gpt-4o-mini")

messages = [
    {
        "role": "system",
        "content": (
            "You are a luxury real estate advertising creative director. "
            "You write cinematic photography directions and select campaign creative choices. "
            "You always respond with ONLY valid JSON — no markdown fences, no preamble."
        ),
    },
    {
        "role": "user",
        "content": (
            f"{task_desc}\n\n"
            "--- COPY CONTEXT ---\n"
            "The following headlines were produced by the campaign copy writer. "
            "Select the single best headline for the scene you describe:\n"
            + "\n".join(f"- {h}" for h in SAMPLE_COPY_HEADLINES)
        ),
    },
]

print("=" * 60)
print(f"Calling visual_prompter LLM ({MODEL}) …")
print("=" * 60)

response = litellm.completion(
    model=MODEL,
    messages=messages,
    temperature=0.8,
    max_tokens=600,
)

raw_output = response.choices[0].message.content.strip()

print("\n── LLM raw output ──────────────────────────────────────────")
print(raw_output)

# ── Parse JSON (strip markdown fences if LLM added them anyway) ──────────────
clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_output, flags=re.DOTALL).strip()
entry = json.loads(clean)

print("\n── Parsed fields ────────────────────────────────────────────")
for k, v in entry.items():
    val_preview = str(v)[:120] + ("…" if len(str(v)) > 120 else "")
    print(f"  {k:15s}: {val_preview}")

# ── Build structured prompt ───────────────────────────────────────────────────
BRIEF = {
    "locality": PROPERTY["locality"],
    "city": PROPERTY["city"],
    "price_cr": PROPERTY["price_cr"],
    "config": PROPERTY["config"],
    "sample_ready": PROPERTY["sample_ready"],
    "usps": PROPERTY["usps"],
}

assembled = build_gpt_image_prompt(entry, BRIEF, VARIANT_KEY)
sanitized = sanitize_image_prompt(assembled, BRIEF, assembled=True)

print("\n\n" + "=" * 60)
print("FINAL PROMPT (assembled + sanitized) — ready for gpt-image-1")
print("=" * 60 + "\n")
print(sanitized)
print("\n" + "=" * 60)
print(f"Prompt length: {len(sanitized)} characters")
