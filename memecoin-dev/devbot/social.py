"""Announce each launch from the dev's own X account, and link that post as the coin's `twitter`.

X API v2 POST /2/tweets with OAuth 1.0a user-context signing (stdlib HMAC-SHA1). X bills
pay-per-use (2026): about $0.01-0.015 per post, more if the post contains a link, so the post
carries the contract address as text and no URL. Off unless all four X_* keys are set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

from . import http

API = "https://api.x.com/2/tweets"
MEDIA = "https://api.x.com/2/media/upload"
PROFILE = "https://api.x.com/1.1/account/update_profile.json"


@dataclass(frozen=True)
class XKeys:
    api_key: str
    api_secret: str
    access_token: str
    access_secret: str


def _q(s: str) -> str:
    return quote(s, safe="")  # RFC 3986: only unreserved characters stay literal


def oauth_header(keys: XKeys, method: str, url: str, *, nonce: str | None = None,
                 timestamp: str | None = None, query: dict[str, str] | None = None) -> str:
    """OAuth 1.0a Authorization header. A JSON body is not part of the signature base; query
    parameters are, so pass them as `query` with `url` left bare."""
    oauth = {
        "oauth_consumer_key": keys.api_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": keys.access_token,
        "oauth_version": "1.0",
    }
    signed = {**(query or {}), **oauth}
    base = "&".join([method.upper(), _q(url), _q("&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(signed.items())))])
    key = f"{_q(keys.api_secret)}&{_q(keys.access_secret)}".encode()
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_q(k)}="{_q(v)}"' for k, v in sorted(oauth.items()))


def launch_text(name: str, symbol: str, description: str, mint: str, disclosure: str) -> str:
    """At most 280 characters; the description is what gets trimmed. An empty mint leaves out the
    CA line (X refuses crypto addresses for an account's first 7 days after authorizing an app)."""
    ca = f"\n\nCA: {mint}" if mint else ""
    head = f"{name} ${symbol}\n\n"
    tail = ca + (("\n" if ca else "\n\n") + disclosure if disclosure else "")
    room = 280 - len(head) - len(tail)
    if room < 20:  # a long disclosure would crowd out the coin: drop it before the description
        tail = ca
        room = 280 - len(head) - len(tail)
    desc = description if len(description) <= room else description[:max(0, room - 1)].rstrip() + "…"
    return head + desc + tail


def upload_image(keys: XKeys, data: bytes, ext: str, mime: str) -> str:
    """Media id for a post. Multipart form fields are not part of the OAuth 1.0a signature base."""
    body, ctype = http.multipart({"media_category": "tweet_image"}, {"media": (f"coin.{ext}", data, mime)})
    raw = http.request("POST", MEDIA, body=body, timeout=60,
                       headers={"Authorization": oauth_header(keys, "POST", MEDIA), "Content-Type": ctype})
    return json.loads(raw)["data"]["id"]


def post(keys: XKeys, text: str, media_id: str = "", reply_to: str = "", quote_id: str = "") -> str:
    """Returns the post id. `quote_id` makes it a quote post of that post."""
    payload = {"text": text, **({"media": {"media_ids": [media_id]}} if media_id else {}),
               **({"reply": {"in_reply_to_tweet_id": reply_to}} if reply_to else {}),
               **({"quote_tweet_id": quote_id} if quote_id else {})}
    raw = http.request("POST", API, body=json.dumps(payload).encode(), timeout=30,
                       headers={"Authorization": oauth_header(keys, "POST", API),
                                "Content-Type": "application/json"})
    return json.loads(raw)["data"]["id"]


def update_bio(keys: XKeys, description: str, url: str = "") -> dict:
    """The profile bio (160 characters max) and, if given, the profile's website link. v2 has no
    profile endpoint; this is v1.1's."""
    query = {"description": description, **({"url": url} if url else {})}
    raw = http.request("POST", PROFILE + "?" + "&".join(f"{k}={_q(v)}" for k, v in query.items()), timeout=30,
                       headers={"Authorization": oauth_header(keys, "POST", PROFILE, query=query)})
    return json.loads(raw)


def get(keys: XKeys, url: str, query: dict[str, str]) -> dict:
    """Signed GET. Reads are billed per resource returned (~$0.005 per post, $0.010 per user, 2026)."""
    full = url + "?" + "&".join(f"{k}={_q(v)}" for k, v in query.items())
    return json.loads(http.request("GET", full, timeout=30,
                                   headers={"Authorization": oauth_header(keys, "GET", url, query=query)}))


def quote_blocked(e: Exception) -> bool:
    """X's 403 for quoting or replying to a post that neither mentions us nor is ours (API rule, 2026)."""
    return isinstance(e, http.HttpError) and e.status == 403 and "where you are mentioned or are the author" in e.body


def address_blocked(e: Exception) -> bool:
    """X's 403 for a crypto address posted inside the account's first 7 days after authentication."""
    return isinstance(e, http.HttpError) and e.status == 403 and "Crypto addresses are prohibited" in e.body


def delete(keys: XKeys, post_id: str) -> None:
    url = f"{API}/{post_id}"
    http.request("DELETE", url, headers={"Authorization": oauth_header(keys, "DELETE", url)}, timeout=30)


def post_url(post_id: str) -> str:
    return f"https://x.com/i/status/{post_id}"


def reply_url(post_id: str, text: str) -> str:
    """X's compose screen as a reply to `post_id` with the text filled in. For a human to tap: the API
    may not reply to posts that don't mention us, but a person may."""
    return f"https://x.com/intent/post?in_reply_to={_q(post_id)}&text={_q(text)}"
