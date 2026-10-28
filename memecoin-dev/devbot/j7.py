"""Send pump.fun creates through j7tracker's deploy API (docs.j7tracker.io) instead of PumpPortal.

j7tracker signs on its own servers with an encrypted copy of the dev wallet's key: J7_WALLET_KEY is
the string its app shows under Deploy Settings > Wallets (never a raw private key). The operator
imported the bot's dev wallet there on 2026-10-14, knowing j7tracker (or a breach of it) can then
move that wallet. Only plain deploys are used: no bundle or sniper wallets.

Auth is a j7tracker session. Its login needs a CAPTCHA, so a script can't log in. The web app keeps
the session in two cookies on core.j7tracker.io: `__Host-j7_rt` (refresh) and `__Secure-j7_at`, a
short-lived access token (a JWT) that the deploy API takes as a Bearer. The refresh endpoint only
answers the j7tracker site itself ("origin not allowed", 2026-10-14) and the bot does not pose as it,
so the operator sends the access token with /j7 <token> and sends a new one when it expires.

The bot's own pre-made mint keypair goes in as `vanity_mint_keypair`, so the X post and the website,
both made before the create, point at the coin that actually gets created.
"""
from __future__ import annotations

import base64
import json
import time

from solders.keypair import Keypair

from . import http, image as image_mod
from .pump import Launched, Rpc, _landed

BASES = {"nyc": "https://tx-nyc.j7tracker.io/deploy", "lax": "https://tx-lax.j7tracker.io/deploy",
         "fra": "https://tx-fra.j7tracker.io/deploy", "sgp": "https://tx-sgp.j7tracker.io/deploy"}
CORE = "https://core.j7tracker.io"


# Measured on-chain 2026-10-14 ($REFFS, $Bbbs): the create moves 0.0168 SOL (rent ~0.0057, j7tracker
# 0.0025, a 0.0085 landing tip) and a second j7tracker transaction takes 0.0016 more. The tip likely
# follows j7tracker's Deploy Settings; the deploy API documents no field for it.
LAUNCH_COST_SOL = 0.0184


class J7Error(RuntimeError):
    pass


def _auth(session: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {session}", "X-J7-Auth": "1"}


def expiry(token: str) -> float:
    """The access token's `exp` (epoch seconds), read from the JWT payload; 0 if it has none."""
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("exp") or 0)
    except (IndexError, ValueError, AttributeError):
        raise J7Error("that isn't a j7tracker access token: send the __Secure-j7_at cookie's value (it starts eyJ)") from None


def session_check(session: str) -> dict:
    """{'username': ..., 'expiresAt': ms} for a live session; J7Error otherwise."""
    try:
        return json.loads(http.request("GET", f"{CORE}/api/session-check", headers=_auth(session), timeout=15))
    except http.HttpError as e:
        raise J7Error(f"j7tracker refused the session (HTTP {e.status}): {e.body[:200]}") from None


def deploy(rpc: Rpc, base: str, session: str, wallet_key: str, mint: Keypair, *, name: str, symbol: str,
           description: str, image: bytes, website: str = "", twitter: str = "", dev_buy_sol: float = 0.0,
           wait_s: float = 20) -> Launched:
    """One pump.fun create. j7tracker answers within ~30 s with the signature; the bot then waits up
    to `wait_s` for it to confirm, like pump.create."""
    _, mime = image_mod.kind(image)
    body = {
        "type": "create_token", "external": True, "mode": "pump", "api_key": wallet_key,
        "name": name[:32], "ticker": symbol[:10], "buy_amount": dev_buy_sol,
        "image_url": f"data:{mime};base64,{base64.b64encode(image).decode()}",
        "description": description, "website": website, "twitter": twitter,
        "private_desc": True,   # our description is the joke; no "Deployed using j7tracker.io" stamp
        "vanity_mint_keypair": str(mint),
    }
    try:
        raw = http.request("POST", f"{base}/submit", body=json.dumps(body).encode(), timeout=45,
                           headers={**_auth(session), "Content-Type": "text/plain;charset=UTF-8"})
    except http.HttpError as e:
        if e.status == 401:
            raise J7Error("j7tracker session expired or invalid: send the bot a fresh one with /j7 <token>") from None
        raise J7Error(f"j7tracker deploy HTTP {e.status}: {e.body[:300]}") from None
    out = json.loads(raw)
    if out.get("type") != "token_create_success":
        raise J7Error(f"j7tracker deploy failed: {out.get('error') or out.get('message') or str(out)[:300]}")
    sig, got = out.get("signature") or "", out.get("mint_address") or ""
    if got != str(mint.pubkey()):
        raise J7Error(f"j7tracker created {got}, not our mint {mint.pubkey()}: the X post and website point elsewhere")
    deadline = time.time() + wait_s
    confirmed = False
    while sig and time.time() < deadline and not confirmed:
        time.sleep(2)
        try:
            confirmed = _landed(rpc, [sig]) is not None
        except (RuntimeError, http.HttpError, OSError):
            pass
    return Launched(got, sig, confirmed)
