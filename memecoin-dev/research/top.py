"""EXPLORATORY: what the coins humans piled into actually look like (top by human buyers)."""
import collections, sys
import numpy as np
import levers as L
creates, metas, trades = L.load(sys.argv[1])
bought = collections.defaultdict(set)
for mint, rows in trades.items():
    if mint in creates:
        for ts, user, side, sol, sig in rows:
            if side == "buy": bought[user].add(mint)
ncreator = collections.Counter(c["creator"] for c in creates.values())
out = []
for mint, c in creates.items():
    m = metas.get(mint) or {}
    if abs(c["v_sol"] * c["v_tok"] / L.STD - 1) > 0.01: continue
    t0, cr = c["ts"], c["creator"]; first = {}; db = 0.0
    for ts, user, side, sol, sig in trades.get(mint, ()):
        if sig == c["sig"] and user == cr and side == "buy": db += sol
        if side == "buy" and user != cr and ts - t0 <= L.WINDOW_S: first.setdefault(user, ts)
    hum = sorted(ts - t0 for u, ts in first.items() if ts - t0 >= 2 and len(bought[u]) <= 10)
    if hum: out.append((len(hum), hum, c, m, db, ncreator[cr]))
out.sort(key=lambda r: -r[0])
H = np.array([r[0] for r in out])
allh = np.concatenate([r[1] for r in out])
print(f"human first-buy delay after launch (all coins): p25 {np.percentile(allh,25):.0f}s, median {np.median(allh):.0f}s, p75 {np.percentile(allh,75):.0f}s, share within 30s {np.mean(allh<=30):.1%}, within 5 min {np.mean(allh<=300):.1%}")
top = out[:300]
print(f"top 300 coins: one-off creators {np.mean([r[5]==1 for r in top]):.0%}, x_post {np.mean([L.tw_kind(r[3].get('twitter'))=='x_post' for r in top]):.0%}, website {np.mean([bool(r[3].get('website')) for r in top]):.0%}, median dev buy {np.median([r[4] for r in top]):.2f} SOL, median first human at {np.median([r[1][0] for r in top]):.0f}s, median 10th human at {np.median([r[1][min(9,len(r[1])-1)] for r in top]):.0f}s")
for h, hum, c, m, db, nc in out[:25]:
    print(f"{h:4} humans | 1st {hum[0]:4.0f}s 10th {hum[min(9,len(hum)-1)]:5.0f}s | dev {db:5.2f} SOL | creator launches {nc:3} | {L.tw_kind(m.get('twitter')):9} web={'Y' if m.get('website') else '-'} | {c['name'][:30]!r} ${c['symbol'][:10]} | {(m.get('description') or '')[:70]!r}")
