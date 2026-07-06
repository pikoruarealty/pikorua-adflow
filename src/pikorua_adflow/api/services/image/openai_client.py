"""
OpenAI image client for the reference-creatives feature.

Ideogram struggles to preserve exact text when editing an existing image, so
reference-creative generation (see routes/visuals.py::generate_reference_variant)
uses OpenAI's gpt-image-1 instead: `edit_image` operates directly on the
reference's pixels (best for text/copy or ad-element tweaks), `generate_image`
is a plain text-to-image call (used for the "change scene" mode, which wants
fresh photography guided by extracted layout notes rather than the original
pixels). Raw urllib calls mirror the existing ideogram_client/image_service
style — no new SDK dependency.
"""

from __future__ import annotations

import json as _json
import time
import urllib.error
import urllib.request

_SIZE_MAP = {
    "1x1": "1024x1024",
    "4x5": "1024x1536",
    "9x16": "1024x1536",
    "2x3": "1024x1536",
    "16x9": "1536x1024",
    "3x2": "1536x1024",
}


def _size_for_aspect(aspect: str) -> str:
    clean = (aspect or "4x5").lower().replace(":", "x")
    return _SIZE_MAP.get(clean, "1024x1536")


def _post(url: str, data: bytes, headers: dict) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return _json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 429 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"OpenAI image request failed [{e.code}]: {detail}") from e
    raise RuntimeError("OpenAI image request failed: exhausted retries")


def edit_image(
    image_bytes: bytes,
    prompt: str,
    key: str,
    mask_bytes: bytes | None = None,
    aspect: str = "4x5",
) -> bytes:
    """Edit a reference image in place (gpt-image-1 `images/edits`).

    Sends the actual reference pixels — used for tight, close-to-reference
    edits (copy changes, secondary ad-element swaps) where text fidelity
    matters more than creative freedom.
    """
    size = _size_for_aspect(aspect)
    boundary = "----PikoruaOpenAIEditBoundary8Ht3wLmQ"

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    def _file_field(name: str, filename: str, content: bytes) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + content + b"\r\n"

    body = (
        _field("model", "gpt-image-1")
        + _field("prompt", prompt)
        + _field("size", size)
        + _file_field("image", "reference.png", image_bytes)
    )
    if mask_bytes:
        body += _file_field("mask", "mask.png", mask_bytes)
    body += f"--{boundary}--\r\n".encode("utf-8")

    data = _post(
        "https://api.openai.com/v1/images/edits",
        body,
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    return _decode_b64_image(data)


def generate_image(prompt: str, key: str, aspect: str = "4x5") -> bytes:
    """Plain text-to-image (gpt-image-1 `images/generations`).

    Used for "change scene" mode: no reference pixels are sent, only textual
    layout/composition notes, so the model has full creative freedom for the
    new photography while still following the described element positions.
    """
    size = _size_for_aspect(aspect)
    body = _json.dumps({"model": "gpt-image-1", "prompt": prompt, "size": size}).encode("utf-8")
    data = _post(
        "https://api.openai.com/v1/images/generations",
        body,
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    return _decode_b64_image(data)


def _decode_b64_image(data: dict) -> bytes:
    import base64 as _b64

    try:
        b64 = data["data"][0]["b64_json"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"OpenAI image response missing b64_json: {data}") from exc
    return _b64.b64decode(b64)
