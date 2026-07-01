"""
Stage 6a — Compositor: deterministic PIL rendering (RENDER mode, default).

Everything text-related happens here in code, so legibility is a guarantee, not a model
hope (§1, §10). The legibility engine enforces the old prose non-negotiables — minimum
size floors, tier contrast, and auto-scrim — as code, stated in exactly one place.

Rendering order (per AdSpec + BriefModel):
  scene -> layout scrim/border -> each present text element (auto-fit + auto-contrast)
  -> logo composite. Elements render only when their data exists in the BriefModel.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from . import fonts
from . import libraries as lib
from .art_director import AdSpec
from .brief_model import BriefModel

# ── Legibility engine constants (§10) — the single home for size/contrast rules ──
CANVAS_REF = 1080                  # MIN_SIZE values are px at this canvas height
MIN_SIZE = {
    "locality": 48, "price": 28, "headline": 22, "eyebrow": 16, "body": 14, "footer": 12,
}
CONTRAST_THRESHOLD = 0.4           # luminance above which a scrim/pill is required
_TARGET_CONTRAST_DARKEN = 150      # scrim alpha when a pill is needed (0-255)


def _hex(c: str, default=(255, 255, 255)) -> tuple[int, int, int]:
    c = (c or "").lstrip("#")
    if len(c) == 6:
        try:
            return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore
        except ValueError:
            pass
    return default


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (v / 255 for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _avg_luminance(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    region = img.convert("RGB").resize((5, 5), box=(x0, y0, x1, y1))
    px = list(region.getdata())
    return sum(_luminance(p) for p in px) / len(px)


def _text_width(draw, text, font, tracking=0) -> int:
    if tracking <= 0:
        return int(draw.textlength(text, font=font))
    return int(sum(draw.textlength(ch, font=font) for ch in text) + tracking * max(0, len(text) - 1))


def _fit_font(draw, text, pairing_id, role, zone_w, zone_h, min_px, max_px, tracking=0):
    """Largest font (by role) whose text fits zone_w (and ~zone_h); never below min_px."""
    lo, hi, best = min_px, max_px, None
    while lo <= hi:
        mid = (lo + hi) // 2
        f = fonts.get_font(pairing_id, role, mid)
        w = _text_width(draw, text, f, tracking)
        asc, desc = f.getmetrics()
        h = asc + desc
        if w <= zone_w and h <= zone_h:
            best = (f, mid)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        f = fonts.get_font(pairing_id, role, min_px)
        return f, min_px
    return best


def _draw_tracked(draw, xy, text, font, fill, tracking=0, anchor_lm=True):
    x, y = xy
    if tracking <= 0:
        draw.text((x, y), text, font=font, fill=fill, anchor="lm" if anchor_lm else "la")
        return
    # tracked: draw glyph by glyph, vertically centred on y
    asc, desc = font.getmetrics()
    top = y - (asc + desc) / 2 if anchor_lm else y
    for ch in text:
        draw.text((x, top), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def _pill(canvas, box, fill_rgb, radius_frac=0.18, alpha=235):
    x0, y0, x1, y1 = box
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    r = int(min(x1 - x0, y1 - y0) * radius_frac)
    d.rounded_rectangle([x0, y0, x1, y1], radius=max(2, r), fill=(*fill_rgb, alpha))
    canvas.alpha_composite(overlay)


def _scrim_panel(canvas, box, color_rgb, opacity):
    x0, y0, x1, y1 = box
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rectangle([x0, y0, x1, y1], fill=(*color_rgb, int(255 * opacity)))
    canvas.alpha_composite(overlay)


def _gradient_corner(canvas, box, color_rgb, opacity):
    """Soft dark vignette fading from the lower-left corner."""
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    grad = Image.new("L", (w, h), 0)
    gd = ImageDraw.Draw(grad)
    for i in range(h):
        a = int(255 * opacity * (i / h))   # darker toward the bottom
        gd.line([(0, i), (w, i)], fill=a)
    color = Image.new("RGBA", (w, h), (*color_rgb, 0))
    color.putalpha(grad)
    canvas.alpha_composite(color, (x0, y0))


# ── Main entry ────────────────────────────────────────────────────────────────

def composite(image_bytes: bytes, spec: AdSpec, brief: BriefModel) -> bytes:
    """Render all ad text from BriefModel onto the scene per AdSpec. Returns PNG bytes."""
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    W, H = base.size
    scale = H / CANVAS_REF
    canvas = base
    draw = ImageDraw.Draw(canvas)

    layout = lib.get_layout(spec.layout_id)
    palette = lib.get_palette(spec.palette_id)
    pairing = spec.type_pairing_id
    zones = layout.get("zones") or {}

    def px_box(zone) -> tuple[int, int, int, int]:
        x = int(zone["x"] * W); y = int(zone["y"] * H)
        w = int(zone["w"] * W); h = int(zone["h"] * H)
        return x, y, x + w, y + h

    def min_px(tier: str) -> int:
        return max(8, int(MIN_SIZE[tier] * scale))

    # 1) Layout scrim / border (provides bulk contrast where the layout designs for it)
    scrim_cfg = layout.get("scrim")
    scrim_rgb = _hex(palette.get("scrim_color"), (0, 0, 0))
    scrim_op = float(palette.get("scrim_opacity", 0.55)) + float((scrim_cfg or {}).get("opacity_boost", 0))
    scrim_box = None
    if scrim_cfg:
        sb = (int(scrim_cfg["x"] * W), int(scrim_cfg["y"] * H),
              int((scrim_cfg["x"] + scrim_cfg["w"]) * W), int((scrim_cfg["y"] + scrim_cfg["h"]) * H))
        scrim_box = sb
        if scrim_cfg.get("shape") == "gradient_corner":
            _gradient_corner(canvas, sb, scrim_rgb, min(0.92, scrim_op))
        else:
            _scrim_panel(canvas, sb, scrim_rgb, min(0.92, scrim_op))

    border_cfg = layout.get("border")
    if border_cfg:
        inset = int(border_cfg.get("inset", 0.035) * W)
        bw = max(1, int(border_cfg.get("width", 0.004) * W))
        bcol = _hex(palette.get(border_cfg.get("color_role", "locality_color")), (200, 170, 80))
        draw.rectangle([inset, inset, W - inset, H - inset], outline=(*bcol, 255), width=bw)

    def covered_by_scrim(box) -> bool:
        if not scrim_box:
            return False
        # element midpoint inside the scrim panel
        mx, my = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        return scrim_box[0] <= mx <= scrim_box[2] and scrim_box[1] <= my <= scrim_box[3]

    def ensure_contrast(box, text_rgb):
        """Add a soft pill behind text if the surface is too bright for the text colour."""
        if covered_by_scrim(box):
            return
        lum = _avg_luminance(canvas, box)
        # bright surface + light-ish text => needs help
        if lum > CONTRAST_THRESHOLD and _luminance(text_rgb) > 0.35:
            pad = int((box[3] - box[1]) * 0.25)
            _pill(canvas, (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
                  scrim_rgb, alpha=_TARGET_CONTRAST_DARKEN)

    def place_x(zone, box, text_w):
        align = zone.get("align", "left")
        if align == "center":
            return (box[0] + box[2]) // 2 - text_w // 2
        if align == "right":
            return box[2] - text_w
        return box[0]

    def draw_line(name, text, role, tier, color_rgb, caps=True, tracking_frac=0.0):
        """Auto-fit + auto-contrast + draw a single text line in its zone."""
        if not text or name not in zones or zones[name] is None:
            return
        zone = zones[name]
        box = px_box(zone)
        zw, zh = box[2] - box[0], box[3] - box[1]
        s = text.upper() if caps else text
        font, size = _fit_font(draw, s, pairing, role, zw, zh, min_px(tier),
                               int(zh * 1.05), tracking=int(min_px(tier) * tracking_frac))
        track = int(size * tracking_frac)
        tw = _text_width(draw, s, font, track)
        x = place_x(zone, box, tw)
        cy = (box[1] + box[3]) // 2
        tight = (x, box[1], x + tw, box[3])
        ensure_contrast(tight, color_rgb)
        _draw_tracked(draw, (x, cy), s, font, (*color_rgb, 255), tracking=track)

    # 2) Elements — render only when the data is present (§6a step 5)
    locality_rgb = _hex(palette.get("locality_color"))
    headline_rgb = _hex(palette.get("headline_color"))
    body_rgb = _hex(palette.get("body_color"))
    eyebrow_rgb = _hex(palette.get("eyebrow_color"), locality_rgb)

    try:
        draw_line("eyebrow", brief.eyebrow, "accent", "eyebrow", eyebrow_rgb, caps=True, tracking_frac=0.12)
    except Exception:
        pass
    try:
        draw_line("locality", brief.locality_display, "display", "locality", locality_rgb, caps=True, tracking_frac=0.04)
    except Exception:
        pass
    try:
        draw_line("city", brief.city_display, "body", "body", body_rgb, caps=True, tracking_frac=0.12)
    except Exception:
        pass
    try:
        draw_line("headline", brief.headline, "display", "headline", headline_rgb, caps=False)
    except Exception:
        pass
    try:
        draw_line("config", brief.config.upper(), "body", "headline", body_rgb, caps=True, tracking_frac=0.06)
    except Exception:
        pass

    # Price — inside a bounded container
    try:
        if brief.price_display and "price" in zones and zones["price"]:
            _draw_price(canvas, draw, zones["price"], px_box(zones["price"]), brief.price_display,
                        palette, pairing, min_px("price"))
    except Exception:
        pass

    # CTA badge — rounded pill
    try:
        if brief.cta_text and "cta_badge" in zones and zones["cta_badge"]:
            _draw_badge(canvas, draw, zones["cta_badge"], px_box(zones["cta_badge"]),
                        brief.cta_text, palette, pairing, min_px("body"))
    except Exception:
        pass

    # Footer row — up to 3 USPs
    try:
        items = brief.footer_items()
        if items and "footer" in zones and zones["footer"]:
            footer_text = "   ·   ".join(i.upper() for i in items)
            draw_line("footer", footer_text, "body", "footer", body_rgb, caps=True, tracking_frac=0.08)
    except Exception:
        pass

    # Logo is composited by the route layer (composite_logo + .logo_backup) so the
    # existing revert-logo flow keeps working — see visuals.generate_images.
    return _finalize(canvas)


def _draw_price(canvas, draw, zone, box, text, palette, pairing, min_px):
    x0, y0, x1, y1 = box
    zw, zh = x1 - x0, y1 - y0
    bg = _hex(palette.get("price_bg"), (20, 20, 20))
    fg = _hex(palette.get("price_text"), (201, 168, 76))
    border = _hex(palette.get("price_border"), fg)
    pad = int(zh * 0.16)
    font, size = _fit_font(draw, text, pairing, "body", zw - 2 * pad, zh - 2 * pad,
                          min_px, int(zh * 0.7))
    tw = int(draw.textlength(text, font=font))
    asc, desc = font.getmetrics()
    th = asc + desc
    # container sized to text + padding, aligned within the zone
    cw, ch = tw + 2 * pad, th + int(pad * 1.2)
    align = zone.get("align", "right")
    cx = x1 - cw if align == "right" else (x0 if align == "left" else (x0 + x1) // 2 - cw // 2)
    cy = (y0 + y1) // 2 - ch // 2
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=int(ch * 0.18),
                         fill=(*bg, 240), outline=(*border, 255), width=max(1, int(ch * 0.04)))
    canvas.alpha_composite(overlay)
    draw.text((cx + cw // 2, cy + ch // 2), text, font=font, fill=(*fg, 255), anchor="mm")


def _draw_badge(canvas, draw, zone, box, text, palette, pairing, min_px):
    x0, y0, x1, y1 = box
    zw, zh = x1 - x0, y1 - y0
    bg = _hex(palette.get("cta_bg"), (201, 168, 76))
    fg = _hex(palette.get("cta_text"), (26, 26, 26))
    pad = int(zh * 0.22)
    # allow wrapping to 2 lines for longer CTAs
    font, size = _fit_font(draw, text, pairing, "body", zw - 2 * pad, (zh - 2 * pad),
                          min_px, int(zh * 0.6))
    tw = int(draw.textlength(text, font=font))
    asc, desc = font.getmetrics()
    th = asc + desc
    cw, ch = min(zw, tw + 2 * pad), th + int(pad * 1.1)
    align = zone.get("align", "right")
    cx = x1 - cw if align == "right" else (x0 if align == "left" else (x0 + x1) // 2 - cw // 2)
    cy = (y0 + y1) // 2 - ch // 2
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=int(ch * 0.45), fill=(*bg, 245))
    canvas.alpha_composite(overlay)
    draw.text((cx + cw // 2, cy + ch // 2), text, font=font, fill=(*fg, 255), anchor="mm")


def _finalize(canvas: Image.Image) -> bytes:
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
