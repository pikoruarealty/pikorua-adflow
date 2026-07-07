"""Banner image generation, prompt editing, and image CRUD routes."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import litellm
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..config import BRAND_LOGO_PATH, REFERENCE_IMAGES_DIR
from ..models import (AssignImagePayload, GenerateRefVariantPayload, ImageGenReq,
                      RegeneratePromptPayload, SavePromptPayload)
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

# New-pipeline baked prompts always open with this string. Used to detect stale old-style
# prose in prompt_overrides so we can ignore it and run the new pipeline instead.
_BAKED_MARKER = "A finished, professionally designed luxury real-estate advertisement"

# Brief fields whose change should invalidate a saved AI-rewrite prompt override —
# these are exactly the facts baked as literal text into the Ideogram prompt (CTA
# presence, cheque flag, price/config, spec-strip source data). A saved override
# is only replayed as-is when its fingerprint still matches the run's current brief;
# otherwise it's stale (e.g. saved before sample_ready/cheque_only was set correctly,
# or before a spec/price edit) and the fresh pipeline is run instead.
_FINGERPRINT_FIELDS = [
    "price_cr", "config", "property_type", "locality", "city",
    "sample_ready", "cheque_only", "rera_verified", "verified_awards",
    "verified_certifications", "verified_landmarks", "standout_feature",
    "buyer_type", "cta",
]

# Any code/library change to the baked-prompt pipeline itself should also invalidate
# previously-trusted overrides — otherwise a bug fix here never reaches images whose
# override happens to still match the brief. Hash the source of the pipeline files
# that shape the actual prompt text; this changes whenever any of them is edited and
# the server restarts, so old overrides fall through to a fresh pipeline run again.
_PIPELINE_FILES = [
    Path(__file__).resolve().parent.parent / "services" / "image" / "baked_prompt.py",
    Path(__file__).resolve().parent.parent / "services" / "image" / "art_director.py",
    Path(__file__).resolve().parent.parent / "services" / "image" / "libraries.py",
    Path(__file__).resolve().parent.parent / "services" / "image" / "sanitizer.py",
    Path(__file__).resolve().parent.parent / "services" / "image" / "libraries" / "design_grammar.yaml",
]


def _pipeline_version() -> str:
    h = hashlib.sha1()
    for p in _PIPELINE_FILES:
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


_PIPELINE_VERSION = _pipeline_version()


def _brief_fingerprint(brief: dict) -> str:
    sig = {k: brief.get(k) for k in _FINGERPRINT_FIELDS}
    sig["_pipeline_version"] = _PIPELINE_VERSION
    return hashlib.sha1(json.dumps(sig, sort_keys=True, default=str).encode()).hexdigest()[:16]


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
    explicit = bool(selected) or bool(alongside_set)
    if not selected and not alongside_set:
        # Neither prompts nor alongside given — generate all, excluding opt_in variants
        # unless the user explicitly chose them.
        selected = {
            e["prompt_num"]
            for e in visual_entries
            if e.get("variant_key", "") not in _OPT_IN_VARIANTS
        }

    brief = run.get("brief", {})
    sample_ready = payload.sample_ready or bool(brief.get("sample_ready", False))
    sanitizer_brief = _brief_for_sanitizer(brief)
    sanitizer_brief["sample_ready"] = sample_ready  # allow payload override

    # Amenity → variant distribution: give each variant one distinct real feature to
    # build its scene around, so the ads reflect THIS property (pool, clubhouse, towers,
    # grand living room…) instead of generic interchangeable interiors. Computed once
    # for the whole batch and persisted onto each entry as `scene_features`, so even a
    # single-variant "Generate" click renders the feature assigned to that slot.
    amenities = [a for a in (brief.get("amenities") or []) if str(a).strip()]
    if amenities and any("scene_features" not in e for e in visual_entries):
        try:
            from ..services.image import scene_features as _sf
            batch_keys = [
                e.get("variant_key", "") for e in visual_entries if e.get("variant_key")
            ]
            mapping = _sf.distribute(amenities, batch_keys, brief)
            if mapping:
                for e in visual_entries:
                    if "scene_features" not in e:
                        e["scene_features"] = mapping.get(e.get("variant_key", ""), "")
                (review_folder / "visual_prompts.json").write_text(
                    json.dumps(visual_entries, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        except Exception as exc:
            print(f"[generate-images] amenity distribution skipped: {exc}")

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

        entry_brief = dict(sanitizer_brief)
        variant_key = entry.get("variant_key", "")
        if variant_key:
            try:
                vm = _get_variant_meta(variant_key)
                cta_hint = vm.get("sample_ready_cta")
                # Only inject the variant-specific CTA text when sample_ready is
                # actually checked — same gate as the cheque_only flag.
                if cta_hint and sample_ready:
                    entry_brief["sample_ready_cta"] = cta_hint
            except Exception:
                pass

        custom_from_payload = payload.custom_prompts.get(i)
        saved_override = saved_edits.get("prompt_overrides", {}).get(str(i))
        override_meta = saved_edits.get("prompt_override_meta", {}).get(str(i), {})
        # A saved override is only trusted as-is when it's either an explicit manual
        # edit (user typed/saved it — always honoured), or an AI-rewrite override whose
        # fingerprint still matches the run's current brief. Anything else — including
        # overrides saved before this fingerprinting existed — is treated as stale and
        # falls through to a fresh pipeline run instead of being resent verbatim.
        is_trusted_override = override_meta.get("source") == "manual" or (
            override_meta.get("source") == "ai_rewrite"
            and override_meta.get("fingerprint") == _brief_fingerprint(brief)
        )
        use_baked_override = bool(
            saved_override and saved_override.startswith(_BAKED_MARKER) and is_trusted_override
        )

        v4_speed = speed if speed in ("TURBO", "DEFAULT", "QUALITY") else "QUALITY"

        if custom_from_payload or use_baked_override:
            # User-typed custom prompt OR a new-pipeline baked prompt saved by
            # /regenerate-prompt — send straight to Ideogram, no further processing.
            direct_prompt = custom_from_payload or saved_override
            from ..services.image.ideogram_client import call as _ideo_call
            try:
                img_bytes = _ideo_call(direct_prompt, ideogram_key, speed=v4_speed, aspect=aspect)
            except Exception as exc:
                from pikorua_adflow.tools.errors import explain_and_log
                friendly = explain_and_log(f"Image generation — prompt {i} (direct)", exc)
                errors.append({
                    "prompt": i, "backend": backend,
                    "error": friendly["message"], "fixable": friendly["fixable"],
                })
                continue

        else:
            # New baked-prompt pipeline: BriefModel → ArtDirector → AdSpec → baked_prompt → Ideogram.
            # Runs when there is no override, or when the saved override is stale old-style prose.
            from ..services.image.brief_model import BriefModel
            from ..services.image.art_director import build_ad_spec, plan_batch_diversity
            from ..services.image import pipeline as _pipeline

            eff_copy = gen_eff_meta.get(i) or {}
            headline = eff_copy.get("headline", "")
            default_cta = "Sample Flat Ready" if sample_ready else ""
            cta = entry_brief.get("sample_ready_cta") or default_cta

            brief_model = BriefModel.from_brief(
                brief,
                headline=headline,
                cta=cta,
                sample_ready_override=sample_ready,
            )
            brief_model.has_logo = BRAND_LOGO_PATH.exists()

            # Scene direction for this slot: the amenity feature assigned to this variant
            # (so the photo shows a real, on-brief feature, not a generic room). For the
            # exterior variant the user's typed description wins when provided.
            scene_note = (entry.get("scene_features") or "").strip()
            if variant_key == "exterior_establishing_shot" and payload.exterior_brief:
                scene_note = payload.exterior_brief.strip()
            exterior_note = scene_note
            # The assigned scene note (esp. a typed exterior brief) may itself name real
            # storey/tower/sq-ft counts; add it to the model's amenities so the sanitizer
            # licenses those figures instead of stripping them as hallucinations.
            if scene_note:
                brief_model.amenities = [*brief_model.amenities, scene_note]

            # Sibling-aware diversity: look at every OTHER slot's already-committed
            # (skeleton, palette) — whether from an earlier session or an earlier
            # iteration of this same batch — so no more than 2 variants ever share a
            # skeleton, and if they do, they get different palettes.
            sibling_pairs = [
                (e.get("skeleton", ""), e.get("palette_id", ""))
                for e in visual_entries
                if e.get("prompt_num") != i and e.get("skeleton")
            ]
            diversity = plan_batch_diversity(1, existing=sibling_pairs)[0]

            spec = build_ad_spec(
                variant_key=variant_key or "lifestyle_private_retreat",
                prompt_num=i,
                brief=brief_model,
                extra_scene_note=exterior_note,
                force_skeleton=diversity["skeleton"],
                force_palette=diversity["palette"],
            )

            # Persist the AdSpec that is actually about to be rendered so the
            # edit-prompt view reflects the real prompt (no need to re-run "AI
            # rewrite" after generating, and it survives image deletion).
            new_entry = spec.to_entry()
            new_entry["variant_key"] = variant_key
            new_entry["prompt_num"] = i
            # Keep the assigned amenity feature on the entry so regeneration reuses it.
            if entry.get("scene_features"):
                new_entry["scene_features"] = entry["scene_features"]
            existing_idx = next(
                (j for j, e in enumerate(visual_entries) if e.get("prompt_num") == i), None
            )
            if existing_idx is not None:
                visual_entries[existing_idx] = new_entry
            else:
                visual_entries.append(new_entry)
            vp_path = review_folder / "visual_prompts.json"
            vp_path.write_text(
                json.dumps(visual_entries, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            try:
                img_bytes = _pipeline.generate_one(
                    spec, brief_model, ideogram_key,
                    speed=v4_speed, aspect=aspect, mode="baked",
                )
            except Exception as exc:
                from pikorua_adflow.tools.errors import explain_and_log
                friendly = explain_and_log(f"Image generation — prompt {i} ({backend})", exc)
                errors.append({
                    "prompt": i, "backend": backend,
                    "error": friendly["message"], "fixable": friendly["fixable"],
                })
                continue

        logo_corner = entry.get("logo_corner", "bottom-right")

        out_path.write_bytes(img_bytes)
        if BRAND_LOGO_PATH.exists():
            try:
                import shutil as _shutil
                logo_backup_dir = out_path.parent / ".logo_backup"
                logo_backup_dir.mkdir(exist_ok=True)
                _shutil.copy2(out_path, logo_backup_dir / out_path.name)
                imgs.composite_logo(out_path, BRAND_LOGO_PATH, corner=logo_corner)
            except Exception:
                pass
        results.append({
            "prompt": i, "status": "generated", "backend": backend,
            "file": str(out_path),
        })

    if payload.custom_prompts:
        edits = cs.load_edits(review_folder)
        overrides = edits.setdefault("prompt_overrides", {})
        meta = edits.setdefault("prompt_override_meta", {})
        for k, v in payload.custom_prompts.items():
            overrides[str(k)] = v
            meta[str(k)] = {"source": "manual"}
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
    edits.setdefault("prompt_override_meta", {})[str(prompt_num)] = {"source": "manual"}
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
    meta = edits.get("prompt_override_meta", {})
    meta.pop(str(prompt_num), None)
    if meta:
        edits["prompt_override_meta"] = meta
    else:
        edits.pop("prompt_override_meta", None)
    cs.save_edits(rf, edits)
    return {"ok": True}


@router.post("/regenerate-prompt/{run_id}")
async def regenerate_prompt(run_id: str, payload: RegeneratePromptPayload):
    """Regenerate one image prompt using the new baked-prompt pipeline (fresh art direction)."""
    from ..services.image.brief_model import BriefModel
    from ..services.image.art_director import build_ad_spec
    from ..services.image import baked_prompt as _baked_prompt

    run = RUNS.get(run_id)
    if not run or run.get("status") != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or not found.")

    review_folder = Path(run["review_folder"])
    visual_entries = _load_visual_prompts(review_folder)
    n = payload.prompt_num
    entry = next((e for e in visual_entries if e.get("prompt_num") == n), None)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"prompt_num {n} out of range.")

    brief = run.get("brief", {})
    variant_key = entry.get("variant_key", "") or "lifestyle_private_retreat"
    sample_ready = bool(brief.get("sample_ready", False))

    try:
        gen_eff_meta = cs.effective_meta(review_folder)
    except Exception:
        gen_eff_meta = {}

    eff_copy = gen_eff_meta.get(n) or {}
    headline = eff_copy.get("headline", "")
    default_cta = "Sample Flat Ready" if sample_ready else ""
    cta = default_cta

    brief_model = BriefModel.from_brief(
        brief, headline=headline, cta=cta, sample_ready_override=sample_ready
    )
    brief_model.has_logo = BRAND_LOGO_PATH.exists()

    try:
        spec = build_ad_spec(
            variant_key=variant_key,
            prompt_num=n,
            brief=brief_model,
        )
        new_prompt = _baked_prompt.build(spec, brief_model)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Art director call failed: {exc}")

    saved_edits = cs.load_edits(review_folder)
    overrides = saved_edits.setdefault("prompt_overrides", {})
    overrides[str(n)] = new_prompt
    meta = saved_edits.setdefault("prompt_override_meta", {})
    meta[str(n)] = {"source": "ai_rewrite", "fingerprint": _brief_fingerprint(brief)}
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
    if not re.fullmatch(r'image_(?:\d+|r\d+)(?:_v\d+)?\.png', filename):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    img_path = Path(run["review_folder"]) / "images" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    # The frontend cache-busts with the file's mtime (?t=<mtime> via imgUrl), so a
    # URL uniquely identifies one version of the file — safe to cache hard. The old
    # no-store header forced EVERY image to re-download on every tab re-render,
    # which is what made a single generate/assign visibly reload all other images.
    return Response(content=img_path.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.delete("/image/{run_id}/{fname}")
def delete_generated_image(run_id: str, fname: str):
    m = re.fullmatch(r'image_(\d+|r\d+)(?:_v\d+)?\.png', fname)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    images = review_folder / "images"
    target = images / fname
    if not target.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    target.unlink()
    backup = images / ".logo_backup" / fname
    if backup.exists():
        backup.unlink()

    # Reference-variant images (image_r{n}...) aren't tied to a visual_prompts.json
    # slot — nothing to reset there.
    slot = m.group(1)
    if slot.isdigit():
        prompt_num = int(slot)
        remaining = list(images.glob(f"image_{prompt_num}.png")) + \
            list(images.glob(f"image_{prompt_num}_v*.png"))
        if not remaining:
            visual_entries = _load_visual_prompts(review_folder)
            idx = next(
                (j for j, e in enumerate(visual_entries) if e.get("prompt_num") == prompt_num),
                None,
            )
            if idx is not None:
                visual_entries[idx] = {
                    "variant_key": visual_entries[idx].get("variant_key", ""),
                    "prompt_num": prompt_num,
                }
                vp_path = review_folder / "visual_prompts.json"
                vp_path.write_text(
                    json.dumps(visual_entries, indent=2, ensure_ascii=False), encoding="utf-8"
                )

            edits = cs.load_edits(review_folder)
            overrides = edits.get("prompt_overrides", {})
            meta = edits.get("prompt_override_meta", {})
            if overrides.pop(str(prompt_num), None) is not None or meta.pop(str(prompt_num), None) is not None:
                if overrides:
                    edits["prompt_overrides"] = overrides
                else:
                    edits.pop("prompt_overrides", None)
                if meta:
                    edits["prompt_override_meta"] = meta
                else:
                    edits.pop("prompt_override_meta", None)
                cs.save_edits(review_folder, edits)

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


@router.post("/revert-logo/{run_id}/{prompt_slug}")
def revert_logo(run_id: str, prompt_slug: str):
    import shutil
    if not re.fullmatch(r'\d+|r\d+', prompt_slug):
        raise HTTPException(status_code=422, detail=f"Invalid prompt_slug: {prompt_slug!r}")
    run = cs.require_complete(run_id)
    images = Path(run["review_folder"]) / "images"
    target = images / f"image_{prompt_slug}.png"
    backup = images / ".logo_backup" / f"image_{prompt_slug}.png"
    if not backup.exists():
        raise HTTPException(status_code=404, detail="No logo backup found for this image.")
    shutil.copy2(backup, target)
    backup.unlink()
    return {"ok": True}


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


@router.post("/generate-reference-variant/{run_id}")
def generate_reference_variant(run_id: str, payload: GenerateRefVariantPayload):
    """Generate an image from a reference creative using one of three modes:

    mode="text_only" (default):
      Edits the reference image in place (OpenAI gpt-image-1) — same photo, same scene,
      same elements; only headline/price/CTA copy changes. Tightest, closest to reference.

    mode="change_scene":
      Extracts the reference image's ad element layout (where location name, price,
      badge, footer sit) via vision LLM and uses it as composition_notes. Generates a
      brand-new photographic scene (gpt-image-1 text-to-image) using scene_variant from
      our standard pipeline. Photography is new; text element positions mirror the reference.

    mode="change_elements":
      Edits the reference image in place (gpt-image-1), keeping the photo/scene but
      varying secondary ad elements (badge, CTA style, accent colour, footer treatment) —
      for visually distinct variants from one reference without changing the scene.

    Ideogram is not used for this feature — it struggles to preserve exact ad copy when
    editing an existing image. All three modes call OpenAI's gpt-image-1.

    Set REMIX_MOCK=1 to return prompts/instructions without calling OpenAI (for testing).
    Set custom_prompt to override the auto-assembled prompt/instruction.
    """
    import datetime
    import shutil

    from ..services.image import openai_client

    openai_key = os.getenv("OPENAI_API_KEY", "")
    run = cs.require_complete(run_id)
    review_folder = Path(run["review_folder"])
    images_dir = review_folder / "images"
    images_dir.mkdir(exist_ok=True)

    # Validate mode
    if payload.mode not in ("text_only", "change_scene", "change_elements"):
        raise HTTPException(
            status_code=400,
            detail="mode must be 'text_only', 'change_scene', or 'change_elements'.",
        )

    # Validate the reference image exists
    safe_name = re.sub(r"[^\w.\-]", "_", payload.reference_filename)
    ref_path = REFERENCE_IMAGES_DIR / safe_name
    if not ref_path.exists():
        raise HTTPException(status_code=404, detail=f"Reference image '{safe_name}' not found.")

    brief = run.get("brief", {})
    eff = cs.effective_meta(review_folder)
    first_headline = next((c["headline"] for c in eff.values() if c.get("headline")), "")
    sample_ready = bool(brief.get("sample_ready", False))
    default_cta = "Sample Flat Ready" if sample_ready else ""

    # Find next available image_r{k}.png slot
    k = 1
    while (images_dir / f"image_r{k}.png").exists():
        k += 1
    out_path = images_dir / f"image_r{k}.png"

    # ── Mode: text_only — tight in-place copy edit ──────────────────────────────
    if payload.mode == "text_only":
        if payload.custom_prompt:
            instruction = payload.custom_prompt.strip()
        else:
            price = brief.get("price_display") or brief.get("price") or ""
            lines = [
                "Edit this real-estate advertisement image. Keep the photograph, scene, "
                "composition, layout, colours, and all graphic elements exactly as they are.",
                "Only update the text content:",
            ]
            if first_headline:
                lines.append(f'- Headline/tagline text should read: "{first_headline}"')
            if price:
                lines.append(f'- Price should read: "{price}"')
            if default_cta:
                lines.append(f'- CTA/badge text should read: "{default_cta}"')
            lines.append("Do not change anything else about the image.")
            instruction = "\n".join(lines)

        if os.getenv("REMIX_MOCK") == "1":
            return {
                "mock": True, "mode": "text_only",
                "prompt_sent": instruction, "filename": out_path.name,
                "reference": safe_name,
            }

        if not openai_key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY not configured.")

        try:
            result_bytes = openai_client.edit_image(
                ref_path.read_bytes(), instruction, openai_key, aspect=payload.aspect,
            )
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log("Reference variant — text only", exc)
            raise HTTPException(status_code=502, detail=friendly["message"])

        prompt = instruction
        provenance = {"reference_filename": safe_name, "mode": "text_only"}

    # ── Mode: change_scene — fresh photography, same layout ─────────────────────
    elif payload.mode == "change_scene":
        from ...crews.content_crew.task_composer import get_variant_meta as _get_vm

        # Validate scene_variant
        try:
            vm = _get_vm(payload.scene_variant)
        except (KeyError, Exception):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scene_variant: {payload.scene_variant!r}",
            )

        # Extract the reference image's ad element layout (cached after first call)
        ref_layout = imgs.extract_reference_ad_layout(ref_path)
        if not ref_layout:
            raise HTTPException(
                status_code=502,
                detail="Could not extract ad layout from reference image. Check VISION_MODEL.",
            )

        # Generate scene_prose for the chosen variant via a small LLM call
        creative_brief = vm.get("creative_brief", "")
        scene_pool_str = ", ".join(vm.get("scene_pool", [])[:3])

        scene_system = (
            "You are a luxury real estate photographer writing a shot brief for an AI image "
            "generator. Write exactly two tight paragraphs (120-140 words total). "
            "Paragraph 1: camera body, lens, angle, focal distance, light quality, time of day, "
            "one natural photographic imperfection. "
            "Paragraph 2: materials, surfaces, architectural detail visible in the shot — "
            "concrete and specific, no generic adjectives. "
            "DO NOT mention text, typography, or ad layout. Output only the two paragraphs."
        )
        scene_user = (
            f"Brief: {creative_brief.strip()}\n"
            f"Scene options (pick one): {scene_pool_str}\n"
            f"Property type: {brief.get('property_type', 'premium apartment')}"
        )

        if payload.custom_prompt:
            scene_prose = payload.custom_prompt.strip()
            prompt = payload.custom_prompt.strip()
        else:
            from ..services.image.brief_model import BriefModel
            from ..services.image.art_director import build_ad_spec
            from ..services.image import baked_prompt as _baked_prompt

            model = os.getenv("CREATIVE_MODEL", "gemini/gemini-2.5-flash")
            try:
                resp = litellm.completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": scene_system},
                        {"role": "user", "content": scene_user},
                    ],
                    temperature=0.85, max_tokens=250,
                )
                scene_prose = resp.choices[0].message.content.strip()
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Scene LLM call failed: {exc}")

            brief_model = BriefModel.from_brief(
                brief,
                headline=first_headline,
                cta=default_cta,
                sample_ready_override=sample_ready,
            )
            brief_model.has_logo = BRAND_LOGO_PATH.exists()

            # Build an AdSpec for the requested scene_variant, then inject:
            # - the LLM-generated scene prose for the fresh photography
            # - the reference image's element layout as the composition notes
            # This gives brand-new photography with the same text element positions.
            spec = build_ad_spec(
                variant_key=payload.scene_variant,
                prompt_num=k,
                brief=brief_model,
            )
            spec.scene_prose = scene_prose
            spec.composition = (
                f"{scene_prose}\n\nAD ELEMENT LAYOUT (mirror this reference ad layout "
                f"exactly — same positions for all text zones):\n{ref_layout}"
            )
            prompt = _baked_prompt.build(spec, brief_model)

        if os.getenv("REMIX_MOCK") == "1":
            return {
                "mock": True, "mode": "change_scene",
                "prompt_sent": prompt,
                "scene_prose": scene_prose if not payload.custom_prompt else prompt,
                "composition_notes": ref_layout,
                "filename": out_path.name, "reference": safe_name,
            }

        if not openai_key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY not configured.")

        try:
            result_bytes = openai_client.generate_image(prompt, openai_key, aspect=payload.aspect)
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log("Reference variant — change scene", exc)
            raise HTTPException(status_code=502, detail=friendly["message"])

        provenance = {
            "reference_filename": safe_name,
            "mode": "change_scene",
            "scene_variant": payload.scene_variant,
        }

    # ── Mode: change_elements — keep photo, vary secondary elements ─────────────
    else:
        if payload.custom_prompt:
            instruction = payload.custom_prompt.strip()
        else:
            instruction = (
                "Edit this real-estate advertisement image. Keep the photograph and scene "
                "exactly as they are — do not change the setting, subject, or composition.\n"
                "Vary the secondary graphic ad elements to produce a visually distinct variant: "
                "change the badge/CTA shape and colour, the accent colour used in text callouts, "
                "and the footer/spec-row treatment. Keep all text content (headline, price, "
                "location) the same as in the reference — only restyle these secondary elements."
            )

        if os.getenv("REMIX_MOCK") == "1":
            return {
                "mock": True, "mode": "change_elements",
                "prompt_sent": instruction, "filename": out_path.name,
                "reference": safe_name,
            }

        if not openai_key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY not configured.")

        try:
            result_bytes = openai_client.edit_image(
                ref_path.read_bytes(), instruction, openai_key, aspect=payload.aspect,
            )
        except Exception as exc:
            from pikorua_adflow.tools.errors import explain_and_log
            friendly = explain_and_log("Reference variant — change elements", exc)
            raise HTTPException(status_code=502, detail=friendly["message"])

        prompt = instruction
        provenance = {"reference_filename": safe_name, "mode": "change_elements"}

    # ── Common: save, logo composite, record provenance ───────────────────────
    out_path.write_bytes(result_bytes)

    logo_corner = "bottom-right"
    if BRAND_LOGO_PATH.exists():
        try:
            logo_backup_dir = out_path.parent / ".logo_backup"
            logo_backup_dir.mkdir(exist_ok=True)
            shutil.copy2(out_path, logo_backup_dir / out_path.name)
            imgs.composite_logo(out_path, BRAND_LOGO_PATH, corner=logo_corner)
        except Exception:
            pass

    edits = cs.load_edits(review_folder)
    rv = edits.setdefault("reference_variants", {})
    rv[str(k)] = {
        **provenance,
        "generated_at": datetime.datetime.utcnow().isoformat(),
        "prompt_sent": prompt,
    }
    cs.save_edits(review_folder, edits)

    return {
        "filename": out_path.name, "status": "generated",
        "reference": safe_name, "mode": payload.mode, "prompt_num": k,
    }


_LAZY_VARIANT_ORDER = [
    "lifestyle_private_retreat",
    "lifestyle_social_home",
    "lifestyle_dynamic_a",
    "lifestyle_dynamic_b",
    "interior_signature_moment",
    "exterior_establishing_shot",
]


@router.post("/generate-prompt/{run_id}/{prompt_num}")
def generate_prompt_on_demand(run_id: str, prompt_num: int):
    """Write a visual_prompts.json entry for one slot on demand (lazy prompt generation).

    Call this before /generate-images when running in LAZY_IMAGE_PROMPTS mode.
    If the entry already has scene_prose it is returned as-is (idempotent).
    Returns the assembled Ideogram prompt text so the UI can show it before generating.
    """

    run = RUNS.get(run_id)
    if not run or run.get("status") != "complete" or not run.get("review_folder"):
        raise HTTPException(status_code=400, detail="Run not complete or not found.")

    review_folder = Path(run["review_folder"])
    vp_path = review_folder / "visual_prompts.json"

    # Load or create placeholder list
    if vp_path.exists():
        try:
            entries = json.loads(vp_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    else:
        entries = []

    # Check if the entry already has scene_prose (idempotent) — return baked prompt preview
    entry = next((e for e in entries if e.get("prompt_num") == prompt_num), None)
    if entry and entry.get("scene_prose"):
        brief = run.get("brief", {})
        sample_ready = bool(brief.get("sample_ready", False))
        default_cta = "Sample Flat Ready" if sample_ready else ""
        try:
            from ..services.image.brief_model import BriefModel
            from ..services.image.art_director import AdSpec
            from ..services.image import baked_prompt as _baked_prompt
            eff = cs.effective_meta(review_folder)
            headline = (eff.get(prompt_num) or {}).get("headline", "")
            bm = BriefModel.from_brief(brief, headline=headline, cta=default_cta,
                                       sample_ready_override=sample_ready)
            bm.has_logo = BRAND_LOGO_PATH.exists()
            spec = AdSpec.from_entry({**entry, "variant_key": entry.get("variant_key", ""),
                                       "prompt_num": prompt_num})
            existing_prompt = _baked_prompt.build(spec, bm)
        except Exception:
            existing_prompt = entry.get("scene_prose", "")
        return {"prompt_num": prompt_num, "prompt": existing_prompt, "already_existed": True}

    # Determine the variant_key for this slot
    if 1 <= prompt_num <= len(_LAZY_VARIANT_ORDER):
        variant_key = _LAZY_VARIANT_ORDER[prompt_num - 1]
    elif entry:
        variant_key = entry.get("variant_key", f"variant_{prompt_num}")
    else:
        raise HTTPException(status_code=400, detail=f"prompt_num {prompt_num} out of range.")

    brief = run.get("brief", {})
    sample_ready = bool(brief.get("sample_ready", False))

    # Use the new art-director pipeline to produce a fully baked AdSpec for this slot.
    # This replaces the old LLM → VisualPromptOutput → build_ad_prompt() flow.
    eff = cs.effective_meta(review_folder)
    headline = (eff.get(prompt_num) or {}).get("headline", "")
    default_cta = "Sample Flat Ready" if sample_ready else ""

    from ..services.image.brief_model import BriefModel
    from ..services.image.art_director import build_ad_spec, AdSpec
    from ..services.image import baked_prompt as _baked_prompt

    brief_model = BriefModel.from_brief(
        brief, headline=headline, cta=default_cta, sample_ready_override=sample_ready
    )
    brief_model.has_logo = BRAND_LOGO_PATH.exists()

    try:
        spec = build_ad_spec(variant_key=variant_key, prompt_num=prompt_num, brief=brief_model)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Art director call failed: {exc}")

    baked_preview = _baked_prompt.build(spec, brief_model)

    # Upsert the AdSpec entry into visual_prompts.json so subsequent /generate-images
    # calls can load it directly via pipeline.ensure_spec().
    new_entry = spec.to_entry()  # full AdSpec fields
    new_entry["variant_key"] = variant_key
    new_entry["prompt_num"] = prompt_num

    existing_idx = next((i for i, e in enumerate(entries) if e.get("prompt_num") == prompt_num), None)
    if existing_idx is not None:
        entries[existing_idx] = new_entry
    else:
        entries.append(new_entry)
        entries.sort(key=lambda e: e.get("prompt_num", 99))

    vp_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"prompt_num": prompt_num, "prompt": baked_preview, "already_existed": False}
