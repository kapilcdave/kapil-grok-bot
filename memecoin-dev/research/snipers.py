"""EXPLORATORY: (1) dev NET when the launch block holds only BROAD snipers (wallets seen in launch blocks
of >= TH distinct creators, too many for one farm's bundle); (2) within creator, which levers make a
broad sniper land in the launch block at all (linear probability, creator FE)."""
import collections, sys
import numpy as np
import bots as B

for path, other in ((sys.argv[1], sys.argv[2]), (sys.argv[2], sys.argv[1])):
    rows = B.rows_of(path, other)
    crs = collections.defaultdict(set)
    for r in rows:
        for w in r["blockset"]:
            crs[w].add(r["cr"])
    nc = np.array([len(c) for c in crs.values()])
    print(f"\n#### {path.split('/')[-1]}: block wallets {len(nc):,}; creators/wallet p50 {np.median(nc):.0f} p90 {np.percentile(nc,90):.0f} p99 {np.percentile(nc,99):.0f} max {nc.max()}")
    for TH in (20, 50):
        broad = {w for w, c in crs.items() if len(c) >= TH}
        share = sum(1 for r in rows for w in r["blockset"] if w in broad) / max(sum(len(r["blockset"]) for r in rows), 1)
        print(f"-- TH={TH}: {len(broad):,} broad wallets carry {share:.1%} of launch-block buyer slots")
        clean = [r for r in rows if r["blockset"] and all(w in broad for w in r["blockset"])]
        print(f"[broad snipers only] {len(clean):,} launches -- bucket: n | median fee | median NET | mean NET | P(NET>0)")
        for i, (lo, hi) in enumerate(B.DEVBUY_EDGES):
            s = [r for r in clean if r["b"] == i]
            if len(s) < 30: continue
            n = np.array([r["net"] for r in s]); f = np.array([r["fee"] for r in s])
            lab = "none" if i == 0 else (f"({lo:g},{hi:g}]" if hi < 1e9 else f">{lo:g}")
            print(f"  {lab:9} {len(s):6,} | {np.median(f):.5f} | {np.median(n):+.5f} | {n.mean():+.5f} | {np.mean(n > 0):.1%}")
        y = np.array([float(any(w in broad for w in r["blockset"])) for r in rows])
        print(f"   P(broad sniper in launch block) overall {y.mean():.1%}")
        names, beta, se, ncr, n = B.fe(rows, y)
        print(f"   within-creator LPM ({ncr:,} creators, {n:,} launches): " + ", ".join(
            f"{nm} {b*100:+.1f}pp (t {b/e:+.1f})" for nm, b, e in zip(names, beta, se)))
