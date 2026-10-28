"""The decisive test: does NET LP income persist out of sample?

Where this branch stands. pumpswapsel.py showed pool R is highly persistent
(Spearman +0.902) and completely useless, because R ranks DEAD pools first: its
top picks earn 0.007 SOL/day. pumpswapinc.py replaced R with net return on
capital and found the population is catastrophic -- capital-weighted
-73.5%/day at f=20bps, LVR eating a full-range memecoin LP alive -- but with a
positive tail: 11 pools of TVL >= 10,000 SOL averaging +0.111%/day.

That tail is the whole question, and as measured it is worthless: those 11 pools
were chosen BY their realised return in the very window the return is reported
from. That is selection on the outcome (`oracle-bound-a-selection-closes-a-model-family`).

So: split the window, rank pools on the FIRST half's net, score them on the
SECOND. Three reference points, because a bare out-of-sample number cannot be
read on its own:

  baseline  no selection at all -- capital-weighted net over every pool
  oracle    ranked on SECOND-half net, i.e. perfect foresight. An upper bound on
            every possible selector, so if the oracle is under the USDC floor
            the branch closes and no cleverer signal can save it.
  placebo   many RANDOM selections of the same size. The real selector has to
            beat this distribution, not merely beat zero -- a point estimate
            proves nothing about a heavy-tailed cross-section
            (`a-placebo-needs-a-distribution-not-a-draw`).

A capacity floor is applied alongside, because a 5-SOL pool paying 4%/day is not
a seat (`quote-availability-is-not-trade-availability`).

    python3 pumpswapnet.py --head 448205742 --blocks 900
"""
import argparse
import json
import random
import sys

from pumpprobe import load_env
import pumpswap as S
import pumpswapr as R
from pumpswapinc import SLOT_SEC, USDC_FLOOR_APR, pool_income, sol_side_map
import pumptape as T

FRACS = (0.10, 0.25, 0.50)
TVL_FLOORS = (0, 1000, 10000)
N_PLACEBO = 400
MIN_HALF_EV = 10


def halves(sol_pools):
    """Split every pool's series at the global median slot."""
    allslots = sorted({s for v, _sd in sol_pools.values() for s, _b, _t, _y in v})
    if len(allslots) < 20:
        sys.exit("window too short to split")
    cut = allslots[len(allslots) // 2]
    a, b = {}, {}
    for k, (v, sd) in sol_pools.items():
        va = [r for r in v if r[0] < cut]
        vb = [r for r in v if r[0] >= cut]
        if len(va) >= MIN_HALF_EV and len(vb) >= MIN_HALF_EV:
            a[k] = (va, sd)
            b[k] = (vb, sd)
    return a, b, cut


def capwtd(rows):
    """Capital-weighted net return per day: the only weighting an LP experiences."""
    w = sum(x["V"] for x in rows)
    if w <= 0:
        return 0.0
    return sum(x["net_day"] * x["V"] for x in rows) / w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=900)
    ap.add_argument("--head", type=int, required=True)
    ap.add_argument("--cache", default="data/pumpswap_events.jsonl")
    ap.add_argument("--f", type=float, default=0.0020)
    ap.add_argument("--json", default="data/pumpswap_net.json")
    ap.add_argument("--seed", type=int, default=20260918)
    args = ap.parse_args()
    random.seed(args.seed)
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    lo = args.head - 400 - args.blocks
    slots = list(range(lo, lo + args.blocks))
    evs = S.cached_events(url, slots, args.cache, offline=True)
    if not evs:
        sys.exit("cache empty")

    side, _n_nowsol = sol_side_map()
    good, excl, dup, _n = R.series(evs, min_swaps=24)
    sol_pools = {}
    for k, v in good.items():
        sd = side.get(T.b58encode(k))
        if sd:
            sol_pools[k] = (v, sd)
    ha, hb, cut = halves(sol_pools)
    span = (max(s for s, _b, _d, _y in evs)
            - min(s for s, _b, _d, _y in evs)) * SLOT_SEC
    half_sec = span / 2.0
    print("window %.0f s of chain; split at slot %d; %d pools in BOTH halves"
          % (span, cut, len(ha)))
    print("each half is ~%.0f s, so a daily rate is a %.0fx extrapolation --"
          % (half_sec, 86400 / max(half_sec, 1)))
    print("read the SIGN and the RANKING, not the annualised magnitude")

    f = args.f
    ia = {k: pool_income(v, f, half_sec, sd) for k, (v, sd) in ha.items()}
    ib = {k: pool_income(v, f, half_sec, sd) for k, (v, sd) in hb.items()}
    both = [k for k in ia if ia[k] and ib.get(k)]
    print("\nf = %d bps; %d pools measurable in both halves" % (1e4 * f, len(both)))
    if len(both) < 15:
        sys.exit("too few pools to test persistence")

    bar = USDC_FLOOR_APR / 365.0
    out = {"f": f, "n": len(both), "cut": cut, "half_sec": half_sec,
           "bar_day": bar, "tiers": {}}

    for floor in TVL_FLOORS:
        # Eligibility on the FIRST half only. Requiring ib[k]["V"] >= floor too
        # would let the universe peek at the scoring half: a pool that drained
        # below the floor during half 2 would be dropped from the sample
        # precisely because of what happened in the half being measured.
        pool = [k for k in both if ia[k]["V"] >= floor]
        if len(pool) < 12:
            print("\n=== TVL floor %d SOL: only %d pools, skipped ==="
                  % (floor, len(pool)))
            continue
        base = capwtd([ib[k] for k in pool])
        print("\n=== TVL floor %d SOL : %d pools ===" % (floor, len(pool)))
        print("  baseline, no selection, second half: %+.4f%%/day"
              % (100 * base))
        print("  the bar to clear is idle USDC: %+.4f%%/day" % (100 * bar))
        print("  %6s %7s %14s %14s %14s %8s %7s"
              % ("top", "n", "REAL oos", "oracle", "placebo p50", "overlap",
                 "beats?"))
        tier = {"n_pools": len(pool), "baseline": base, "fracs": {}}
        for frac in FRACS:
            kk = max(3, int(frac * len(pool)))
            sel_r = sorted(pool, key=lambda z: -ia[z]["net_day"])[:kk]
            sel_o = sorted(pool, key=lambda z: -ib[z]["net_day"])[:kk]
            real = capwtd([ib[k] for k in sel_r])
            orac = capwtd([ib[k] for k in sel_o])
            # REAL landing on ORACLE is either skill or a leak; the overlap of
            # the two selected SETS says which. Identical sets mean the two
            # columns are not independent evidence.
            ovl = len(set(sel_r) & set(sel_o)) / float(kk)
            draws = []
            for _ in range(N_PLACEBO):
                draws.append(capwtd([ib[k] for k in random.sample(pool, kk)]))
            draws.sort()
            p50 = draws[len(draws) // 2]
            p95 = draws[int(0.95 * (len(draws) - 1))]
            # "beats" means: above the USDC floor AND above the 95th percentile
            # of random selections of the same size. Either alone is cheap.
            ok = "YES" if (real > bar and real > p95) else "no"
            print("  %5.0f%% %7d %+13.4f%% %+13.4f%% %+13.4f%% %7.0f%% %7s"
                  % (100 * frac, kk, 100 * real, 100 * orac, 100 * p50,
                     100 * ovl, ok))
            tier["fracs"]["%.2f" % frac] = {
                "k": kk, "real": real, "oracle": orac,
                "placebo_p50": p50, "placebo_p95": p95,
                "overlap": ovl, "beats": ok == "YES"}
        # rank correlation on NET, the analogue of the +0.902 that R showed
        xa = sorted(pool, key=lambda k: ia[k]["net_day"])
        xb = sorted(pool, key=lambda k: ib[k]["net_day"])
        pa = {k: i for i, k in enumerate(xa)}
        pb = {k: i for i, k in enumerate(xb)}
        n = len(pool)
        d2 = sum((pa[k] - pb[k]) ** 2 for k in pool)
        rho = 1 - 6 * d2 / (n * (n * n - 1))
        print("  Spearman rho(first-half net, second-half net) = %+.3f" % rho)
        print("  (R's own rho was +0.902 -- and R was still useless, so a high")
        print("   rho here is necessary and nowhere near sufficient)")
        tier["spearman_net"] = rho
        out["tiers"][str(floor)] = tier

    print("\n=== how to read this ===")
    print("  If the ORACLE column is below the USDC floor, the branch closes:")
    print("  no selector can beat perfect foresight. If the oracle clears it but")
    print("  REAL does not beat the placebo, the income is real and unreachable,")
    print("  which closes it just as firmly for a seat that has to be chosen in")
    print("  advance.")
    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
