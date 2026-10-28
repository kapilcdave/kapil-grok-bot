"""EXPLORATORY: creator fixed effects. Same levers as the prereg, demeaned within creator, on
creators with 2+ launches in the tape, so 'serious creators do everything' can't explain a lever."""
import collections, sys
from urllib.parse import urlparse
import numpy as np
import levers as L
from devbot.trends import terms

def run(path, other):
    oc, _om, ot = L.load(other)
    returning = {u for mint, rows in ot.items() if mint in oc for _t, u, side, *_ in rows if side == "buy"}
    del oc, ot
    creates, metas, trades = L.load(path)
    bought = collections.defaultdict(set)
    for mint, rows in trades.items():
        if mint in creates:
            for ts, user, side, sol, sig in rows:
                if side == "buy": bought[user].add(mint)
    rows, tools = [], collections.defaultdict(list)
    for mint, c in creates.items():
        m = metas.get(mint)
        if not m or not m.get("ok") or abs(c["v_sol"] * c["v_tok"] / L.STD - 1) > 0.01: continue
        t0, cr = c["ts"], c["creator"]; first = {}; db = 0.0
        for ts, user, side, sol, sig in trades.get(mint, ()):
            if sig == c["sig"] and user == cr and side == "buy": db += sol
            if side == "buy" and user != cr and ts - t0 <= L.WINDOW_S: first.setdefault(user, ts)
        h = sum(1 for u, ts in first.items() if ts - t0 >= 2 and len(bought[u]) <= 10 and u in returning)
        k = L.tw_kind(m.get("twitter")); name = c.get("name") or ""
        host = urlparse(c.get("uri") or "").netloc
        tools[host].append(h)
        rows.append((cr, np.log1p(h), [k == "x_post", k == "x_profile", bool(m.get("website")), bool(m.get("description")),
                     0 < db <= 0.1, 0.1 < db <= 1, db > 1, name.isupper(), 13 <= (t0 // 3600) % 24 <= 22]))
    names = ["x_post", "x_profile", "website", "description", "devbuy_le0.1", "devbuy_0.1_1", "devbuy_gt1", "all_caps", "us_hours"]
    byc = collections.defaultdict(list)
    for cr, y, x in rows: byc[cr].append((y, x))
    Y, X, G = [], [], []
    for cr, lst in byc.items():
        if len(lst) < 2: continue
        ys = np.array([y for y, _ in lst]); xs = np.array([x for _, x in lst], float)
        Y.extend(ys - ys.mean()); X.extend(xs - xs.mean(0)); G.extend([cr] * len(lst))
    Y, X, G = np.array(Y), np.array(X), np.array(G)
    keep = X.std(0) > 0
    beta, se = L.cluster_ols(X[:, keep], Y, G)
    print("tools (n>=200): " + "; ".join(f"{h} n={len(v)} meanH {np.mean(v):.2f} P5 {np.mean(np.array(v)>=5):.1%}" for h, v in sorted(tools.items(), key=lambda kv: -len(kv[1])) if len(v) >= 200))
    print(f"== {path.split('/')[-1]}: creator FE, {len(set(G)):,} creators, {len(Y):,} launches")
    for n, b, s in zip([n for n, k in zip(names, keep) if k], beta, se):
        print(f"  {n:13} {b:+.4f} se {s:.4f} t {b/s:+5.1f}  x{np.exp(b):.2f}")
a, b = sys.argv[1:3]
run(a, b); run(b, a)
