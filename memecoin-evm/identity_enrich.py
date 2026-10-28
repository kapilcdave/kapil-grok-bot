"""Resolve the EOA behind every Mint and Sell on the BSC launch tape.

The insider-sell exit overlay (the one pump.fun mechanism that replicated FORWARD) triggers on
the CREATOR's sell, not on any sell -- so the rule needs seller identity. The tape already keeps
`tx` per swap, and `eth_getTransactionByHash` is a plain lookup (no archive), so identity is
recoverable retroactively for launches already collected.

⛔ THIS SCRIPT COMPUTES NO RETURNS. The tape is frozen under
`preregistrations/bsc_pancake_curve_exit_20261012.md` amendment 2: exactly one evaluation at
n >= 1500. This builds a FEATURE and reports TRIGGER REACHABILITY only -- whether the overlay
can fire at all, and how soon -- which must be known before a rule is frozen
(a preregistered bucket must be reachable by the instrument). It prints no P&L.

    python3 -I identity_enrich.py            # enrich + reachability
    python3 -I identity_enrich.py --limit 20

Cache: data/identity_cache.json, keyed by tx hash, so re-runs are cheap and resumable.
Public BSC RPCs 403 plain urllib -- a User-Agent is required.
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

ENDPOINTS = ["https://bsc-rpc.publicnode.com", "https://binance.llamarpc.com",
             "https://bsc-dataseed1.binance.org", "https://bsc.meowrpc.com"]
HEADERS = {"content-type": "application/json", "user-agent": "Mozilla/5.0"}
TAPE = "data/sync_tape.jsonl"
CACHE = "data/identity_cache.json"
PACE_S = 0.08                      # the accumulator is sharing this endpoint; stay polite


def rpc(method, params, tries=3):
    for attempt in range(tries):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    url, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                     "params": params}).encode(), HEADERS)
                r = json.load(urllib.request.urlopen(req, timeout=20))
                if "result" in r and r["result"] is not None:
                    return r["result"]
            except Exception:
                pass
        time.sleep(min(2.0 * (attempt + 1), 4.0))      # capped: retention is the scarce resource
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    rows = [json.loads(l) for l in open(TAPE) if l.strip()]
    if args.limit:
        rows = rows[:args.limit]
    print(f"launches on tape: {len(rows)}   cached tx: {len(cache)}")

    need = set()
    for r in rows:
        for m in r["mints"]:
            need.add(m["tx"])
        for s in r["swaps"]:
            if not s["buy"]:
                need.add(s["tx"])
    todo = [h for h in need if h not in cache]
    print(f"tx to resolve: {len(need)}   already cached: {len(need)-len(todo)}   fetching: {len(todo)}")

    for i, h in enumerate(todo, 1):
        t = rpc("eth_getTransactionByHash", [h])
        cache[h] = (t or {}).get("from")
        time.sleep(PACE_S)
        if i % 100 == 0 or i == len(todo):
            json.dump(cache, open(CACHE, "w"))
            print(f"  resolved {i}/{len(todo)}  unresolved so far "
                  f"{sum(1 for v in cache.values() if v is None)}")
    json.dump(cache, open(CACHE, "w"))

    # ---- reachability of the overlay trigger. NO returns computed. ----
    stat = {"launches": 0, "no_deployer": 0, "deployer_sold": 0, "any_sold": 0,
            "open_buyer_sold": 0, "lag_blocks": [], "sell_rank": []}
    for r in rows:
        stat["launches"] += 1
        dep = cache.get(r["mints"][0]["tx"]) if r["mints"] else None
        if not dep:
            stat["no_deployer"] += 1
            continue
        sw = sorted(r["swaps"], key=lambda x: (x["blk"], x["txi"], x["li"]))
        if not sw:
            continue
        open_blk = sw[0]["blk"]
        sells = [s for s in sw if not s["buy"] and s["blk"] >= open_blk]
        if sells:
            stat["any_sold"] += 1
        hits = [s for s in sells if cache.get(s["tx"]) == dep]
        if hits:
            stat["deployer_sold"] += 1
            stat["lag_blocks"].append(hits[0]["blk"] - open_blk)
            stat["sell_rank"].append(sells.index(hits[0]) + 1)

    def q(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")

    n = stat["launches"]
    print("\n=== OVERLAY TRIGGER REACHABILITY (no P&L) ===")
    print(f"launches                              {n}")
    print(f"  deployer EOA unresolved             {stat['no_deployer']}")
    print(f"  had any sell after the open         {stat['any_sold']} ({stat['any_sold']/max(n,1):.0%})")
    print(f"  DEPLOYER itself sold                {stat['deployer_sold']} "
          f"({stat['deployer_sold']/max(n,1):.0%})  <-- the overlay's trigger rate")
    if stat["lag_blocks"]:
        lb = stat["lag_blocks"]
        print(f"  blocks from open to deployer sell   p25 {q(lb,.25):.0f}  median {q(lb,.5):.0f}  "
              f"p75 {q(lb,.75):.0f}  (0.45 s/block)")
        sr = stat["sell_rank"]
        print(f"  deployer's sell is the Nth sell     p25 {q(sr,.25):.0f}  median {q(sr,.5):.0f}  "
              f"p75 {q(sr,.75):.0f}")
    print("\nNo returns computed. Scoring is gated at n >= 1500 by the standing preregistration.")


if __name__ == "__main__":
    main()
