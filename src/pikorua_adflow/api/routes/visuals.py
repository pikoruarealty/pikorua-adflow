"""Banner image generation, prompt editing, and image CRUD routes."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import litellm
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..config import BRAND_LOGO_PATH, REFERENCE_IMAGES_DIR
from ..models import (AssignImagePayload, ImageGenReq, RegeneratePromptPayload,
                      SavePromptPayload)
from ..services import campaign_service as cs
from ..services import image_service as imgs
from ..state import RUNS
from ...crews.content_crew.task_composer import get_variant_meta as _get_variant_meta

router = APIRouter()

_VARIANT_LABELS = {
    "lifestyle_private_retreat": "Lifestyle — Private Retreat",
    "lifestyle_social_home": "Lifestyle — The Social Home",
    "lifestyle_dynamic_a": "Lifestyle — Scene A",
    "lifestyle_dynamic_b": "Lifestyle — Scene B",
    "interior_signature_moment": "Interior Signature Moment",
    "exterior_establishing_shot": "Exterior Establishing Shot",
    # legacy keys kept for runs generated before the variant restructure
    "lifestyle_city_connection": "Lifestyle — The Address",
    "architectural_perspective": "Architectural Perspective",
    "lifestyle_moment": "Lifestyle Moment",
    "iconic_representation": "Iconic Representation",
}

_OPT_IN_VARIANTS = {"exterior_establishing_shot"}

_LEGACY_VARIANT_KEYS = [
    "lifestyle_private_retreat",
    "lifestyle_social_home",
    "lifestyle_city_connection",
    "exterior_establishing_shot",
    "interior_signature_moment",
]


def _load_visual_prompts(review_folder: Path) -> list[dict]:
    """Load visual_prompts.json; fall back to parsing legacy visual_brief.md."""
    vp_path = review_folder / "visual_prompts.json"
    if vp_path.exists():
        try:
            return json.loads(vp_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Legacy fallback: parse the old visual_brief.md format
    vb_path = review_folder / "visual_brief.md"
    if not vb_path.exists():
        return []
    text = vb_path.read_text(encoding="utf-8")
    entries = []
    for i, block in enumerate(re.split(r"\n---\n", text), 1):
        pq = re.search(r'"([\s\S]+?)"(?:\s*$)', block.strip())
        ptext = pq.group(1).strip() if pq else block.strip().strip('"')
        if not ptext:
            continue
        vk = _LEGACY_VARIANT_KEYS[i - 1] if 1 <= i <= 5 else f"variant_{i}"
        entries.append({
            "variant_key": vk,
            "prompt_num": i,
            "ideogram_prompt": ptext,
            "scene_tag": "",
            "tone_tag": "",
            "logo_corner": "bottom-right",
        })
    return entries


def _brief_for_sanitizer(brief: dict) -> dict:
    """Extract the fields sanitize_image_prompt expects from a run's brief dict."""
    price_cr = str(brief.get("price_cr", "")).strip()
    return {
        "locality": brief.get("locality", ""),
        "city": brief.get("city", ""),
        "property_type": brief.get("property_type", ""),
        "price_cr": price_cr,
        "sample_ready": bool(brief.get("sample_ready", False)),
        "rera_verified": bool(brief.get("rera_verified", False)),
        "verified_awards": bool(brief.get("verified_awards", False)),
        "verified_certifications": bool(brief.get("verified_certifications", False)),
        "verified_landmarks": bool(brief.get("verified_landmarks", False)),
        "config": brief.get("config", ""),
        "usps": brief.get("usps", []),
        "property_name": brief.get("property_name", ""),
    }


@router.post("/generate-images/{run_id}")
def generate_images(run_id: str, payload: ImageGenReq | None = None):
    """Generate images for a completed run (Ideogram backend)."""
    payload = payload or ImageGenReq()
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = RUNS[run_id]
    if run["status"] != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or no review folder.")

    openai_key = os.getenv("OPENAI_API_KEY", "")
    ideogram_key = os.getenv("IDEOGRAM_API_KEY", "")
    replicate_token = os.getenv("REPLICATE_API_TOKEN", "")
    together_key = os.getenv("TOGETHER_API_KEY", "")

    review_folder = Path(run["review_folder"])
    visual_entries = _load_visual_prompts(review_folder)
    if not visual_entries:
        raise HTTPException(
            status_code=400,
            detail="No image prompts found for this run (visual_prompts.json and visual_brief.md are both missing or empty).",
        )

    images_dir = review_folder / "images"
    images_dir.mkdir(exist_ok=True)

    alongside_set = {p for p in (payload.alongside or []) if 1 <= p <= len(visual_entries)}
    selected = {p for p in (payload.prompts or []) if 1 <= p <= len(visual_entries)}
    explicit = bool(selected)
    if not explicit:
        # Generate all — exclude opt_in variants unless the user explicitly chose them
        selected = {
            e["prompt_num"]
            for e in visual_entries
            if e.get("variant_key", "") not in _OPT_IN_VARIANTS
        }

    brief = run.get("brief", {})
    sample_ready = payload.sample_ready or bool(brief.get("sample_ready", False))
    sanitizer_brief = _brief_for_sanitizer(brief)
    sanitizer_brief["sample_ready"] = sample_ready  # allow payload override

    try:
        gen_eff_meta = cs.effective_meta(review_folder)
    except Exception:
        gen_eff_meta = {}

    saved_edits = cs.load_edits(review_folder)

    results = []
    errors = []
    for entry in visual_entries:
        i = entry.get("prompt_num", 0)
        is_alongside = i in alongside_set
        if i not in selected and not is_alongside:
            continue
        if is_alongside:
            k = 2
            while (images_dir / f"image_{i}_v{k}.png").exists():
                k += 1
            out_path = images_dir / f"image_{i}_v{k}.png"
        else:
            out_path = images_dir / f"image_{i}.png"
        if out_path.exists() and not explicit and not is_alongside:
            results.append({"prompt": i, "status": "already_exists", "file": str(out_path)})
            continue

        if not ideogram_key:
            errors.append({
                "prompt": i, "backend": "none", "fixable": False,
                "error": (
                    "Ideogram is not connected yet. Add an IDEOGRAM_API_KEY "
                    "to generate images."
                ),
            })
            continue

        speed = payload.speeds.get(i) or payload.speed
        aspect = payload.ratios.get(i) or payload.ratio
        backend = "ideogram"

        # Prefer custom prompt override → user-saved edit → AI-generated prompt
        custom_or_saved = (
            payload.custom_prompts.get(i)
            or saved_edits.get("prompt_overrides", {}).get(str(i))
        )
        entry_brief = dict(sanitizer_brief)
        variant_key = entry.get("variant_key", "")
        if variant_key:
            try:
                vm = _get_variant_meta(variant_key)
                cta = vm.get("sample_ready_cta")
                if cta:
                    entry_brief["sample_ready_cta"] = cta
            except Exception:
                pass

        if custom_or_saved:
            # User provided an explicit prompt — sanitize it fully (legacy path)
            raw_prompt = custom_or_saved
            sanitized = imgs.sanitize_image_prompt(raw_prompt, entry_brief)
        elif entry.get("scene_prose"):
            # New format: assemble the structured ad brief from the LLM's creative choices.
            # For the exterior variant, prepend any user-supplied building description so
            # the prompt references actual building details rather than inventing them.
            gen_entry = dict(entry)
            if variant_key == "exterior_establishing_shot" and payload.exterior_brief:
                gen_entry["scene_prose"] = (
                    payload.exterior_brief.strip() + " " + gen_entry.get("scene_prose", "")
                ).strip()
            raw_prompt = imgs.build_ad_prompt(gen_entry, entry_brief, variant_key)
            sanitized = imgs.sanitize_image_prompt(raw_prompt, entry_brief, assembled=True)
        else:
            # Legacy format: prose ideogram_prompt stored in visual_prompts.json
            raw_prompt = entry.get("ideogram_prompt", "")
            sanitized = imgs.sanitize_image_prompt(raw_prompt, entry_brief)

        logo_corner = entry.get("logo_corner", "bottom-right")

        try:
            v4_speed = speed if speed in ("TURBO", "DEFAULT") else "DEFAULT"
            img_bytes = imgs.call_ideogram(
                sanitized, ideogram_key, v4_speed, aspect, recipe_tag=entry.get("recipe_tag", "")
            )
            out_path.write_bytes(img_bytes)
            if BRAND_LOGO_PATH.exists():
                try:
                    logo_backup_dir = out_path.parent / ".logo_backup"
                    logo_backup_dir.mkdir(exist_ok=True)
                    import shutil as _shutil
                    _shutil.copy2(out_path, logo_backup_dir / out_path.name)
                    imgs.composite_logo(out_path, BRAND_LOGO_PATH, corner=logo_corner)
                except Exception:
                    pass
            results.append({
                "prompt": i, "status": "generated", "backend": backend,
                "file": str(out_path),
            })
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log(f"Image generation — prompt {i} ({backend})", exc)
            errors.append({
                "prompt": i, "backend": backend,
                "error": friendly["message"], "fixable": friendly["fixable"],
            })

    if payload.custom_prompts:
        edits = cs.load_edits(review_folder)
        overrides = edits.setdefault("prompt_overrides", {})
        for k, v in payload.custom_prompts.items():
            overrides[str(k)] = v
        cs.save_edits(review_folder, edits)

    return {"run_id": run_id, "generated": results, "errors": errors}


@router.post("/save-prompt/{run_id}/{prompt_num}")
def save_prompt(run_id: str, prompt_num: int, payload: SavePromptPayload):
    run = RUNS.get(run_id)
    if not run or not run.get("review_folder"):
        raise HTTPException(status_code=404, detail="Run not found.")
    rf = Path(run["review_folder"])
    edits = cs.load_edits(rf)
    edits.setdefault("prompt_overrides", {})[str(prompt_num)] = payload.text
    cs.save_edits(rf, edits)
    return {"ok": True}


@router.post("/revert-prompt/{run_id}/{prompt_num}")
def revert_prompt(run_id: str, prompt_num: int):
    run = RUNS.get(run_id)
    if not run or not run.get("review_folder"):
        raise HTTPException(status_code=404, detail="Run not found.")
    rf = Path(run["review_folder"])
    edits = cs.load_edits(rf)
    overrides = edits.get("prompt_overrides", {})
    overrides.pop(str(prompt_num), None)
    if overrides:
        edits["prompt_overrides"] = overrides
    else:
        edits.pop("prompt_overrides", None)
    cs.save_edits(rf, edits)
    return {"ok": True}


@router.post("/regenerate-prompt/{run_id}")
async def regenerate_prompt(run_id: str, payload: RegeneratePromptPayload):
    """Rewrite one image-prompt description using the campaign's ad copy and brand rules."""
    run = RUNS.get(run_id)
    if not run or run.get("status") != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or not found.")

    review_folder = Path(run["review_folder"])
    visual_entries = _load_visual_prompts(review_folder)
    n = payload.prompt_num
    entry = next((e for e in visual_entries if e.get("prompt_num") == n), None)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"prompt_num {n} out of range.")

    saved_edits = cs.load_edits(review_folder)
    original_prompt = entry.get("ideogram_prompt", "")
    current_prompt = (
        saved_edits.get("prompt_overrides", {}).get(str(n)) or original_prompt
    )

    variant_key = entry.get("variant_key", "")
    variant_label = _VARIANT_LABELS.get(variant_key, f"Prompt {n}")
    prompt_type = "Social ad creative with text overlay"

    brief = run.get("brief", {})
    property_name = brief.get("property_name", "")
    property_type = brief.get("property_type", "")
    city = brief.get("city", "")
    locality = brief.get("locality", "")
    price_cr = brief.get("price_cr", "")
    standout = brief.get("standout_feature", "")

    eff = cs.effective_meta(review_folder)
    copy_lines = []
    for num in sorted(eff)[:5]:
        c = eff[num]
        copy_lines.append(f'  Variant {num}: headline="{c["headline"]}" / body="{c["body"]}"')
    copy_block = "\n".join(copy_lines) if copy_lines else "  (no copy variants available)"

    # Scene-type rules per prompt number — define WHAT the image shows, not WHERE text goes.
    # Text placement is determined by the composition; the scene rule just enforces the
    # correct visual character for each slot so regenerated prompts don't drift to the
    # wrong shot type (e.g. exterior when interior is expected).
    _zone_rules = {
        1: (
            "SCENE TYPE: Architectural Perspective — camera INSIDE the building. "
            "Interior architecture is the subject: corridor vanishing point, high lobby "
            "ceiling, balcony seen from inside the apartment, glass curtain wall from "
            "the interior side, or staircase geometry. No exterior facade views. "
            "No people, or at most one unidentifiable silhouette for scale. "
            "Mood: precise, confident, editorial. Text anchors wherever the composition "
            "creates a naturally dark or clean area — do not force it to a fixed zone."
        ),
        2: (
            "SCENE TYPE: Lifestyle Moment — one or two people inside the home, candid, "
            "never facing camera, mid one small believable action. No exterior views. "
            "The action and headline must thematically rhyme. Mood: warm aspiration, "
            "a real moment in an exceptional home. Text anchors wherever the composition "
            "creates space — above, beside, or below the figure."
        ),
        3: (
            "SCENE TYPE: Iconic Detail — one hero object or architectural detail, "
            "art-directed like a luxury product ad. Maximum negative space. No people, "
            "no full-room shots. Could be a material macro, a tabletop vignette, or a "
            "signature architectural element. Fewest text elements of the five variants. "
            "Text uses whatever breathing room the composition naturally provides."
        ),
        4: (
            "SCENE TYPE: Exterior Establishing Shot — full building in its urban context, "
            "three-quarter angle (never head-on). Favour blue-hour or twilight: deep indigo "
            "sky, warm interior lights glowing from units, motion-blurred street light trails. "
            "This is the only variant showing the building facade from outside. "
            "Full information stack (location, headline, price, trust badges) distributed "
            "naturally across the composition's dark areas."
        ),
        5: (
            "SCENE TYPE: Interior Signature Moment — empty room, no people. Light quality "
            "and material do the emotional work: dramatic diagonal light across a marble floor, "
            "a dusk cityscape through full-height glazing, one styled object as the only human "
            "touch. The shadow the light creates is the text surface — push it to near-black. "
            "Mood: quiet luxury, the feeling of entering a room that knows it is exceptional."
        ),
    }
    zone_rule = _zone_rules.get(n, _zone_rules[1]).replace("{locality}", locality)

    ref_block = ""
    if REFERENCE_IMAGES_DIR.exists():
        ref_descs = []
        for rp in sorted(REFERENCE_IMAGES_DIR.glob("*")):
            if rp.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            dp = imgs.ref_description_path(rp)
            if dp.exists():
                ref_descs.append(f"  • [{rp.name}] {dp.read_text(encoding='utf-8').strip()}")
        if ref_descs:
            ref_block = (
                "\n\nREFERENCE CREATIVE INSPIRATION:\n"
                "Study these for the visual language of premium advertising — how text "
                "zones relate to photography, typographic container quality, ornamental "
                "detail.  Let them inspire the approach; the actual structure should "
                "emerge from the image's natural composition and campaign data.\n"
                + "\n".join(ref_descs)
            )

    first_headline = next((c["headline"] for c in eff.values() if c.get("headline")), "")

    system_prompt = f"""You are a luxury real-estate ad art director writing image prompts for PIKORUA.
You write prompts that are sent directly to an AI image generator (gpt-image-1).
The prompt must read like a cinematographer's shot brief: scene, light, materials, mood,
and text elements — described as if you are painting the image, not filling a template.

Campaign context:
- Property: {property_name} ({property_type})
- Location: {locality + ", " if locality else ""}{city}
- Price: ₹{price_cr} Cr
- Standout feature: {standout or "not specified"}

Ad copy variants (pick a headline from these):
{copy_block}{ref_block}

VARIANT RULES — slot {n} ({variant_label}):
{zone_rule}

TEXT ELEMENTS (always required — quote the exact strings):
- Location name "{locality}" — largest text, warm gold (#C9A84C), ALL CAPS tracked serif.
  Must read at 300px thumbnail. Anchors wherever the composition creates a dark surface.
- Price "₹{price_cr} Cr" — inside a clearly bounded dark container (rectangle with gold
  border, frosted card, or natural shadow dark enough for 7:1 contrast). Never floating.
- Headline (pick from copy variants above) — elegant serif or fine sans, smaller than location.
- One or two supporting lines (tagline, CTA badge) if the variant allows them — each must
  rest on a dark surface; add a scrim if the photo is not dark enough.
Scale contrast between text tiers is mandatory: location name is 5–8× the size of subtext.

PHOTOREALISM: camera body + lens + ISO, aperture for depth of field, named light colour
temperature and direction, exact material finishes, one natural imperfection (lens flare,
grain, chromatic aberration), asymmetric off-axis composition.

PALETTE: deep navy / charcoal in backing areas, warm gold for dominant text, warm white
for supporting copy. No neon, no cold blues, no pure white panels.

HARD RULES:
· No phone numbers, URLs, sq ft, possession dates, floor counts, RERA numbers.
· No brand names, logos, or wordmarks.
· One corner must be left clean — a logo is composited after generation.
· Do not invent property facts.

Write a single flowing-prose prompt (200–400 words).
No preamble, no labels, no surrounding quotes."""

    user_msg = f"""Rewrite image prompt slot {n} ("{variant_label}" — {prompt_type}).

Current prompt:
{current_prompt}

Rewrite it following all style rules above."""

    model = os.getenv("CREATIVE_MODEL", "gemini/gemini-2.5-flash")
    try:
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.85, max_tokens=600,
        )
        new_prompt = resp.choices[0].message.content.strip().strip('"')
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")

    overrides = saved_edits.setdefault("prompt_overrides", {})
    overrides[str(n)] = new_prompt
    cs.save_edits(review_folder, saved_edits)

    return {"prompt_num": n, "prompt": new_prompt}


# ── Image serving + CRUD ─────────────────────────────────────────────────────
@router.get("/image/{run_id}/{filename}")
def serve_image(run_id: str, filename: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="Run not found.")
    run = RUNS[run_id]
    if not run.get("review_folder"):
        raise HTTPException(status_code=404, detail="No review folder for this run.")
    if not re.fullmatch(r'image_\d+(?:_v\d+)?\.png', filename):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    img_path = Path(run["review_folder"]) / "images" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(content=img_path.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.delete("/image/{run_id}/{fname}")
def delete_generated_image(run_id: str, fname: str):
    if not re.fullmatch(r'image_\d+(?:_v\d+)?\.png', fname):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    run = cs.require_complete(run_id)
    images = Path(run["review_folder"]) / "images"
    target = images / fname
    if not target.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    target.unlink()
    backup = images / ".logo_backup" / fname
    if backup.exists():
        backup.unlink()
    return {"ok": True}


@router.post("/upload-image/{run_id}/{variant}")
async def upload_image(run_id: str, variant: int, request: Request):
    """Replace a version's image with a user upload (raw bytes)."""
    import shutil
    run = cs.require_complete(run_id)
    rf = Path(run["review_folder"])
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="No image data received.")
    if not (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"
            or data[:4] == b"RIFF" or data[:3] == b"GIF"):
        raise HTTPException(status_code=400,
                            detail="File doesn't look like a PNG/JPG/WebP/GIF image.")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 12 MB).")
    images = rf / "images"
    images.mkdir(exist_ok=True)
    target = images / f"image_{variant}.png"
    if target.exists():
        backup_dir = images / ".ai_backup"
        backup_dir.mkdir(exist_ok=True)
        b = backup_dir / f"image_{variant}.png"
        if not b.exists():
            shutil.copy2(target, b)
    target.write_bytes(data)
    return {"ok": True, "variant": variant}


@router.post("/revert-image/{run_id}/{variant}")
def revert_image(run_id: str, variant: int):
    import shutil
    run = cs.require_complete(run_id)
    images = Path(run["review_folder"]) / "images"
    target = images / f"image_{variant}.png"
    backup = images / ".ai_backup" / f"image_{variant}.png"
    if backup.exists():
        shutil.copy2(backup, target)
        return {"ok": True, "restored": True}
    if target.exists():
        target.unlink()
    return {"ok": True, "restored": False}


@router.post("/revert-logo/{run_id}/{prompt_num}")
def revert_logo(run_id: str, prompt_num: int):
    import shutil
    run = cs.require_complete(run_id)
    images = Path(run["review_folder"]) / "images"
    target = images / f"image_{prompt_num}.png"
    backup = images / ".logo_backup" / f"image_{prompt_num}.png"
    if not backup.exists():
        raise HTTPException(status_code=404, detail="No logo backup found for this image.")
    shutil.copy2(backup, target)
    backup.unlink()
    return {"ok": True}


@router.post("/inpaint/{run_id}/{prompt_num}")
async def inpaint_image(run_id: str, prompt_num: int, request: Request):
    """Inpaint a masked region of an existing generated image.

    Accepts multipart/form-data with:
      - mask_png  : PNG file (white = regenerate, black = keep)
      - edit_prompt : plain text describing the change
      - source_file : optional filename of the image to edit (defaults to latest variant)
    Returns the new variant filename.
    """
    ideogram_key = os.getenv("IDEOGRAM_API_KEY", "")
    if not ideogram_key:
        raise HTTPException(status_code=400, detail="IDEOGRAM_API_KEY not configured.")
    run = cs.require_complete(run_id)
    images_dir = Path(run["review_folder"]) / "images"

    form = await request.form()
    edit_prompt = (form.get("edit_prompt") or "").strip()
    if not edit_prompt:
        raise HTTPException(status_code=400, detail="edit_prompt is required.")

    # Resolve source image
    source_file = (form.get("source_file") or "").strip()
    if source_file:
        if not re.fullmatch(r'image_\d+(?:_v\d+)?\.png', source_file):
            raise HTTPException(status_code=400, detail="Invalid source_file.")
        src_path = images_dir / source_file
    else:
        # Use latest variant: prefer highest _vN, fall back to base image
        variants = sorted(
            [f for f in images_dir.glob(f"image_{prompt_num}_v*.png")],
            key=lambda p: int(re.search(r"_v(\d+)", p.name).group(1))
        )
        src_path = variants[-1] if variants else images_dir / f"image_{prompt_num}.png"
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="Source image not found.")

    mask_file = form.get("mask_png")
    if mask_file is None:
        raise HTTPException(status_code=400, detail="mask_png is required.")
    mask_bytes = await mask_file.read()
    image_bytes = src_path.read_bytes()

    # Determine aspect from the run's visual brief (default 4:5)
    aspect = "4x5"

    # INPAINT_MOCK=1 skips the Ideogram API call for UI/flow testing without credits.
    # Returns the edit prompt so you can review what would have been sent to Ideogram.
    if os.getenv("INPAINT_MOCK") == "1":
        return {"mock": True, "prompt_sent": edit_prompt, "prompt_num": prompt_num}

    result_bytes = imgs.call_ideogram_inpaint(
        image_bytes=image_bytes,
        mask_bytes=mask_bytes,
        prompt=edit_prompt,
        key=ideogram_key,
        aspect=aspect,
    )

    # Save as next available variant slot
    k = 2
    while (images_dir / f"image_{prompt_num}_v{k}.png").exists():
        k += 1
    out_path = images_dir / f"image_{prompt_num}_v{k}.png"
    out_path.write_bytes(result_bytes)
    return {"file": out_path.name, "prompt_num": prompt_num}


@router.post("/assign-image/{run_id}/{variant_num}")
def assign_image(run_id: str, variant_num: int, payload: AssignImagePayload):
    run = cs.require_complete(run_id)
    rf = Path(run["review_folder"])
    edits = cs.load_edits(rf)
    m = edits.setdefault("meta", {})
    cur = m.get(str(variant_num), {})
    if payload.image_num is not None:
        cur["image_num"] = str(payload.image_num)
    else:
        cur.pop("image_num", None)
    m[str(variant_num)] = cur
    cs.save_edits(rf, edits)
    return {"ok": True}
