"""EXPLORATORY: is 'first launch today x dev buy 0.5-2 SOL' a property of the LAUNCH or of the creator?
Same no-bundle subset as policy.py; creator fixed effects; creators with 2+ launches only."""
import collections, os, sys
import numpy as np
import bots as B
import levers as L

fi = B.NAMES.index("first_in_tape")
for path, other in ((sys.argv[1], sys.argv[2]), (sys.argv[2], sys.argv[1])):
    rows = B.rows_of(path, other)
    crs = collections.defaultdict(set)
    for r in rows:
        for w in r["blockset"]: crs[w].add(r["cr"])
    broad = {w for w, c in crs.items() if len(c) >= 50}
    S = [r for r in rows if all(w in broad for w in r["blockset"])]
    if os.environ.get("STANDARD_ONLY") == "1":   # drop Mayhem coins (the agent traded them)
        S = [r for r in S if not r["mayhem"]]
    feats = lambda r: [r["x"][fi], 0.5 < r["devbuy"] <= 2, r["devbuy"] > 2,
                       r["x"][fi] and 0.5 < r["devbuy"] <= 2, r["x"][fi] and r["devbuy"] > 2,
                       0 < r["devbuy"] <= 0.5]
    names = ["first", "db_0.5_2", "db_gt2", "first x db_0.5_2", "first x db_gt2", "db_0_0.5"]
    net = np.array([r["net"] for r in S]); y = np.clip(net, *np.percentile(net, [1, 99]))
    for label, yy in (("NET winsor 1/99", y), ("P(broad sniper)", np.array([float(bool(r["blockset"])) for r in S]))):
        byc = collections.defaultdict(list)
        for r, v in zip(S, yy): byc[r["cr"]].append((v, feats(r)))
        Y, X, G = [], [], []
        for cr, lst in byc.items():
            if len(lst) < 2: continue
            ys = np.array([a for a, _ in lst], float); xs = np.array([b for _, b in lst], float)
            Y.extend(ys - ys.mean()); X.extend(xs - xs.mean(0)); G.extend([cr] * len(lst))
        Y, X, G = np.array(Y), np.array(X), np.array(G)
        beta, se = L.cluster_ols(X, Y, G)
        print(f"{path.split('/')[-1][5:13]} {label:16} ({len(set(G)):,} creators, {len(Y):,}): " +
              ", ".join(f"{n} {b:+.4f} (t {b/e:+.1f})" for n, b, e in zip(names, beta, se)))
        # how many creators actually vary the dev buy on their first vs later launches?
    fm = collections.Counter((r["cr"]) for r in S if r["x"][fi] and 0.5 < r["devbuy"] <= 2)
    multi = {cr for cr, l in collections.Counter(r["cr"] for r in S).items() if l >= 2}
    print(f"   creators with 2+ launches whose FIRST had a 0.5-2 SOL dev buy: {len(set(fm) & multi):,}")
