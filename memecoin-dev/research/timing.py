"""EXPLORATORY: when do returning (v2) humans buy, vs bots and sybils?"""
import collections, sys
import numpy as np
import levers as L
a, b = sys.argv[1:3]
oc, _om, ot = L.load(b)
returning = {u for mint, rows in ot.items() if mint in oc for _t, u, side, *_ in rows if side == "buy"}
del oc, ot
creates, metas, trades = L.load(a)
bought = collections.defaultdict(set)
for mint, rows in trades.items():
    if mint in creates:
        for ts, user, side, sol, sig in rows:
            if side == "buy": bought[user].add(mint)
d_h, d_bot, d_one = [], [], []
vol = collections.Counter()
for mint, c in creates.items():
    if abs(c["v_sol"] * c["v_tok"] / L.STD - 1) > 0.01: continue
    t0 = c["ts"]; seen = set()
    for ts, user, side, sol, sig in trades.get(mint, ()):
        dt = ts - t0
        if user == c["creator"] or dt > 3600: continue
        kind = "human" if len(bought[user]) <= 10 and user in returning and dt >= 2 else ("bot" if len(bought[user]) > 10 else "single-use/launch-block")
        b10 = "0-10s" if dt <= 10 else "10-30s" if dt <= 30 else "30s-2m" if dt <= 120 else "2-10m" if dt <= 600 else "10-60m"
        vol[(kind, b10)] += sol
        if side == "buy" and user not in seen:
            seen.add(user)
            (d_h if kind == "human" else d_bot if kind == "bot" else d_one).append(dt)
for lab, d in (("returning humans", d_h), ("bots (>10 coins/day)", d_bot), ("single-use / launch-block", d_one)):
    d = np.array(d)
    print(f"{lab:26} first-buy delay: p10 {np.percentile(d,10):4.0f}s p25 {np.percentile(d,25):4.0f}s median {np.median(d):5.0f}s p75 {np.percentile(d,75):5.0f}s | within 30s {np.mean(d<=30):5.1%}")
tot = sum(vol.values())
print("\nshare of all non-creator volume (first hour) by who x when:")
for kind in ("bot", "single-use/launch-block", "human"):
    print(f"  {kind:24}" + "".join(f" {b}: {vol[(kind, b)]/tot:5.1%}" for b in ("0-10s", "10-30s", "30s-2m", "2-10m", "10-60m")))
