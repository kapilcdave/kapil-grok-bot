"""Original coin art from Cloudflare Workers AI (FLUX.1 schnell), or Pollinations when Workers AI fails.

Pollinations is free (~5-8 s for 512x512 in testing). Keyless, the legacy endpoint stamps a
"pollinations.ai" watermark and follows the style loosely; with an sk_ key (enter.pollinations.ai)
the gen.pollinations.ai endpoint returns clean art that follows the style.

Cost is 4.8 neurons per 512x512 tile plus 9.6 per step. A 512x512 image at 4 steps is about
43 neurons, against about 77 for the default 1024x1024 at 6 steps. Width and height aren't in
the model's documented schema, so if the API rejects them the call falls back to the default
size, which is also square.
"""
from __future__ import annotations

import base64
import io
import json
import re
import secrets
from urllib.parse import quote, urlencode

from . import http

MODEL = "@cf/black-forest-labs/flux-1-schnell"
POLLINATIONS = "https://image.pollinations.ai/prompt/"     # keyless, watermarked
POLLINATIONS_KEYED = "https://gen.pollinations.ai/image/"  # needs the sk_ key, no watermark
SIZE = 512
# Variants of a live coin edit ITS picture: FLUX.2 [klein] 4B does generation and editing in one
# model, ~31 neurons for a 512x512 edit (5.37 per input tile + 26.05 per output tile), input images
# at most 512x512, sent as multipart. Pollinations' copy of the same model (alias "klein", 0.005
# pollen) edits only with a key (keyless answers 402); its /v1/images/edits takes the picture as an
# upload, because asking it to fetch an IPFS gateway URL got the gateway's 429 (2026-10-13).
EDIT_MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
POLLINATIONS_EDITS = "https://gen.pollinations.ai/v1/images/edits"
POLLINATIONS_EDIT_MODEL = "klein"
# Without "keep the face" klein dropped the character's face while adding a sidekick (2026-10-13).
EDIT_STYLE = (" Keep everything already in the input image exactly as it is: the same character with"
              " the same face, eyes and expression, pose, framing, art style and colours. Only make that"
              " one change. No text, no letters, no watermark.")
# ipfs.io now answers direct requests with a "service worker gateway only" page, not the file.
IPFS_RE = r"^https?://(?:ipfs\.io|cloudflare-ipfs\.com|[^/]*\.mypinata\.cloud)/ipfs/"
GATEWAY = "https://gateway.pinata.cloud/ipfs/"
STEPS = 4
# Describes the art only. The words "memecoin", "logo" or "coin" in a prompt get drawn as lettering.
STYLE = (", cartoon mascot character, centered, thick outlines, flat vivid colours, plain background,"
         " no text, no letters, no words, no watermark")


def _run(account_id: str, api_token: str, body: dict) -> bytes:
    raw = http.post_json(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{MODEL}",
        body, headers={"Authorization": f"Bearer {api_token}"}, timeout=120)
    out = json.loads(raw)
    image = (out.get("result") or {}).get("image") or out.get("image")
    if not image:
        raise RuntimeError(f"no image in Cloudflare response: {str(out)[:300]}")
    return base64.b64decode(image)


def _cloudflare(account_id: str, api_token: str, prompt: str) -> bytes:
    body = {"prompt": (prompt + STYLE)[:2048], "steps": STEPS}  # the API rejects `seed`
    try:
        return _run(account_id, api_token, {**body, "width": SIZE, "height": SIZE})
    except http.HttpError as e:
        if e.status != 400:
            raise
        return _run(account_id, api_token, body)


def _pollinations(prompt: str, token: str) -> bytes:
    query = urlencode({"width": SIZE, "height": SIZE, "model": "flux", "nologo": "true",
                       "seed": secrets.randbelow(2**31)})
    base = POLLINATIONS_KEYED if token else POLLINATIONS
    data = http.request("GET", base + quote((prompt + STYLE)[:1200], safe="") + "?" + query,
                        headers={"Authorization": f"Bearer {token}"} if token else None, timeout=120)
    if not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")):
        raise RuntimeError(f"Pollinations returned no image: {data[:200]!r}")
    return data


def source_url(url: str) -> str:
    """A coin's image URL that actually serves the file."""
    return re.sub(IPFS_RE, GATEWAY, url or "")


def fit(data: bytes) -> bytes:
    """PNG at most SIZE on a side: the edit model's input limit, and it turns webp/gif into PNG."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail((SIZE, SIZE))
        out = io.BytesIO()
        im.save(out, "PNG")
    return out.getvalue()


def _cloudflare_edit(account_id: str, api_token: str, src_png: bytes, twist: str) -> bytes:
    body, ctype = http.multipart({"prompt": (twist + EDIT_STYLE)[:2048], "width": str(SIZE), "height": str(SIZE)},
                                 {"input_image_0": ("source.png", src_png, "image/png")})
    raw = http.request("POST", f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{EDIT_MODEL}",
                       body=body, headers={"Authorization": f"Bearer {api_token}", "Content-Type": ctype},
                       timeout=120)
    out = json.loads(raw)
    image = (out.get("result") or {}).get("image") or out.get("image")
    if not image:
        raise RuntimeError(f"no image in Cloudflare edit response: {str(out)[:300]}")
    return base64.b64decode(image)


def _pollinations_edit(src_png: bytes, twist: str, token: str) -> bytes:
    body, ctype = http.multipart({"prompt": (twist + EDIT_STYLE)[:1200], "model": POLLINATIONS_EDIT_MODEL,
                                  "size": f"{SIZE}x{SIZE}"}, {"image": ("source.png", src_png, "image/png")})
    raw = http.request("POST", POLLINATIONS_EDITS, body=body, timeout=120,
                       headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})
    item = (json.loads(raw).get("data") or [{}])[0]
    data = base64.b64decode(item.get("b64_json") or "")
    if not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")):
        raise RuntimeError(f"Pollinations edit returned no image: {raw[:200]!r}")
    return data


def variant(account_id: str, api_token: str, src_url: str, twist: str, *, pollinations_token: str = "") -> bytes:
    """The source coin's own picture with one change: Workers AI, then Pollinations (keyed only).
    Raises when neither works; the caller decides whether fresh art is acceptable."""
    url = source_url(src_url)
    if not url.startswith("https://"):
        raise RuntimeError(f"source coin has no usable image URL: {src_url!r}")
    try:
        src = fit(http.request("GET", url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30))
    except Exception as e:  # PIL errors on a non-image too
        raise RuntimeError(f"source picture unreadable: {str(e)[:150]}") from None
    errors = []
    try:
        return _cloudflare_edit(account_id, api_token, src, twist)
    except (http.HttpError, OSError, RuntimeError, ValueError) as e:
        errors.append(f"Workers AI: {str(e)[:150]}")
    if pollinations_token:
        try:
            return _pollinations_edit(src, twist, pollinations_token)
        except (http.HttpError, OSError, RuntimeError, ValueError) as e:
            errors.append(f"Pollinations: {str(e)[:150]}")
    raise RuntimeError("; ".join(errors))


def generate(account_id: str, api_token: str, prompt: str, *, fallback: bool = False,
             pollinations_token: str = "") -> bytes:
    """Square JPEG (or PNG) bytes: Workers AI, then Pollinations if `fallback` and Workers AI fails."""
    try:
        return _cloudflare(account_id, api_token, prompt)
    except http.HttpError:
        if not fallback:
            raise
        return _pollinations(prompt, pollinations_token)


def kind(data: bytes) -> tuple[str, str]:
    """(extension, MIME type) from the magic bytes; the two models return different formats."""
    return ("png", "image/png") if data[:8] == b"\x89PNG\r\n\x1a\n" else ("jpg", "image/jpeg")


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG's SOF header, so the bot can log what it actually got."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker, seg = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + seg
    return None
