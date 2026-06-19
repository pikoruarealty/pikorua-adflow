"""Brand logo, reference-image, and logo/favicon asset endpoints."""

from __future__ import annotations

import io
import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..config import BRAND_LOGO_PATH, LOGO_DIR, REFERENCE_IMAGES_DIR
from ..services import image_service
from ..services.campaign_service import require_complete

router = APIRouter()


# ── Brand logo ───────────────────────────────────────────────────────────────
@router.post("/brand-logo")
async def upload_brand_logo(request: Request):
    """Store a brand logo (PNG/JPG/WebP) to be composited onto generated images."""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="No image data received.")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo too large (max 8 MB).")
    if not (data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff"
            or data[:4] in (b"RIFF", b"WEBP")):
        raise HTTPException(status_code=400, detail="File must be PNG, JPG, or WebP.")
    BRAND_LOGO_PATH.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image as _PILImage
    img = _PILImage.open(io.BytesIO(data)).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    BRAND_LOGO_PATH.write_bytes(buf.getvalue())
    return {"ok": True, "width": img.width, "height": img.height}


@router.get("/brand-logo")
def get_brand_logo():
    if not BRAND_LOGO_PATH.exists():
        raise HTTPException(status_code=404, detail="No brand logo uploaded yet.")
    return Response(content=BRAND_LOGO_PATH.read_bytes(), media_type="image/png")


@router.delete("/brand-logo")
def delete_brand_logo():
    if BRAND_LOGO_PATH.exists():
        BRAND_LOGO_PATH.unlink()
    return {"ok": True}


@router.post("/apply-logo/{run_id}")
def apply_logo_to_run(run_id: str):
    """Composite the brand logo onto all images already on disk for this run."""
    if not BRAND_LOGO_PATH.exists():
        raise HTTPException(status_code=400, detail="No brand logo uploaded yet.")
    run = require_complete(run_id)
    review_folder = Path(run["review_folder"])
    images_dir = review_folder / "images"
    if not images_dir.exists():
        return {"ok": True, "count": 0}

    # Build a prompt_num → logo_corner lookup from visual_prompts.json
    corner_by_num: dict[int, str] = {}
    vp_path = review_folder / "visual_prompts.json"
    if vp_path.exists():
        try:
            for entry in json.loads(vp_path.read_text(encoding="utf-8")):
                n = entry.get("prompt_num")
                c = entry.get("logo_corner", "bottom-right")
                if n:
                    corner_by_num[int(n)] = c
        except Exception:
            pass

    count = 0
    for img_path in sorted(images_dir.glob("image_*.png")):
        try:
            backup_dir = images_dir / ".logo_backup"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / img_path.name
            if not backup.exists():
                import shutil as _shutil
                _shutil.copy2(img_path, backup)
            m = re.match(r"image_(\d+)", img_path.stem)
            corner = corner_by_num.get(int(m.group(1)), "bottom-right") if m else "bottom-right"
            image_service.composite_logo(img_path, BRAND_LOGO_PATH, corner=corner)
            count += 1
        except Exception:
            pass
    return {"ok": True, "count": count}


# ── Reference images ─────────────────────────────────────────────────────────
@router.post("/reference-images")
async def upload_reference_images(request: Request):
    """Accept one or more reference images (multipart/form-data or raw bytes)."""
    content_type = request.headers.get("content-type", "")
    REFERENCE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    if "multipart/form-data" in content_type:
        form = await request.form()
        for _field_name, file in form.multi_items():
            if hasattr(file, "read"):
                data = await file.read()
                filename = getattr(file, "filename", None) or f"ref_{uuid.uuid4().hex[:8]}.png"
            else:
                continue
            if len(data) > 12 * 1024 * 1024:
                continue
            safe_name = re.sub(r"[^\w.\-]", "_", filename)
            dest = REFERENCE_IMAGES_DIR / safe_name
            k = 1
            while dest.exists():
                stem, suf = safe_name.rsplit(".", 1) if "." in safe_name else (safe_name, "png")
                dest = REFERENCE_IMAGES_DIR / f"{stem}_{k}.{suf}"
                k += 1
            dest.write_bytes(data)
            desc = image_service.analyze_reference_image(dest)
            saved.append({"filename": dest.name, "description": desc})
    else:
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="No image data received.")
        if len(data) > 12 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large (max 12 MB).")
        fname = f"ref_{uuid.uuid4().hex[:8]}.png"
        dest = REFERENCE_IMAGES_DIR / fname
        dest.write_bytes(data)
        desc = image_service.analyze_reference_image(dest)
        saved.append({"filename": fname, "description": desc})
    return {"ok": True, "saved": saved}


@router.get("/reference-images")
def list_reference_images():
    if not REFERENCE_IMAGES_DIR.exists():
        return {"images": []}
    imgs = []
    for p in (sorted(REFERENCE_IMAGES_DIR.glob("*.png")) + sorted(REFERENCE_IMAGES_DIR.glob("*.jpg"))
              + sorted(REFERENCE_IMAGES_DIR.glob("*.jpeg")) + sorted(REFERENCE_IMAGES_DIR.glob("*.webp"))):
        desc = image_service.ref_description_path(p)
        imgs.append({
            "filename": p.name,
            "description": desc.read_text(encoding="utf-8").strip() if desc.exists() else "",
        })
    return {"images": imgs}


@router.get("/reference-images/{filename}")
def get_reference_image(filename: str):
    safe = re.sub(r"[^\w.\-]", "_", filename)
    path = REFERENCE_IMAGES_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    ext = path.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")
    return Response(content=path.read_bytes(), media_type=mime)


@router.delete("/reference-images/{filename}")
def delete_reference_image(filename: str):
    safe = re.sub(r"[^\w.\-]", "_", filename)
    path = REFERENCE_IMAGES_DIR / safe
    desc = image_service.ref_description_path(path)
    if path.exists():
        path.unlink()
    if desc.exists():
        desc.unlink()
    return {"ok": True}


# ── Brand logo / favicon imagery ─────────────────────────────────────────────
@router.get("/logo/light")
def logo_light():
    return Response(content=image_service.trimmed_png(LOGO_DIR / "without Sparkle Logo.png"),
                    media_type="image/png")


@router.get("/logo/dark")
def logo_dark():
    return Response(content=image_service.trimmed_png(LOGO_DIR / "with Sparkle Logo.png"),
                    media_type="image/png")


@router.get("/favicon.ico")
def favicon():
    return Response(content=image_service.square_favicon(LOGO_DIR / "favicon.png"),
                    media_type="image/png")
