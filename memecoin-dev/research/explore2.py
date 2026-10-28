"""EXPLORATORY follow-ups to the prereg (not decisive): within-creator launch order, dev-buy dose,
X post shared (a story post many coins link) vs unique (likely the creator's own)."""
import collections, sys
import numpy as np
sys.argv += [] ; import levers as L

def run(path):
    creates, metas, trades = L.load(path)
    bought = collections.defaultdict(set)
    for mint, rows in trades.items():
        if mint in creates:
            for ts, user, side, sol, sig in rows:
                if side == "buy": bought[user].add(mint)
    H, dev, std = {}, {}, {}
    for mint, c in creates.items():
        t0, cr = c["ts"], c["creator"]; first = {}; db = 0.0
        for ts, user, side, sol, sig in trades.get(mint, ()):
            if sig == c["sig"] and user == cr and side == "buy": db += sol
            if side == "buy" and user != cr and ts - t0 <= L.WINDOW_S: first.setdefault(user, ts)
        H[mint] = sum(1 for u, ts in first.items() if ts - t0 >= 2 and len(bought[u]) <= 10)
        dev[mint] = db
        std[mint] = abs(c["v_sol"] * c["v_tok"] / L.STD - 1) <= 0.01
    print(f"== {path.split('/')[-1]}")
    # 1) within creator: launch #1 vs #2 (paired), std curves only
    seq = collections.defaultdict(list)
    for c in sorted(creates.values(), key=lambda c: c["ts"]):
        if std[c["mint"]]: seq[c["creator"]].append(c["mint"])
    for k in (2, 3):
        d = [np.log1p(H[s[0]]) - np.log1p(np.mean([H[m] for m in s[1:k]])) for s in seq.values() if len(s) >= k]
        d = np.array(d); rng = np.random.default_rng(0)
        bs = [rng.choice(d, len(d)).mean() for _ in range(2000)]
        print(f"  within-creator, first launch minus next {k-1} (log1p H): {d.mean():+.4f} CI [{np.percentile(bs,2.5):+.4f},{np.percentile(bs,97.5):+.4f}] n={len(d)} creators")
    first_h = [H[s[0]] for s in seq.values() if len(s) >= 2]; later_h = [H[m] for s in seq.values() if len(s) >= 2 for m in s[1:]]
    one_off = [H[s[0]] for s in seq.values() if len(s) == 1]
    print(f"  mean H: one-off creators' launch {np.mean(one_off):.2f} | repeat creators' FIRST {np.mean(first_h):.2f} | their later {np.mean(later_h):.2f}")
    # 2) dev buy dose (std, meta ok)
    bins = [(0, 0), (1e-9, 0.1), (0.1, 0.3), (0.3, 1), (1, 3), (3, 1e9)]
    for lo, hi in bins:
        hs = [H[m] for m in creates if std[m] and m in metas and metas[m].get("ok") and (dev[m] == 0 if hi == 0 else lo < dev[m] <= hi)]
        if hs: print(f"  dev buy {lo:g}-{hi:g} SOL: n={len(hs):6,} mean H {np.mean(hs):6.2f}  P(H>=5) {np.mean(np.array(hs)>=5):.1%}")
    # 3) X post shared vs unique
    cnt = collections.Counter()
    for m in metas.values():
        if m.get("ok") and L.tw_kind(m.get("twitter")) == "x_post":
            cnt[m["twitter"].split("?")[0].lower()] += 1
    for lab, cond in (("x_post unique (likely own)", lambda u: cnt[u] == 1), ("x_post shared by 2+ coins (story post)", lambda u: cnt[u] >= 2)):
        hs = [H[mm] for mm, m in metas.items() if std.get(mm) and m.get("ok") and L.tw_kind(m.get("twitter")) == "x_post" and cond(m["twitter"].split("?")[0].lower())]
        print(f"  {lab}: n={len(hs):,} mean H {np.mean(hs):.2f} P(H>=5) {np.mean(np.array(hs)>=5):.1%}")
    hs = [H[mm] for mm, m in metas.items() if std.get(mm) and m.get("ok") and L.tw_kind(m.get("twitter")) == "none"]
    print(f"  no X link: n={len(hs):,} mean H {np.mean(hs):.2f} P(H>=5) {np.mean(np.array(hs)>=5):.1%}")

for p in sys.argv[1:]: run(p)
