"""The decision variable: the creator's own mark-to-market round trip, with NO exit selection.

  python -m research.bundlenet_pnl research/data/bn_launches.jsonl

Two estimator bugs found in the first cut of this arm, both in the memory index already:

- `a-closed-position-subsample-is-selection-on-behaviour`. Conditioning on "the dev fully exited inside
  600 s" keeps 56.8% of launches and selects WINNERS: a dev who is underwater simply holds. Fixed by
  marking every unsold token to the curve, so every launch where the dev bought is scored.
- `a-conditioned-cost-cheaper-than-unconditional-is-a-broken-statistic`. The no-flow control came back
  at **+5.08%** when the a-priori sign is negative (a round trip with no buyers must lose the fee).
  Cause: the control only zeroed BROAD buying after the create block, leaving single/mid buying free to
  move the price. The real control is `n_other600 == 0` -- the dev is the only trader in the window.

Marking is exact rather than at last price: dumping `q` tokens into a constant-product curve returns
`v_sol - K/(v_tok + q)`, so a dev holding a big unsold position is marked at what the curve would
actually pay, not at the quoted price. Fees use this repo's own calibration (`launch_one.py`): pay
1.0125x on the way in, receive 0.9875x on the way out.
"""
from __future__ import annotations

import collections
import json
import sys

import numpy as np

K = 30.0 * 1_073_000_000.0
BUY_F, SELL_F = 1.0125, 0.9875
B = [(0, 0.1), (0.1, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 10), (10, 1e9)]
LBL = ["0-0.1", "0.1-0.5", "0.5-1", "1-2", "2-5", "5-10", ">10"]


def dump_proceeds(q: float, v_sol: float, v_tok: float) -> float:
    """SOL the curve pays for selling q tokens into it, before fee."""
    if q <= 0:
        return 0.0
    return max(0.0, v_sol - K / (v_tok + q))


def pnl_of(r: dict) -> tuple[float, float]:
    """(mark-to-market P&L in SOL, cost basis in SOL) for the creator's position."""
    buys, sells = r["dev_buy_sol600"], r["dev_sell_sol600"]
    held = max(0.0, r["dev_buy_tok600"] - r["dev_sell_tok600"])
    mark = dump_proceeds(held, r["v_sol_last"], r["v_tok_last"])
    cost = BUY_F * buys
    return SELL_F * (sells + mark) - cost, cost


def main() -> int:
    rows = [r for r in (json.loads(l) for l in open(sys.argv[1]))
            if r["n_bad"] == 0 and r["peak_complete"]]
    rows = [r for r in rows if r["dev_buy_tok600"] > 0]
    print(f"{len(rows):,} launches: clean curve, full 600 s window, the creator bought")

    x = np.array([r["dev_sol_slot0"] for r in rows])
    pnl = np.array([pnl_of(r)[0] for r in rows])
    cost = np.array([pnl_of(r)[1] for r in rows])
    other = np.array([r["n_other600"] for r in rows])
    held = np.array([max(0.0, r["dev_buy_tok600"] - r["dev_sell_tok600"]) for r in rows])
    exited = held <= 0.05 * np.array([r["dev_buy_tok600"] for r in rows])

    print("\n=== CONTROL: the dev is the only trader in 600 s (a-priori sign must be NEGATIVE) ===")
    m = other == 0
    print(f"n={m.sum():,}  mean P&L {pnl[m].mean():+.5f}  median {np.median(pnl[m]):+.5f}  "
          f"mean return {100 * (pnl[m] / np.maximum(cost[m], 1e-9)).mean():+.3f}%  "
          f"P(>0) {(pnl[m] > 0).mean():.4f}")
    if pnl[m].mean() > 0:
        print("  *** CONTROL FAILED: estimator still reports a profit with no counterparty ***")
    else:
        print("  control passes: no counterparty, loses the fee, as it must")

    print("\n=== the dev's round trip, marked to the curve, NO exit selection ===")
    print(f"{'dose':>8} {'n':>7} {'mean':>9} {'median':>9} {'P(>0)':>7} {'p10':>9} {'p90':>9} "
          f"{'med ret%':>9} {'%exited':>8}")
    for (lo, hi), lab in zip(B, LBL):
        s = (x > lo) & (x <= hi)
        if s.sum() < 30:
            continue
        ret = 100 * pnl[s] / np.maximum(cost[s], 1e-9)
        print(f"{lab:>8} {s.sum():>7,} {pnl[s].mean():>+9.4f} {np.median(pnl[s]):>+9.4f} "
              f"{(pnl[s] > 0).mean():>7.3f} {np.percentile(pnl[s], 10):>+9.4f} "
              f"{np.percentile(pnl[s], 90):>+9.4f} {np.median(ret):>+9.2f} {100 * exited[s].mean():>7.1f}%")
    print(f"\npooled: n={len(pnl):,} mean {pnl.mean():+.4f} median {np.median(pnl):+.4f} "
          f"P(>0) {(pnl > 0).mean():.3f} total {pnl.sum():+,.1f} SOL")

    print("\n=== same, restricted to full exits (the biased view, for comparison) ===")
    for (lo, hi), lab in zip(B, LBL):
        s = (x > lo) & (x <= hi) & exited
        if s.sum() < 30:
            continue
        print(f"{lab:>8} n={s.sum():>6,} mean {pnl[s].mean():>+8.4f} median {np.median(pnl[s]):>+8.4f} "
              f"P(>0) {(pnl[s] > 0).mean():>5.3f}   vs all: mean "
              f"{pnl[(x > lo) & (x <= hi)].mean():>+8.4f}")

    print("\n=== concentration: who earns it ===")
    byc: dict[str, float] = collections.defaultdict(float)
    cnt: collections.Counter = collections.Counter()
    for r, p in zip(rows, pnl):
        byc[r["creator"]] += p
        cnt[r["creator"]] += 1
    v = np.array(list(byc.values()))
    print(f"{len(v):,} creators: mean {v.mean():+.4f} median {np.median(v):+.4f} "
          f"share positive {100 * (v > 0).mean():.1f}%")
    top = sorted(byc.items(), key=lambda kv: -kv[1])
    pos = v[v > 0].sum()
    for k in (1, 5, 50, 500):
        print(f"  top {k:>4} creators hold {100 * sum(p for _, p in top[:k]) / pos:>5.1f}% of all positive P&L")
    rep = [c for c in byc if cnt[c] >= 10]
    if rep:
        rv = np.array([byc[c] / cnt[c] for c in rep])
        print(f"creators with >=10 launches (n={len(rep)}): mean P&L per launch {rv.mean():+.4f}, "
              f"median {np.median(rv):+.4f}, share positive {100 * (rv > 0).mean():.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
