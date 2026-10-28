"""Early pump.fun trades of given mints, straight from the chain (QuickNode HTTP, endpoint in the env file).

  python -m research.chaintrades MINTS.json OUT.json [--first 40] [--span 120]

MINTS.json is a list of mints. OUT.json maps mint -> {create_slot, create_time, trades:[{dt, side, sol,
tok, px, signer}]} for the first `--first` successful txs within `--span` s of the create. dt is seconds
since the create from SLOT differences (0.4 s a slot: blockTime only has 1 s resolution). The bonding
curve is the biggest token holder in the tx; its lamport change is the trade's SOL (pump.fun's fee goes
elsewhere, so it is net of fee) and its token change is the tokens, so px = SOL per token paid.
Cost: 1 getSignaturesForAddress (+ pages while 1000 come back) and <= --first getTransaction a coin.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

SLOT_S = 0.4


def endpoint() -> str:
    if os.environ.get("CHAINTRADES_URL"):        # e.g. Helius when QuickNode's daily cap is spent
        return os.environ["CHAINTRADES_URL"]
    for line in open(Path.home() / ".config/solana-rpc/quicknode.env"):
        if line.startswith("QUICKNODE_SOLANA_HTTP="):
            return line.split("=", 1)[1].strip()
    sys.exit("QUICKNODE_SOLANA_HTTP not set")


class Rpc:
    def __init__(self) -> None:
        self.url, self.calls = endpoint(), 0

    def __call__(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(4):
            try:
                req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
                r = json.load(urllib.request.urlopen(req, timeout=30))
                self.calls += 1
                if "error" in r:
                    if r["error"].get("code") == -32003:
                        sys.exit("QuickNode daily request cap reached: stop and resume tomorrow")
                    raise RuntimeError(r["error"])
                return r["result"]
            except RuntimeError:
                raise
            except Exception:
                time.sleep(1 + attempt)
        raise RuntimeError(f"{method} failed")


def decode(tx: dict, mint: str):
    """(side, sol, tok, signer) for one tx's bonding-curve movement; None if the curve isn't in it."""
    m, keys = tx["meta"], [k if isinstance(k, str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
    pre = {b["owner"]: float(b["uiTokenAmount"]["uiAmountString"] or 0) for b in m.get("preTokenBalances", []) if b["mint"] == mint}
    post = {b["owner"]: float(b["uiTokenAmount"]["uiAmountString"] or 0) for b in m.get("postTokenBalances", []) if b["mint"] == mint}
    if not post:
        return None
    curve = max(post, key=lambda o: max(post.get(o, 0), pre.get(o, 0)))
    if curve not in keys:
        return None
    i = keys.index(curve)
    dsol = (m["postBalances"][i] - m["preBalances"][i]) / 1e9
    dtok = post.get(curve, 0) - pre.get(curve, 0)
    if dtok == 0 or dsol == 0 or (dtok > 0) == (dsol > 0):    # a trade moves curve SOL and tokens opposite ways
        return None
    return ("sell" if dtok > 0 else "buy"), abs(dsol), abs(dtok), keys[0]


def one(rpc: Rpc, mint: str, first: int, span: float):
    sigs, before = [], None
    for _ in range(3):
        page = rpc("getSignaturesForAddress", [mint, {"limit": 1000, **({"before": before} if before else {})}])
        sigs += page
        if len(page) < 1000:
            break
        before = page[-1]["signature"]
    sigs = [s for s in sigs if not s.get("err")][::-1]    # oldest first
    if not sigs:
        return None
    s0 = sigs[0]["slot"]
    keep = [s for s in sigs if (s["slot"] - s0) * SLOT_S <= span][:first]
    out = {"create_slot": s0, "create_time": sigs[0].get("blockTime"), "n_sigs": len(sigs), "trades": []}
    for s in keep:
        try:
            tx = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
        except RuntimeError:
            continue
        d = decode(tx, mint) if tx else None
        if d and s is not sigs[0]:    # the first tx is the create, not a trade
            out["trades"].append({"dt": round((s["slot"] - s0) * SLOT_S, 2), "side": d[0], "sol": d[1], "tok": d[2], "px": d[1] / d[2],
                                  "signer": d[3], "slot": s["slot"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mints")
    ap.add_argument("out")
    ap.add_argument("--first", type=int, default=40)
    ap.add_argument("--span", type=float, default=120)
    a = ap.parse_args()
    mints = json.load(open(a.mints))
    done = json.load(open(a.out)) if os.path.exists(a.out) else {}
    rpc = Rpc()
    for n, m in enumerate(mints, 1):
        if m in done:
            continue
        done[m] = one(rpc, m, a.first, a.span)
        json.dump(done, open(a.out, "w"))
        print(f"{n}/{len(mints)} {m[:6]} trades={len((done[m] or {}).get('trades', []))} calls={rpc.calls}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
