"""Full, resumable trade tape for ONE mint, straight from the chain (QuickNode HTTP).

  python -m research.minttape MINT OUT.jsonl [--poll 45] [--minutes 180]

Appends one JSON object a transaction, oldest-first within a cycle, deduped by signature across runs, so
it can be stopped and resumed. Successful txs are decoded the same way as `chaintrades`: the bonding
curve (or pool) is the biggest token holder in the tx, its lamport change is the trade's SOL and its
token change the tokens. Failed txs are kept with `err` set and the signer only -- a failed buy in the
launch block is a rival sniper, and that count is the competition.

Cost: one getSignaturesForAddress a cycle (plus pages) and one getTransaction a new tx.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

SLOT_S = 0.4


def endpoint() -> str:
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
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"{method} failed")


def decode(tx: dict, mint: str):
    """(side, sol, tok, pool) for the pool-side movement; None if no pool leg is in the tx."""
    m = tx["meta"]
    keys = [k if isinstance(k, str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
    pre = {b["owner"]: float(b["uiTokenAmount"]["uiAmountString"] or 0) for b in m.get("preTokenBalances", []) if b["mint"] == mint}
    post = {b["owner"]: float(b["uiTokenAmount"]["uiAmountString"] or 0) for b in m.get("postTokenBalances", []) if b["mint"] == mint}
    if not post:
        return None
    pool = max(post, key=lambda o: max(post.get(o, 0), pre.get(o, 0)))
    if pool not in keys:
        return None
    i = keys.index(pool)
    dsol = (m["postBalances"][i] - m["preBalances"][i]) / 1e9
    dtok = post.get(pool, 0) - pre.get(pool, 0)
    if dtok == 0 or dsol == 0 or (dtok > 0) == (dsol > 0):
        return None
    return ("sell" if dtok > 0 else "buy"), abs(dsol), abs(dtok), pool


def holders(tx: dict, mint: str) -> dict:
    """Post-trade token balance a wallet, so a tape replay can rebuild the holder set."""
    return {b["owner"]: float(b["uiTokenAmount"]["uiAmountString"] or 0)
            for b in tx["meta"].get("postTokenBalances", []) if b["mint"] == mint and b.get("owner")}


def signatures(rpc: Rpc, mint: str, until: str | None) -> list:
    """Newest-first signatures, stopping at `until` (exclusive) when given."""
    out, before = [], None
    while True:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        if until:
            p["until"] = until
        page = rpc("getSignaturesForAddress", [mint, p])
        out += page
        if len(page) < 1000 or len(out) > 20000:
            return out
        before = page[-1]["signature"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mint")
    ap.add_argument("out")
    ap.add_argument("--poll", type=float, default=45)
    ap.add_argument("--minutes", type=float, default=180)
    a = ap.parse_args()

    out = Path(a.out)
    seen, newest = set(), None
    if out.exists():
        rows = [json.loads(l) for l in open(out) if l.strip()]
        seen = {r["sig"] for r in rows}
        if rows:
            newest = max(rows, key=lambda r: r["slot"])["sig"]

    rpc, deadline = Rpc(), time.time() + a.minutes * 60
    while True:
        cycle = time.time()
        sigs = [s for s in signatures(rpc, a.mint, newest) if s["signature"] not in seen][::-1]  # oldest first
        wrote = 0
        with open(out, "a") as fh:
            for s in sigs:
                row = {"sig": s["signature"], "slot": s["slot"], "t": s.get("blockTime"), "err": bool(s.get("err"))}
                try:
                    tx = rpc("getTransaction", [s["signature"],
                                                {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
                except RuntimeError:
                    tx = None
                if tx:
                    keys = [k if isinstance(k, str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
                    row["signer"] = keys[0]
                    row["fee"] = tx["meta"]["fee"]
                    row["cu"] = tx["meta"].get("computeUnitsConsumed")
                    row["logs"] = [x for x in (tx["meta"].get("logMessages") or []) if "Instruction:" in x][:12]
                    if not row["err"]:
                        d = decode(tx, a.mint)
                        if d:
                            row.update(side=d[0], sol=d[1], tok=d[2], px=d[1] / d[2], pool=d[3])
                        row["holders"] = holders(tx, a.mint)
                fh.write(json.dumps(row) + "\n")
                seen.add(s["signature"])
                wrote += 1
            fh.flush()
        if sigs:
            newest = sigs[-1]["signature"]
        print(f"[{time.strftime('%H:%M:%S')}] +{wrote} tx (total {len(seen)}) calls={rpc.calls}", flush=True)
        if time.time() > deadline:
            return 0
        time.sleep(max(1.0, a.poll - (time.time() - cycle)))


if __name__ == "__main__":
    sys.exit(main())
