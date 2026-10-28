"""EXPLORATORY: are launch-block buyers the creator's own bundle or outside snipers? A wallet whose
launch-block buys all sit under ONE creator is that creator's bundle; one seen in the launch blocks of
3+ distinct creators is a sniper. Re-score dev NET by dev-buy bucket on each kind of launch."""
import collections, sys
import numpy as np
import bots as B

for path, other in ((sys.argv[1], sys.argv[2]), (sys.argv[2], sys.argv[1])):
    rows = B.rows_of(path, other)
    crs = collections.defaultdict(set)
    for r in rows:
        for w in r["blockset"]:
            crs[w].add(r["cr"])
    kind = {w: ("bundle" if len(c) == 1 else "sniper" if len(c) >= 3 else "mixed") for w, c in crs.items()}
    print(f"\n#### {path.split('/')[-1]}: launch-block wallets {len(kind):,}: " +
          ", ".join(f"{k} {v:,}" for k, v in collections.Counter(kind.values()).items()))
    def label(r):
        ks = {kind[w] for w in r["blockset"]}
        if not ks: return "no block buyer"
        if ks == {"sniper"}: return "snipers only"
        if "sniper" not in ks: return "bundle/mixed only"
        return "both"
    groups = collections.defaultdict(list)
    for r in rows: groups[label(r)].append(r)
    for g in ("no block buyer", "snipers only", "bundle/mixed only", "both"):
        sub = groups[g]
        print(f"[{g}] {len(sub):,} launches -- bucket: n | median fee | median NET | mean NET | P(NET>0)")
        for i, (lo, hi) in enumerate(B.DEVBUY_EDGES):
            s = [r for r in sub if r["b"] == i]
            if len(s) < 30: continue
            n = np.array([r["net"] for r in s]); f = np.array([r["fee"] for r in s])
            lab = "none" if i == 0 else (f"({lo:g},{hi:g}]" if hi < 1e9 else f">{lo:g}")
            print(f"  {lab:9} {len(s):6,} | {np.median(f):.5f} | {np.median(n):+.5f} | {n.mean():+.5f} | {np.mean(n > 0):.1%}")
