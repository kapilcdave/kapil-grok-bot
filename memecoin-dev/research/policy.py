"""EXPLORATORY: the UNCONDITIONAL value of a launch policy for a creator who does not bundle.
Keep launches whose launch block is empty or holds only broad snipers (>= 50 creators), i.e. drop every
launch with a creator-linked block wallet; score dev NET by (first launch today?) x (dev-buy bucket)."""
import collections, sys
import numpy as np
import bots as B

def ci(n, k=2000, rng=np.random.default_rng(0)):
    m = [np.mean(np.clip(rng.choice(n, len(n)), None, np.percentile(n, 99))) for _ in range(k)]
    return np.percentile(m, [2.5, 97.5])

for path, other in ((sys.argv[1], sys.argv[2]), (sys.argv[2], sys.argv[1])):
    rows = B.rows_of(path, other)
    crs = collections.defaultdict(set)
    for r in rows:
        for w in r["blockset"]: crs[w].add(r["cr"])
    broad = {w for w, c in crs.items() if len(c) >= 50}
    S = [r for r in rows if all(w in broad for w in r["blockset"])]
    print(f"\n#### {path.split('/')[-1]}: {len(S):,} no-bundle launches of {len(rows):,}")
    print("first? bucket   n     P(sniper)  median NET   mean NET (top 1% capped) [95% CI]   P(NET>0)")
    first_idx = B.NAMES.index("first_in_tape")
    for first in (True, False):
        for i, (lo, hi) in enumerate(B.DEVBUY_EDGES):
            s = [r for r in S if r["b"] == i and bool(r["x"][first_idx]) == first]
            if len(s) < 50: continue
            n = np.array([r["net"] for r in s]); lo_, hi_ = ci(n)
            lab = "none" if i == 0 else (f"({lo:g},{hi:g}]" if hi < 1e9 else f">{lo:g}")
            cap = np.mean(np.clip(n, None, np.percentile(n, 99)))
            print(f"{'first' if first else 'later':6} {lab:9} {len(s):5,}  {np.mean([bool(r['blockset']) for r in s]):6.1%}   {np.median(n):+.4f}    {cap:+.4f} [{lo_:+.4f},{hi_:+.4f}]   {np.mean(n > 0):.1%}")
