"""
Thin Ideogram v4 wrapper for the new pipeline.

The low-level multipart/form-data HTTP calls (retry/backoff, resolution maps, image
download) are stable and already battle-tested in image_service.py — the spec says to
reuse them rather than re-implement. This module re-exports them under the names the
new pipeline uses, so route code depends on api/services/image/* and never reaches
back into the legacy module directly.
"""

from __future__ import annotations

from ..image_service import (
    call_ideogram as _call_ideogram,
    call_ideogram_inpaint as _call_inpaint,
    call_ideogram_remix as _call_remix,
)


def call(prompt: str, key: str, speed: str = "QUALITY", aspect: str = "4x5") -> bytes:
    """Generate an image from a text prompt. Returns raw image bytes."""
    return _call_ideogram(prompt, key, speed=speed, aspect=aspect)


def call_inpaint(
    image_bytes: bytes, mask_bytes: bytes, prompt: str, key: str,
    speed: str = "QUALITY", aspect: str = "4x5",
) -> bytes:
    """Edit a masked region of an existing image (white = regenerate)."""
    return _call_inpaint(image_bytes, mask_bytes, prompt, key, speed=speed, aspect=aspect)


def call_remix(
    image_bytes: bytes, prompt: str, key: str,
    speed: str = "DEFAULT", aspect: str = "4x5", image_weight: float = 0.5,
) -> bytes:
    """Generate a variant of a reference image guided by a text prompt."""
    return _call_remix(image_bytes, prompt, key, speed=speed, aspect=aspect,
                        image_weight=image_weight)
