"""Tiny stdlib HTTP helpers: the box has 1 GB shared with Hermes, so no requests/httpx here."""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from typing import Any

UA = "devbot/0.1"


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> bytes:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace")) from None


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    return json.loads(request("GET", url, headers=headers, timeout=timeout))


def post_json(url: str, payload: Any, *, headers: dict[str, str] | None = None, timeout: float = 60) -> bytes:
    return request(
        "POST",
        url,
        body=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        timeout=timeout,
    )


def multipart(
    fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
) -> tuple[bytes, str]:
    """Encode a multipart/form-data body. files maps field -> (filename, data, content_type)."""
    boundary = "----devbot" + secrets.token_hex(12)
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for name, (filename, data, ctype) in files.items():
        out += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"
