"""AMENDMENT 2 arm: catch RECYCLED bundle wallets, then redo the dose-response on a predetermined exposure.

  python -m research.bundlenet_affinity research/data/bn_launches.jsonl

Two confounds can manufacture the whole positive gradient found in out-bundlenet.txt:

C1 SIMULTANEITY / POPULARITY. `self_sol` is summed over the SAME 10 s as `outside_sol`, and the
   `single`-mint class contains every ordinary one-shot buyer, not just bundle wallets. A popular
   launch draws more of both, so the correlation needs no causal content at all. Fix: exposure =
   `dev_sol_slot0`, the creator's own buy in the CREATE BLOCK, which is predetermined with respect to
   every later buy; dependent = broad-wallet buying in slots AFTER the create block.

C2 RECYCLED BUNDLE WALLETS. A farm that reuses its bundle wallets across launches makes them
   `broad` (>= 3 mints), so they are counted as OUTSIDE money -- and precisely on the big-self-buy
   launches. Fix: a (creator, wallet) pair is an INSIDER PAIR if the wallet early-bought >= 3 of that
   creator's launches and >= 50% of all launches it ever early-bought belong to that creator. Those
   buys are removed from outside flow. This is the tape-only analogue of the shared-funder/sweep test
   that identified the Alien Pepe bundle on chain.
"""
from __future__ import annotations

import collections
import json
import sys

import numpy as np

from .bundlenet_analyse import BUCKETS, LABELS, ci

MIN_PAIR, MIN_SHARE = 3, 0.5


def main() -> int:
    rows = [json.loads(l) for l in open(sys.argv[1])]
    rows = [r for r in rows if r["n_bad"] == 0]
    print(f"{len(rows):,} launches with zero invariant-violating rows")

    pair = collections.Counter()
    tot = collections.Counter()
    for r in rows:
        for u, sol, c, after in r["early"]:
            if c == 3:
                pair[(r["creator"], u)] += 1
                tot[u] += 1
    insider = {(c, u) for (c, u), n in pair.items() if n >= MIN_PAIR and n / tot[u] >= MIN_SHARE}
    print(f"broad (creator,wallet) pairs {len(pair):,}; INSIDER pairs {len(insider):,} "
          f"covering {len({u for _, u in insider}):,} distinct wallets")

    removed = kept = 0.0
    for r in rows:
        o_all = o_clean = o_after = 0.0
        for u, sol, c, after in r["early"]:
            if c != 3:
                continue
            o_all += sol
            if (r["creator"], u) in insider:
                removed += sol
                continue
            o_clean += sol
            kept += sol
            if after:
                o_after += sol
        r["outside_x"] = o_clean
        r["outside_x_after0"] = o_after
    print(f"outside SOL removed as insider-pair money: {removed:,.1f} of {removed + kept:,.1f} "
          f"({100 * removed / max(removed + kept, 1e-9):.1f}%)")

    for expo, dep, label in [
        ("self_sol", "outside_x", "C2 only: self_sol (10 s) vs outside EXCLUDING insider pairs"),
        ("dev_sol_slot0", "outside_x_after0",
         "C1+C2: dev buy in the CREATE BLOCK vs broad buying AFTER it, insider pairs excluded"),
    ]:
        x = np.array([r[expo] for r in rows])
        y = np.array([r[dep] for r in rows])
        print(f"\n=== {label} ===")
        print(f"{'bucket':>8} {'n':>7} {'mean':>9} {'[95% CI]':>20} {'median':>8} {'p90':>9}")
        base = None
        for (lo, hi), lab in zip(BUCKETS, LABELS):
            m = (x == 0) if hi == 0 else (x > lo) & (x <= hi)
            if m.sum() == 0:
                continue
            o = y[m]
            l, h = ci(o)
            if base is None:
                base = o
            print(f"{lab:>8} {m.sum():>7,} {o.mean():>9.4f} {f'[{l:.3f},{h:.3f}]':>20} "
                  f"{np.median(o):>8.4f} {np.percentile(o, 90):>9.3f}")
        print(f"{'bucket':>8} {'diff vs 0':>12} {'MDE(95%)':>10} {'resolved?':>10}")
        for (lo, hi), lab in zip(BUCKETS[1:], LABELS[1:]):
            m = (x > lo) & (x <= hi)
            if m.sum() < 2:
                continue
            o = y[m]
            se = np.sqrt(o.var(ddof=1) / len(o) + base.var(ddof=1) / len(base))
            d = o.mean() - base.mean()
            print(f"{lab:>8} {d:>+12.4f} {1.96 * se:>10.4f} {'YES' if abs(d) > 1.96 * se else 'no':>10}")
        from scipy.stats import spearmanr
        rho, p = spearmanr(x, y)
        print(f"Spearman rho={rho:+.4f} p={p:.2e}")

    # net-of-cost view: what does the creator get back for the create-block buy?
    x = np.array([r["dev_sol_slot0"] for r in rows])
    y = np.array([r["outside_x_after0"] for r in rows])
    print("\n=== marginal outside SOL per extra SOL of create-block dev buy (insider-clean) ===")
    for (lo, hi), lab in zip(BUCKETS[1:], LABELS[1:]):
        m = (x > lo) & (x <= hi)
        if m.sum() < 30:
            continue
        print(f"{lab:>8} n={m.sum():>6,}  dev {x[m].mean():>7.3f} -> outside {y[m].mean():>7.3f}  "
              f"ratio {y[m].mean() / x[m].mean():>6.2f}  median outside {np.median(y[m]):>7.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
