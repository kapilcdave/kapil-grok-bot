"""Preregistered analysis of research/data/bn_launches.jsonl.

  python -m research.bundlenet_analyse research/data/bn_launches.jsonl

Design frozen in PREREG-bundle-netted-dose-response-20261017.md (+ AMENDMENT 1). Buckets, the primary
dependent (`outside_sol`), the asymmetric inference rule and the MDE requirement were all declared
before this file was written.
"""
from __future__ import annotations

import json
import sys

import numpy as np

BUCKETS = [(0, 0), (0, 0.1), (0.1, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 10), (10, 1e9)]
LABELS = ["self=0", "0-0.1", "0.1-0.5", "0.5-1", "1-2", "2-5", "5-10", ">10"]
RNG = np.random.default_rng(20261017)


def ci(x: np.ndarray, n: int = 2000) -> tuple[float, float]:
    if len(x) < 2:
        return (float("nan"), float("nan"))
    idx = RNG.integers(0, len(x), size=(n, len(x)))
    m = x[idx].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def table(rows: list[dict], label: str) -> None:
    self_sol = np.array([r["self_sol"] for r in rows])
    out = np.array([r["outside_sol"] for r in rows])
    naive = np.array([r["naive_sol"] for r in rows])
    mid = np.array([r["mid_sol"] for r in rows])
    netc = np.array([r["net_curve"] for r in rows])
    swarm = np.array([1.0 if r["swarm"] else 0.0 for r in rows])
    nb = np.array([r["naive_buyers"] for r in rows])
    cb = np.array([r["clean_buyers"] for r in rows])

    print(f"\n=== {label}: n={len(rows):,} ===")
    print(f"{'bucket':>8} {'n':>7} {'outside_sol mean':>17} {'[95% CI]':>22} {'median':>7} "
          f"{'P(swarm)':>9} {'net_curve':>10} {'naive_sol':>10} {'mid_sol':>8} {'buyers n/c':>12}")
    base = None
    for (lo, hi), lab in zip(BUCKETS, LABELS):
        m = (self_sol == 0) if hi == 0 else (self_sol > lo) & (self_sol <= hi)
        if m.sum() == 0:
            continue
        o = out[m]
        lo_ci, hi_ci = ci(o)
        if base is None:
            base = o
        print(f"{lab:>8} {m.sum():>7,} {o.mean():>17.4f} {f'[{lo_ci:.4f},{hi_ci:.4f}]':>22} "
              f"{np.median(o):>7.4f} {swarm[m].mean():>9.3f} {netc[m].mean():>10.4f} "
              f"{naive[m].mean():>10.4f} {mid[m].mean():>8.4f} {nb[m].mean():>5.2f}/{cb[m].mean():<6.2f}")

    # declared MDE: smallest resolvable difference vs the self=0 bucket, at 95%
    print(f"\n{'bucket':>8} {'diff vs self=0':>15} {'MDE(95%)':>10} {'resolved?':>10}")
    for (lo, hi), lab in zip(BUCKETS[1:], LABELS[1:]):
        m = (self_sol > lo) & (self_sol <= hi)
        if m.sum() < 2:
            continue
        o = out[m]
        se = np.sqrt(o.var(ddof=1) / len(o) + base.var(ddof=1) / len(base))
        d = o.mean() - base.mean()
        print(f"{lab:>8} {d:>+15.4f} {1.96 * se:>10.4f} {'YES' if abs(d) > 1.96 * se else 'no':>10}")

    pos = self_sol > 0
    if pos.sum() > 10:
        from scipy.stats import spearmanr
        rho, p = spearmanr(self_sol[pos], out[pos])
        print(f"\nSpearman(self_sol, outside_sol | self_sol>0): rho={rho:+.4f} p={p:.2e} n={pos.sum():,}")
        rho2, p2 = spearmanr(self_sol, out)
        print(f"Spearman over all eligible launches:            rho={rho2:+.4f} p={p2:.2e} n={len(rows):,}")

    # how much of the naive statistic is the creator's own money
    tot_naive, tot_out, tot_self, tot_mid = naive.sum(), out.sum(), self_sol.sum(), mid.sum()
    print(f"\ncontamination, first 10 s, corpus totals:")
    print(f"  self (creator + single-mint wallets) {tot_self:12.1f} SOL")
    print(f"  outside (broad wallets)              {tot_out:12.1f} SOL")
    print(f"  mid (2-mint wallets, unclassified)   {tot_mid:12.1f} SOL")
    print(f"  naive 'all non-creator buyers'       {tot_naive:12.1f} SOL  "
          f"-> {100 * (tot_naive - tot_out) / tot_naive:.1f}% of it is NOT broad-wallet money")
    big = self_sol >= 2
    if big.sum():
        print(f"  on launches with self_sol >= 2 SOL (n={big.sum():,}): naive buyers {nb[big].mean():.2f}, "
              f"of which broad {cb[big].mean():.2f} ({100 * cb[big].mean() / max(nb[big].mean(), 1e-9):.0f}%)")


def dump_beats_hold(rows: list[dict]) -> None:
    sel = [r for r in rows if r["dev_sell_tok"] > 0 and r["peak_complete"] and r["px_peak"] > 0]
    if not sel:
        print("\nno launches with a dev sell inside 60 s")
        return
    ratio = np.array([(r["dev_sell_sol"] / r["dev_sell_tok"]) / r["px_peak"] for r in sel])
    print(f"\n=== secondary: did the operator's exit beat holding? n={len(sel):,} ===")
    print(f"realised avg sell price / peak price in 600 s: median {np.median(ratio):.3f}  "
          f"mean {ratio.mean():.3f}  p25 {np.percentile(ratio, 25):.3f}  p75 {np.percentile(ratio, 75):.3f}")
    print(f"share exiting above 90% of the peak: {100 * (ratio > 0.9).mean():.1f}%   "
          f"below 50%: {100 * (ratio < 0.5).mean():.1f}%")


def main() -> int:
    rows = [json.loads(l) for l in open(sys.argv[1])]
    clean = [r for r in rows if r["n_bad"] == 0]
    print(f"loaded {len(rows):,} eligible launches; {len(clean):,} with zero invariant-violating rows "
          f"({100 * len(clean) / len(rows):.1f}%)")
    traded = [r for r in rows if r["n_trades"] > 0]
    print(f"{len(traded):,} had any trade in 60 s ({100 * len(traded) / len(rows):.1f}%)")
    table(clean, "A: launches with n_bad == 0 (headline)")
    table(rows, "B: all eligible launches, bad rows dropped (agreement check)")
    dump_beats_hold(clean)
    return 0


if __name__ == "__main__":
    sys.exit(main())
