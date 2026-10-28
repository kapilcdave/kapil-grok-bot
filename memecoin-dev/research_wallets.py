"""Disclosed research wallets: buy-only, published up front, excluded from the buyer metric.

Question: how many unique buyers OUTSIDE our own set does an AI-safety memecoin attract? The wallets
exist only to take part openly. They are NOT meant to look organic, so there is no swap "aging" and no
randomized timing: buys follow a plain fixed-spacing schedule you state in the disclosure.

    python research_wallets.py gen    --n 100                  # keys (0600) + public manifest.json
    python research_wallets.py fund   --per-sol 0.02 --send     # main wallet -> each research wallet
    python research_wallets.py buy    --mint MINT --buy-sol 0.005 --spacing-s 600 --send
    python research_wallets.py metric --mint MINT               # unique buyers not in the manifest
    python research_wallets.py sweep  --send                    # everything back to main

Rules enforced in code: there is no sell path here, `buy` refuses unless manifest.json says
`disclosed: true` (set it with `disclose` only after the label is public), and every action is a
dry run without --send.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from solders.pubkey import Pubkey

import launch_one as lo
from devbot import http, pump, wallets as wallets_mod

DIR = Path(__file__).resolve().parent / "research" / "data" / "research-wallets"
MANIFEST = DIR / "manifest.json"
KEYS = lo.STATE_DIR / "research-wallets"   # apart from devbot's rotated store (sweep_idle empties that one)
FEE_RESERVE_SOL = 0.003                    # kept per wallet so a buy never fails on fees
TRADES = "https://frontend-api-v3.pump.fun/trades/all/{mint}?limit=200&offset={offset}"


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def save_manifest(m: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2) + "\n")


def cmd_gen(a) -> int:
    store = wallets_mod.Wallets(KEYS)
    if MANIFEST.exists():
        sys.exit(f"{MANIFEST} exists; refusing to overwrite a published wallet list")
    pubs = [str(store.new().pubkey()) for _ in range(a.n)]
    save_manifest({"purpose": "AI-safety memecoin buyer-attraction study; these wallets buy and hold, never sell",
                   "disclosed": False, "wallets": pubs})
    print(f"{a.n} wallets -> {MANIFEST} (keys in {KEYS}); publish the list, then run `disclose`")
    return 0


def cmd_disclose(a) -> int:
    m = load_manifest()
    m["disclosed"] = True
    m["disclosed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_manifest(m)
    return 0


def cmd_fund(a) -> int:
    m = load_manifest()
    rpc = pump.Rpc(lo.rpc_urls())
    main = pump.load_keypair(lo.KEYPAIR_PATH)
    need = a.per_sol * len(m["wallets"])
    print(f"fund {len(m['wallets'])} x {a.per_sol} SOL = {need:g} SOL from {main.pubkey()}")
    if not a.send:
        return 0
    for w in m["wallets"]:
        print(w, wallets_mod.fund(rpc, main, Pubkey.from_string(w), a.per_sol))
    return 0


def cmd_buy(a) -> int:
    m = load_manifest()
    if not m.get("disclosed"):
        sys.exit("manifest.disclosed is false: publish the wallet list and label, then run `disclose`")
    rpc = pump.Rpc(lo.rpc_urls())
    store = wallets_mod.Wallets(KEYS)
    order = m["wallets"][: a.count or None]
    print(f"{len(order)} buys of {a.buy_sol:g} SOL on {a.mint}, one every {a.spacing_s:g}s")
    for i, w in enumerate(order):
        if a.send:
            kp = store.get(w)
            tx = pump._build({"publicKey": w, "action": "buy", "mint": a.mint, "denominatedInSol": "true",
                              "amount": a.buy_sol, "slippage": lo.SLIPPAGE_PCT,
                              "priorityFee": lo.PRIORITY_FEE_SOL, "pool": "auto"}, [kp])
            sig = rpc.send(tx)
            print(i, w, sig, flush=True)
            time.sleep(a.spacing_s)
        else:
            print(i, w, "(dry run)")
    return 0


def buyers(mint: str) -> set[str]:
    out: set[str] = set()
    for offset in range(0, 20_000, 200):
        page = http.get_json(TRADES.format(mint=mint, offset=offset), headers={"User-Agent": "Mozilla/5.0"})
        if not page:
            break
        out |= {t["user"] for t in page if t.get("is_buy")}
        if len(page) < 200:
            break
    return out


def cmd_metric(a) -> int:
    ours = set(load_manifest()["wallets"])
    every = buyers(a.mint)
    outside = every - ours
    print(json.dumps({"mint": a.mint, "buyers_total": len(every), "ours": len(every & ours),
                      "unique_outside_buyers": len(outside)}))
    return 0


def cmd_sweep(a) -> int:
    m = load_manifest()
    rpc = pump.Rpc(lo.rpc_urls())
    main = pump.load_keypair(lo.KEYPAIR_PATH)
    store = wallets_mod.Wallets(KEYS)
    for w in m["wallets"]:
        if a.send:
            print(w, wallets_mod.sweep(rpc, main, store.get(w)))
        else:
            print(w, "(dry run)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen"); g.add_argument("--n", type=int, default=100); g.set_defaults(fn=cmd_gen)
    sub.add_parser("disclose").set_defaults(fn=cmd_disclose)
    f = sub.add_parser("fund"); f.add_argument("--per-sol", type=float, default=0.02)
    f.add_argument("--send", action="store_true"); f.set_defaults(fn=cmd_fund)
    b = sub.add_parser("buy"); b.add_argument("--mint", required=True)
    b.add_argument("--buy-sol", type=float, default=0.005); b.add_argument("--spacing-s", type=float, default=600)
    b.add_argument("--count", type=int, default=0); b.add_argument("--send", action="store_true")
    b.set_defaults(fn=cmd_buy)
    mt = sub.add_parser("metric"); mt.add_argument("--mint", required=True); mt.set_defaults(fn=cmd_metric)
    s = sub.add_parser("sweep"); s.add_argument("--send", action="store_true"); s.set_defaults(fn=cmd_sweep)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
