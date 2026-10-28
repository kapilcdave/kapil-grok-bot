"""Backfill the swarm wallets' recent history, in the same row shape as research/swarmwatch.py.

    python -m research.swarmback --wallets research/data/swarm_wallets.txt [--per-wallet 100]

Writes research/data/swarmback-YYYYMMDD.jsonl. Read only: getSignaturesForAddress + getTransaction on Helius.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from devbot import http
from research.swarmwatch import env, summarize


def rpc(url: str, method: str, params: list):
    """Helius's free plan rate-limits a burst (1,681 of 1,904 fetches came back empty at 8 threads, 10-15),
    so back off and retry instead of recording a gap."""
    for wait in (0.0, 0.5, 1.0, 2.0, 4.0, 8.0):
        time.sleep(wait)
        try:
            body = json.loads(http.post_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
            if body.get("result") is not None:
                return body["result"]
        except Exception:
            pass
    return None


def endpoints() -> list[str]:
    """Helius, QuickNode and the public RPC: each is rate-limited on its own, so spread the fetches."""
    import launch_one
    urls = [env("HTTP_URL")] + launch_one.rpc_urls()
    return list(dict.fromkeys(urls))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallets", required=True)
    ap.add_argument("--per-wallet", type=int, default=100)
    a = ap.parse_args()
    urls = endpoints()
    url = urls[0]
    wallets = Path(a.wallets).read_text().split()
    jobs = []
    for w in wallets:
        for s in rpc(url, "getSignaturesForAddress", [w, {"limit": a.per_wallet}]) or []:
            if not s.get("err"):
                jobs.append((w, s["signature"]))

    def one(ij):
        i, (w, sig) = ij
        tx = None
        for k in range(len(urls)):          # this job's endpoint first, then the others
            tx = rpc(urls[(i + k) % len(urls)], "getTransaction",
                     [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}])
            if tx:
                break
        if not tx:
            return {"wallet": w, "sig": sig, "missing": True}
        return {"wallet": w, "sig": sig, "slot": tx["slot"], "block_time": tx.get("blockTime"), **summarize(tx, w)}

    out = Path(__file__).resolve().parent / "data" / f"swarmback-{datetime.now(timezone.utc):%Y%m%d}.jsonl"
    with cf.ThreadPoolExecutor(3 * len(urls)) as ex, out.open("w") as f:
        for row in ex.map(one, enumerate(jobs)):
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"{len(jobs)} transactions from {len(wallets)} wallets -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
