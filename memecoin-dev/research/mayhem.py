"""EXPLORATORY: is Mayhem mode worth it for a dev? Mayhem coins = the agent traded them in the first hour.
The agent pays no creator fee, so it is excluded from the fee base, from 'bots' and from launch-block
snipers (bots.py). Within creator (creators who launched both kinds that day), on the same levers + mayhem."""
import collections, sys
import numpy as np
import bots as B
import levers as L

for path, other in ((sys.argv[1], sys.argv[2]), (sys.argv[2], sys.argv[1])):
    rows = B.rows_of(path, other)
    crs = collections.defaultdict(set)
    for r in rows:
        for w in r["blockset"]: crs[w].add(r["cr"])
    broad = {w for w, c in crs.items() if len(c) >= 50}
    M = [r for r in rows if r["mayhem"]]; S = [r for r in rows if not r["mayhem"]]
    print(f"\n#### {path.split('/')[-1]}: {len(rows):,} launches, Mayhem {len(M):,} ({len(M)/len(rows):.1%})")
    print(f"agent in the launch block: {np.mean([r['agent_block'] for r in M]):.1%} of Mayhem coins; "
          f"agent share of Mayhem coins' volume: {sum(r['vagent'] for r in M) / sum(r['vall'] for r in M):.1%}")
    for lab, g in (("mayhem", M), ("standard", S)):
        f = np.array([r["fee"] for r in g]); n = np.array([r["net"] for r in g])
        print(f"  {lab:8} n {len(g):6,} | P(broad sniper) {np.mean([any(w in broad for w in r['blockset']) for r in g]):.1%} "
              f"| median non-agent fee {np.median(f):.5f} mean {f.mean():.5f} | median NET {np.median(n):+.5f} "
              f"mean (top1% cap) {np.mean(np.clip(n, None, np.percentile(n, 99))):+.5f}")
    ys = {"log1p(non-agent bot SOL)": np.log1p([r["vbot"] for r in rows]),
          "log1p(human SOL)": np.log1p([r["vhum"] for r in rows]),
          "P(broad sniper, not agent)": np.array([float(any(w in broad for w in r["blockset"])) for r in rows]),
          "creator fee SOL (winsor)": None, "dev NET SOL (winsor)": None}
    fee = np.array([r["fee"] for r in rows]); net = np.array([r["net"] for r in rows])
    ys["creator fee SOL (winsor)"] = np.clip(fee, None, np.percentile(fee, 99))
    ys["dev NET SOL (winsor)"] = np.clip(net, *np.percentile(net, [1, 99]))
    names = ["mayhem"] + B.NAMES
    for label, y in ys.items():
        byc = collections.defaultdict(list)
        for r, v in zip(rows, y): byc[r["cr"]].append((v, [r["mayhem"]] + r["x"]))
        Y, X, G = [], [], []
        for cr, lst in byc.items():
            if len(lst) < 2 or len({x[0] for _, x in lst}) < 2: continue   # creators who launched BOTH kinds
            yy = np.array([a for a, _ in lst], float); xx = np.array([b for _, b in lst], float)
            Y.extend(yy - yy.mean()); X.extend(xx - xx.mean(0)); G.extend([cr] * len(lst))
        Y, X, G = np.array(Y), np.array(X), np.array(G)
        keep = X.std(0) > 0
        beta, se = L.cluster_ols(X[:, keep], Y, G)
        kn = [n for n, k in zip(names, keep) if k]
        i = kn.index("mayhem")
        print(f"  within creator [{label}] ({len(set(G)):,} creators, {len(Y):,}): mayhem {beta[i]:+.4f} (t {beta[i]/se[i]:+.1f})"
              + (f" x{np.exp(beta[i]):.2f}" if label.startswith("log1p") else ""))
