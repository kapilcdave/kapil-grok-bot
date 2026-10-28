"""EXPLORATORY: the per-launch distribution of the policy a 0.09-SOL wallet can afford: non-Mayhem, no
creator-linked block buyers, dev buy 0 or <= 0.05 SOL, first launch today (fresh) vs later."""
import collections, sys
import numpy as np
import bots as B

fi = B.NAMES.index("first_in_tape")
for path, other in ((sys.argv[1], sys.argv[2]), (sys.argv[2], sys.argv[1])):
    rows = B.rows_of(path, other)
    crs = collections.defaultdict(set)
    for r in rows:
        for w in r["blockset"]: crs[w].add(r["cr"])
    broad = {w for w, c in crs.items() if len(c) >= 50}
    S = [r for r in rows if not r["mayhem"] and all(w in broad for w in r["blockset"])]
    print(f"\n#### {path.split('/')[-1]}  (first-hour creator fee; fee >= 0.91 SOL needs ~303 SOL of volume)")
    print("cell                      n     median NET  mean NET  P(NET>0)  P(fee>=.01) P(fee>=.1) P(fee>=.91) max fee")
    for lab, keep in (("fresh, no dev buy", lambda r: r["x"][fi] and r["devbuy"] == 0),
                      ("fresh, dev buy <=0.05", lambda r: r["x"][fi] and 0 < r["devbuy"] <= 0.05),
                      ("repeat, no dev buy", lambda r: not r["x"][fi] and r["devbuy"] == 0),
                      ("repeat, dev buy <=0.05", lambda r: not r["x"][fi] and 0 < r["devbuy"] <= 0.05)):
        g = [r for r in S if keep(r)]
        n = np.array([r["net"] for r in g]); f = np.array([r["fee"] for r in g])
        print(f"{lab:24} {len(g):6,}  {np.median(n):+.4f}    {n.mean():+.4f}   {np.mean(n > 0):6.1%}   {np.mean(f >= .01):7.2%}   "
              f"{np.mean(f >= .1):7.2%}   {np.mean(f >= .91):7.3%}   {f.max():.3f}")
