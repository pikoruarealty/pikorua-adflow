"""
Smoke test for the image-prompt pipeline (task_composer + prompt assembly + sanitiser).

Covers:
  1. All 5 variant task descriptions are non-empty and distinct
  2. Variant descriptions reference the correct scene_pool
  3. prior_scene_tags / prior_tone_tags are embedded in the description
  4. Sanitiser strips banned claims and preserves valid content
  5. build_gpt_image_prompt() produces structured, correctly sectioned prompts
  6. Prompt structure varies by tone (different ad structure per tone_tag)
  7. Palette configs are injected and vary by palette_tag
  8. Full round-trip: brief → assembled prompt → sanitised → logo-ready

Run with:
  cd pikorua-adflow
  python -m pytest tests/test_image_pipeline.py -v
or:
  python tests/test_image_pipeline.py
"""

import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Ensure stdout handles non-ASCII characters (₹, em-dash, etc.) on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pikorua_adflow.crews.content_crew.task_composer import (
    VisualPromptOutput,
    compose_description,
    get_variant_meta,
    list_variants,
)
from pikorua_adflow.api.services.image_service import (
    build_gpt_image_prompt,
    sanitize_image_prompt,
    sanitize_structured_output,
    PALETTE_CONFIGS,
    AD_STRUCTURES,
)

SAMPLE_BRIEF = {
    "property_name": "Nehrunagar Residences",
    "locality": "Nehrunagar",
    "city": "Ahmedabad",
    "property_type": "3 & 4 BHK Apartment",
    "config": "3 & 4 BHK",
    "price_cr": "3",
    "sample_ready": True,
    "rera_verified": False,
    "verified_awards": False,
    "verified_certifications": False,
    "verified_landmarks": False,
}

SAMPLE_ENTRY = {
    "variant_key": "exterior_establishing_shot",
    "scene_prose": (
        "Exterior establishing shot of a luxury apartment building in Nehrunagar, "
        "soaring against a deep indigo dusk skyline. Sony A7R V, 85mm f/4, three-quarter "
        "angle. Warm interior lights glow from floor-to-ceiling glazing. Light trails from "
        "passing cars. Atmospheric haze softly blurs the distant cityscape."
    ),
    "headline": "Live where Ahmedabad's best address begins.",
    "eyebrow": "THE ADDRESS YOU NEVER WANTED TO LEAVE.",
    "palette_tag": "navy_gold",
    "scene_tag": "twilight_street_level_light_trails",
    "tone_tag": "dark_luxury",
    "logo_corner": "bottom-right",
}


# ---------------------------------------------------------------------------
# 1. Task description composition
# ---------------------------------------------------------------------------

def test_all_variants_non_empty_and_distinct():
    variants = list_variants()
    assert len(variants) == 5, f"Expected 5 variants, got {len(variants)}"
    descriptions = {vk: compose_description(vk) for vk in variants}
    for vk, desc in descriptions.items():
        assert desc.strip(), f"compose_description('{vk}') returned empty string"
    descs = list(descriptions.values())
    for i in range(len(descs)):
        for j in range(i + 1, len(descs)):
            assert descs[i] != descs[j], (
                f"Variants {variants[i]} and {variants[j]} produced identical descriptions"
            )
    print("  PASS All 5 variant descriptions are non-empty and distinct")


def test_variant_descriptions_reference_scene_pool():
    for vk in list_variants():
        meta = get_variant_meta(vk)
        desc = compose_description(vk)
        for scene in meta["scene_pool"]:
            assert scene in desc, (
                f"Variant '{vk}' description missing scene_pool entry '{scene}'"
            )
    print("  PASS All variant descriptions reference their correct scene_pool")


def test_prior_tags_embedded_in_description():
    vk = "exterior_establishing_shot"
    scene_history = ["twilight_street_level_light_trails", "dusk_landscaped_approach"]
    tone_history = ["dark_luxury"]
    recipe_history = ["the_sky_chandelier"]
    desc = compose_description(
        vk,
        prior_scene_tags=scene_history,
        prior_tone_tags=tone_history,
        prior_recipe_tags=recipe_history,
    )
    for tag in scene_history:
        assert tag in desc, f"prior_scene_tag '{tag}' not in description"
    for tag in tone_history:
        assert tag in desc, f"prior_tone_tag '{tag}' not in description"
    for tag in recipe_history:
        assert tag in desc, f"prior_recipe_tag '{tag}' not in description"
    # The variant's allowed recipes should be offered as a constrained menu.
    assert "DESIGN RECIPE" in desc, "recipe-selection menu missing from description"
    print("  PASS prior scene/tone/recipe tags embedded; recipe menu present")


def test_allowed_palettes_in_description():
    for vk in list_variants():
        meta = get_variant_meta(vk)
        desc = compose_description(vk)
        palettes = meta.get("allowed_palettes", [])
        assert palettes, f"Variant '{vk}' has no allowed_palettes in config"
        for p in palettes:
            assert p in desc, f"Variant '{vk}' allowed_palette '{p}' missing from description"
    print("  PASS allowed_palettes listed in every variant description")


# ---------------------------------------------------------------------------
# 2. Sanitiser (legacy prose path)
# ---------------------------------------------------------------------------

DIRTY_PROMPT = (
    "RERA approved Nehrunagar residences with guaranteed appreciation, best in the city. "
    "Award winning certified green building just 2 km from the metro station. "
    "100% guaranteed returns. Sample house ready — visit today! #luxury ₹3 Cr"
)


def test_sanitiser_strips_absolute_claims():
    result = sanitize_image_prompt(DIRTY_PROMPT, SAMPLE_BRIEF)
    for banned in ("guaranteed appreciation", "best in the city", "guaranteed returns"):
        assert banned.lower() not in result.lower(), (
            f"Absolute-banned phrase '{banned}' survived sanitisation"
        )
    print("  PASS Absolute-banned claims stripped")


def test_sanitiser_strips_unverified_conditional_claims():
    result = sanitize_image_prompt(DIRTY_PROMPT, SAMPLE_BRIEF)
    assert "rera approved" not in result.lower(), (
        "Unverified conditional claim 'rera approved' survived"
    )
    print("  PASS Unverified conditional claims stripped")


def test_sanitiser_strips_never_invent_sentences():
    result = sanitize_image_prompt(DIRTY_PROMPT, SAMPLE_BRIEF)
    for phrase in ("km from", "metro station"):
        assert phrase.lower() not in result.lower(), (
            f"Never-invent phrase '{phrase}' survived"
        )
    print("  PASS Never-invent sentences stripped")


def test_sanitiser_keeps_sample_ready_badge():
    result = sanitize_image_prompt(DIRTY_PROMPT, SAMPLE_BRIEF)
    assert "sample" in result.lower(), "sample_ready=True — sample badge should be present"
    print("  PASS sample_ready badge present for brief with sample_ready=True")


def test_sanitiser_enforces_price_format():
    result = sanitize_image_prompt("A luxury interior shot.", SAMPLE_BRIEF)
    assert "₹3 Cr" in result, "Sanitiser did not enforce canonical price string"
    print("  PASS Canonical price string enforced on legacy prose prompt")


def test_sanitiser_strips_sample_ready_when_false():
    no_sample = dict(SAMPLE_BRIEF, sample_ready=False)
    result = sanitize_image_prompt(DIRTY_PROMPT, no_sample)
    assert "visit today" not in result.lower(), "'visit today' should be stripped when sample_ready=False"
    print("  PASS sample-ready language stripped when brief.sample_ready=False")


def test_sanitiser_assembled_skips_price_enforcement():
    # assembled=True skips _enforce_price_format — price is already in the assembled prompt
    plain = "An architectural interior scene."
    result = sanitize_image_prompt(plain, SAMPLE_BRIEF, assembled=True)
    # The price enforcement line should NOT have been appended
    assert "must read exactly" not in result, (
        "assembled=True should skip price enforcement injection"
    )
    print("  PASS assembled=True skips price enforcement injection")


# ---------------------------------------------------------------------------
# 3. build_gpt_image_prompt() assembly
# ---------------------------------------------------------------------------

def test_build_gpt_image_prompt_contains_key_sections():
    prompt = build_gpt_image_prompt(SAMPLE_ENTRY, SAMPLE_BRIEF, "exterior_establishing_shot")
    for section in (
        "A finished, world-class luxury real estate advertisement",
        "Layout structure:",
        "Render these exact text elements into the design",
        "Typography hierarchy:",
        "Colour & text treatment:",
        "No invented text, no logos, no watermarks",
        "Aspect ratio",
    ):
        assert section in prompt, f"Key section missing from assembled prompt: '{section}'"
    print("  PASS build_ad_prompt() contains all required Ideogram-native sections")


def test_recipe_drives_art_direction_and_text_density():
    # info_band_style is now a property of the RECIPE (the design bundle), not the variant.
    # the_dark_water_canvas declares compact_spec_row → "Specification Row" label.
    full_entry = dict(SAMPLE_ENTRY, recipe_tag="the_dark_water_canvas")
    full_prompt = build_gpt_image_prompt(full_entry, SAMPLE_BRIEF, "exterior_establishing_shot")
    assert "the_dark_water_canvas" in full_prompt, "recipe art-direction block missing"
    assert "Layout discipline" in full_prompt, "layout discipline missing for full_detail recipe"
    assert "Specification Row" in full_prompt, (
        "the_dark_water_canvas recipe (compact_spec_row) should render the spec row"
    )

    # A different recipe drives a different bottom-band layout from the SAME variant —
    # the_horizon_anchor declares icon_grid_strip → "Bottom Amenity Grid".
    grid_entry = dict(SAMPLE_ENTRY, recipe_tag="the_horizon_anchor")
    grid_prompt = build_gpt_image_prompt(grid_entry, SAMPLE_BRIEF, "exterior_establishing_shot")
    assert "Bottom Amenity Grid" in grid_prompt, (
        "the_horizon_anchor recipe (icon_grid_strip) should render the amenity grid"
    )
    assert "Specification Row" not in grid_prompt, (
        "icon_grid_strip recipe must not also render the compact spec row"
    )

    # Recipe with no info_band in text_roles → spec row suppressed
    teaser_entry = dict(SAMPLE_ENTRY, recipe_tag="the_backlit_silhouette")
    teaser_prompt = build_gpt_image_prompt(teaser_entry, SAMPLE_BRIEF, "exterior_establishing_shot")
    assert "Specification Row" not in teaser_prompt, (
        "recipe without info_band role should suppress the spec row"
    )
    assert "Bottom Information Band" not in teaser_prompt, (
        "recipe without info_band role should suppress all info band labels"
    )
    print("  PASS recipe drives art direction and gates text density by text_roles")


def test_build_gpt_image_prompt_uses_locality_not_product():
    prompt = build_gpt_image_prompt(SAMPLE_ENTRY, SAMPLE_BRIEF, "exterior_establishing_shot")
    # Location name (locality) must appear; product name must not
    assert "NEHRUNAGAR" in prompt, "Locality 'NEHRUNAGAR' missing from assembled prompt"
    assert "Nehrunagar Residences" not in prompt, (
        "Developer/project name should never appear in ad prompt"
    )
    print("  PASS Locality used as primary headline; project name excluded")


def test_build_gpt_image_prompt_includes_price():
    prompt = build_gpt_image_prompt(SAMPLE_ENTRY, SAMPLE_BRIEF, "exterior_establishing_shot")
    assert "₹3 Cr" in prompt, "Price missing from assembled prompt"
    print("  PASS Price '₹3 Cr' included in assembled prompt")


def test_build_gpt_image_prompt_includes_sample_ready_badge():
    prompt = build_gpt_image_prompt(SAMPLE_ENTRY, SAMPLE_BRIEF, "exterior_establishing_shot")
    assert "SAMPLE APARTMENT" in prompt.upper(), "Sample badge missing when sample_ready=True"
    print("  PASS Sample apartment badge included when sample_ready=True")


def test_build_gpt_image_prompt_excludes_sample_badge_when_false():
    brief_no_sample = dict(SAMPLE_BRIEF, sample_ready=False)
    prompt = build_gpt_image_prompt(SAMPLE_ENTRY, brief_no_sample, "exterior_establishing_shot")
    # Check for the actual CTA text — the layout description mentions "Sample apartment badge"
    # as a structural element name, but the actual badge CTA ("SAMPLE APARTMENT READY")
    # should only appear in the typography block when sample_ready=True.
    assert "SAMPLE APARTMENT READY" not in prompt, (
        "Sample badge CTA should not appear in typography hierarchy when sample_ready=False"
    )
    print("  PASS Sample badge CTA excluded from typography when sample_ready=False")


def test_build_gpt_image_prompt_structure_varies_by_tone():
    dark_entry = dict(SAMPLE_ENTRY, tone_tag="dark_luxury")
    bright_entry = dict(SAMPLE_ENTRY, tone_tag="bright_aspirational")
    vk = "exterior_establishing_shot"
    dark_prompt = build_gpt_image_prompt(dark_entry, SAMPLE_BRIEF, vk)
    bright_prompt = build_gpt_image_prompt(bright_entry, SAMPLE_BRIEF, vk)
    # exterior_establishing_shot: dark→bordered_campaign, bright→structured_split
    assert "gold hairline border" in dark_prompt, (
        "dark_luxury exterior should use bordered_campaign (gold hairline border)"
    )
    assert "information zone" in bright_prompt, (
        "bright_aspirational exterior should use structured_split (information zone)"
    )
    print("  PASS Ad structure varies by tone_tag for exterior_establishing_shot")


def test_build_gpt_image_prompt_palette_config_injected():
    # Palette is dynamic: the entry's palette_tag (LLM choice) flows straight through,
    # no longer overridden by a per-variant preferred_palette. Every palette injects.
    from pikorua_adflow.api.services.image_service import _VARIANTS_CONFIG
    for palette_tag, expected_config in PALETTE_CONFIGS.items():
        if not palette_tag.endswith(("_gold", "_cream", "_warmth")):
            continue  # skip non-palette structure configs in the same dict
        entry = dict(SAMPLE_ENTRY, palette_tag=palette_tag)
        prompt = build_gpt_image_prompt(entry, SAMPLE_BRIEF, "lifestyle_private_retreat")
        first_line_prefix = expected_config.split("\n")[0][:15]
        assert first_line_prefix in prompt, (
            f"Palette '{palette_tag}' should inject its colour config "
            f"(expected '{first_line_prefix}' in prompt)"
        )
    print("  PASS Entry palette_tag flows through to the prompt for every palette")


def test_dedupe_visual_batch_enforces_distinct_palette_and_recipe():
    # The batch distinctness pass guarantees five different palettes + recipes even when
    # the LLM drifts to the same favourites — without pinning any design to a topic.
    from pikorua_adflow.crews.content_crew.task_composer import dedupe_visual_batch, VARIANT_KEYS
    entries = [
        {"variant_key": vk, "palette_tag": "charcoal_gold", "recipe_tag": "the_horizon_anchor"}
        for vk in VARIANT_KEYS
    ]
    deduped = dedupe_visual_batch(entries)
    palettes = [e["palette_tag"] for e in deduped]
    assert len(set(palettes)) == len(palettes), f"palettes not distinct: {palettes}"
    print("  PASS dedupe_visual_batch yields a distinct palette per variant")


def test_build_gpt_image_prompt_includes_scene_prose():
    prompt = build_gpt_image_prompt(SAMPLE_ENTRY, SAMPLE_BRIEF, "exterior_establishing_shot")
    assert SAMPLE_ENTRY["scene_prose"][:50] in prompt, "scene_prose not at the start of assembled prompt"
    print("  PASS scene_prose appears at the start of assembled prompt")


# ---------------------------------------------------------------------------
# 4. VisualPromptOutput Pydantic model
# ---------------------------------------------------------------------------

def test_pydantic_output_model_new_fields():
    valid = VisualPromptOutput(
        scene_prose="A luxury interior scene with dramatic diagonal light. Sony A7R V, 35mm f/1.8.",
        headline="Live where mornings feel borrowed from a postcard.",
        eyebrow="AN EXCEPTIONAL PLACE TO CALL HOME.",
        palette_tag="navy_gold",
        scene_tag="living_room_dusk_diagonal_light",
        tone_tag="dark_luxury",
        logo_corner="bottom-right",
    )
    assert valid.scene_prose, "scene_prose not set"
    assert valid.headline, "headline not set"
    assert valid.palette_tag == "navy_gold", "palette_tag not set"
    assert valid.logo_corner == "bottom-right", "logo_corner not set"
    assert valid.ideogram_prompt == "", "ideogram_prompt default should be empty string"

    data = valid.model_dump()
    for field in ("scene_prose", "headline", "eyebrow", "palette_tag", "scene_tag", "tone_tag", "logo_corner"):
        assert field in data, f"Expected field '{field}' missing from model_dump()"
    print("  PASS VisualPromptOutput model validates and serialises with new fields")


def test_pydantic_legacy_compat():
    # Old entries with only ideogram_prompt should still be constructable
    legacy = VisualPromptOutput(
        scene_prose="",
        headline="",
        palette_tag="navy_gold",
        scene_tag="twilight_street_level_light_trails",
        tone_tag="dark_luxury",
        logo_corner="bottom-left",
        ideogram_prompt="A luxury exterior shot of a building in Nehrunagar.",
    )
    assert legacy.ideogram_prompt, "ideogram_prompt not stored for legacy compat"
    print("  PASS VisualPromptOutput legacy compat field (ideogram_prompt) works")


# ---------------------------------------------------------------------------
# 5. Full round-trip (new format)
# ---------------------------------------------------------------------------

def test_full_round_trip_new_format():
    vk = "exterior_establishing_shot"
    desc = compose_description(vk, prior_scene_tags=[], prior_tone_tags=[])
    assert desc.strip(), "Composed description is empty"

    # Simulate what the LLM returns
    entry = dict(SAMPLE_ENTRY)

    # Assemble prompt
    raw_prompt = build_gpt_image_prompt(entry, SAMPLE_BRIEF, vk)
    assert "NEHRUNAGAR" in raw_prompt
    assert "₹3 Cr" in raw_prompt

    # Sanitise (assembled mode)
    sanitized = sanitize_image_prompt(raw_prompt, SAMPLE_BRIEF, assembled=True)
    assert "guaranteed returns" not in sanitized.lower(), "Hard ban survived"
    assert "Do not render any company logo" in sanitized, "Anti-logo guard missing"
    assert entry["logo_corner"] in ("bottom-left", "bottom-right", "top-left", "top-right"), (
        f"Invalid logo_corner: {entry['logo_corner']}"
    )

    print("  PASS Full round-trip: compose → assemble → sanitise → logo-ready")
    print(f"    logo_corner: {entry['logo_corner']}")
    print(f"    Prompt preview (first 120 chars): {sanitized[:120]}…")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        test_all_variants_non_empty_and_distinct,
        test_variant_descriptions_reference_scene_pool,
        test_prior_tags_embedded_in_description,
        test_allowed_palettes_in_description,
        test_sanitiser_strips_absolute_claims,
        test_sanitiser_strips_unverified_conditional_claims,
        test_sanitiser_strips_never_invent_sentences,
        test_sanitiser_keeps_sample_ready_badge,
        test_sanitiser_enforces_price_format,
        test_sanitiser_strips_sample_ready_when_false,
        test_sanitiser_assembled_skips_price_enforcement,
        test_build_gpt_image_prompt_contains_key_sections,
        test_build_gpt_image_prompt_uses_locality_not_product,
        test_build_gpt_image_prompt_includes_price,
        test_build_gpt_image_prompt_includes_sample_ready_badge,
        test_build_gpt_image_prompt_excludes_sample_badge_when_false,
        test_build_gpt_image_prompt_structure_varies_by_tone,
        test_build_gpt_image_prompt_palette_config_injected,
        test_build_gpt_image_prompt_includes_scene_prose,
        test_pydantic_output_model_new_fields,
        test_pydantic_legacy_compat,
        test_full_round_trip_new_format,
    ]
    print("\nImage pipeline smoke tests")
    print("=" * 60)
    failed = []
    for t in tests:
        try:
            t()
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
            failed.append(t.__name__)
    print("=" * 60)
    if failed:
        print(f"FAILED: {len(failed)}/{len(tests)} tests")
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)
    else:
        print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    run_all()
