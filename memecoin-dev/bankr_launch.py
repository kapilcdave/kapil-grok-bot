"""Launch a token on Bankr (Doppler, Base, gas-sponsored) and track creator fees.

    python bankr_launch.py launch "Name" SYM [--image URL | --image-file PATH] [--tweet URL] [--live]
    python bankr_launch.py fees            # lifetime creator fees per logged launch
    python bankr_launch.py claim           # claim fees on every logged launch

Without --live the deploy is simulated (no broadcast, no quota slot). Key: BANKR_API_KEY in ~/.devbot/.env.
Limits: 3 counted launches / wallet / 24h, 1 / min, ~10 / IP / 24h.

--image-file pins a local file to IPFS first (needs PINATA_JWT) and uses that URL, for launching a
picture the operator supplied instead of one the pipeline generated. Must run from a box that clears
Bankr's WAF; see bankr_pipeline.py's header.
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

API = "https://api.bankr.bot"
LEDGER = Path(__file__).parent / "data" / "bankr_launches.jsonl"


def load_env():
    p = Path.home() / ".devbot" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def call(method, path, body=None, auth=True):
    h = {"content-type": "application/json", "user-agent": "bankr-launch/1"}
    if auth:
        h["x-api-key"] = os.environ["BANKR_API_KEY"]
    req = urllib.request.Request(API + path, method=method, headers=h,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def wallet():
    _, j = call("GET", "/wallet/me")
    return next(w["address"] for w in j["wallets"] if w["chain"] == "evm")


def pin(path):
    """Pin a local image to IPFS and return a gateway URL Bankr can fetch server-side."""
    from devbot import http, image                     # only this path needs devbot
    art = Path(path).read_bytes()
    ext, mime = image.kind(art)
    body, ctype = http.multipart({"network": "public"}, {"file": (f"art.{ext}", art, mime)})
    raw = http.request("POST", "https://uploads.pinata.cloud/v3/files", body=body, timeout=180,
                       headers={"Authorization": f"Bearer {os.environ['PINATA_JWT']}", "Content-Type": ctype})
    url = "https://gateway.pinata.cloud/ipfs/" + json.loads(raw)["data"]["cid"]
    http.request("GET", url, timeout=60)               # Bankr fetches it: prove the gateway serves it
    return url


def launch(a):
    if a.image_file:
        a.image = pin(a.image_file)
        print("pinned:", a.image)
    body = {"tokenName": a.name, "tokenSymbol": a.symbol, "chain": "base", "provider": "doppler",
            "feeRecipient": {"type": "wallet", "value": a.recipient or wallet()},
            "quoteOnlyFees": a.quote_only_fees, "disableVesting": a.no_vesting,
            "simulateOnly": not a.live}
    # The wire field is "image", NOT "imageUrl": @bankr/cli sends `image` and the API silently
    # accepts-and-drops unknown keys (a bogus field still returns 200), so imageUrl launched
    # two tokens with no picture at all. tweetUrl and websiteUrl are spelled as sent.
    for k, v in (("image", a.image), ("tweetUrl", a.tweet), ("websiteUrl", a.website)):
        if v:
            body[k] = v
    code, j = call("POST", "/token-launches/deploy", body)
    print(code, json.dumps(j, indent=2))
    if a.live and code in (200, 201) and j.get("tokenAddress"):
        LEDGER.parent.mkdir(exist_ok=True)
        with LEDGER.open("a") as f:
            f.write(json.dumps({"ts": int(time.time()), "name": a.name, "symbol": a.symbol,
                                "token": j["tokenAddress"], "tx": j.get("txHash"), "resp": j}) + "\n")


def ledger():
    return [json.loads(l) for l in LEDGER.read_text().splitlines()] if LEDGER.exists() else []


def fees(_):
    for r in ledger():
        code, j = call("GET", f"/token-launches/{r['token']}/fees?days=30", auth=False)
        print(r["symbol"], r["token"], code, json.dumps(j)[:400])


def claim(_):
    for r in ledger():
        code, j = call("POST", f"/token-launches/{r['token']}/fees/claim", {})
        print(r["symbol"], code, json.dumps(j)[:300])


if __name__ == "__main__":
    load_env()
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    l = sp.add_parser("launch")
    l.add_argument("name"); l.add_argument("symbol")
    l.add_argument("--image"); l.add_argument("--image-file"); l.add_argument("--tweet")
    l.add_argument("--website")
    l.add_argument("--recipient"); l.add_argument("--live", action="store_true")
    l.add_argument("--quote-only-fees", action="store_true"); l.add_argument("--no-vesting", action="store_true")
    l.set_defaults(fn=launch)
    sp.add_parser("fees").set_defaults(fn=fees)
    sp.add_parser("claim").set_defaults(fn=claim)
    a = ap.parse_args()
    a.fn(a)
