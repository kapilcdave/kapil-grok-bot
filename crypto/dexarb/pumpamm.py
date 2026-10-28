"""Is there an LP seat in the post-graduation pool? The analytic screen first.

`FINDINGS_pumpfun.md` closes the bonding curve and names exactly one adjacent
branch: the pool a graduated token migrates into. That pool is a plain
constant-product AMM, so unlike the curve it DOES have a two-sided liquidity
seat -- you deposit both assets and earn a fee on every swap. That is the same
economic shape as the one family that has ever paid in this lab
(`inplay-wide-maker-income`): fee income against adverse selection.

The reason to write this before collecting anything is that both sides of the
comparison have closed forms, so the required contrast can be priced in advance
(`price-the-required-contrast-before-fitting`).

  fee income rate      = f * Q            f = LP fee share, Q = volume per day
  adverse selection    = (sigma^2 / 8) * V    per day, for a CPMM

The second is LVR ("loss versus rebalancing", Milionis-Moallemi-Roughgarden-
Zhang): the exact rate at which a constant-product LP loses to arbitrageurs
who rebalance it, for a pool of value V holding an asset of volatility sigma.
It is the AMM's version of adverse selection, and like the toll in
`adverse-selection-is-a-level-toll-not-a-slope-killer` it does not care how
clever the LP is -- it is a property of the curve and the price path.

Setting them equal gives the whole branch as ONE dimensionless number:

  R = 8 * f * (Q/V) / sigma^2        LP wins iff R > 1

Q/V is daily turnover, sigma is daily vol. Both are measurable from a swap tape
without any model of my own, and neither is a fitted parameter.

    python3 pumpamm.py                 # the analytic screen
    python3 pumpamm.py --graduation    # + how many curves ever reach a pool
"""
import argparse
import json
import sys

import pumpcurve as C

# PumpSwap charges 25 bps a swap. The LP share is the part that matters here;
# treat it as an ASSUMPTION to verify against a migration transaction before
# any verdict is written, and report the screen across a range either way.
FEE_TOTAL_BPS = 25
FEE_LP_BPS = 20

# pump.fun migrates a curve once it has sold its real token allocation, which
# corresponds to ~85 SOL raised on top of the 30 SOL virtual start. Treated as
# a THRESHOLD TO MEASURE, not a constant to trust: the tape says whether
# trading actually stops there.
GRAD_VSOL = 115.0


def required_turnover(sigma_day, f):
    """Daily volume/TVL a CPMM LP needs just to break even against LVR."""
    return sigma_day ** 2 / (8.0 * f)


def ratio(f, turnover, sigma_day):
    """R = 8f(Q/V)/sigma^2. LP beats the rebalancing benchmark iff R > 1."""
    return 8.0 * f * turnover / (sigma_day ** 2)


def screen():
    print("=== ANALYTIC SCREEN: daily turnover (volume/TVL) a full-range CPMM")
    print("    LP needs just to cover LVR, before any directional exposure. ===")
    print("    required Q/V = sigma^2 / (8f)\n")
    fees = [(FEE_LP_BPS, "PumpSwap LP share (assumed)"),
            (FEE_TOTAL_BPS, "PumpSwap total swap fee"),
            (100, "1% -- a Uniswap-style exotic tier")]
    print("  %-14s %s" % ("sigma/day", "".join("%18s" % ("f=%dbps" % b)
                                               for b, _ in fees)))
    for sd in (0.25, 0.50, 1.00, 2.00, 3.00):
        row = "".join("%15.1fx   " % required_turnover(sd, b / 1e4)
                      for b, _ in fees)
        print("  %-14s %s" % ("%.0f%%" % (100 * sd), row))
    for b, lab in fees:
        print("  f=%dbps: %s" % (b, lab))
    print("\n  Read this as the bar, not the answer. A freshly graduated")
    print("  memecoin can genuinely turn over many times its TVL in a day, so")
    print("  this does NOT close the branch by itself -- it says the branch")
    print("  lives or dies on the JOINT distribution of turnover and vol, and")
    print("  that the pair must be measured on the SAME pool over the SAME")
    print("  window. Measuring a high turnover on hot pools and a vol from")
    print("  elsewhere would be the mix error in")
    print("  `capture-is-an-identity-so-pooled-capture-is-a-spread-mix`.")
    print("\n  Two things this screen deliberately omits, both adverse:")
    print("   - LVR is the loss to a REBALANCING benchmark, so R>1 means the")
    print("     LP beats holding 50/50, not that it makes money. On a token")
    print("     whose price path is the one measured in Wall 1, the benchmark")
    print("     itself loses. R>1 is necessary, not sufficient.")
    print("   - a full-range CPMM LP is always net buying the falling side, so")
    print("     a rug takes the SOL leg too. That is a directional term this")
    print("     ratio does not contain at all.")


def graduation(path):
    """How many curves ever reach a pool, from the tape already collected.

    Free: this is a count over data on disk, and it bounds the whole branch --
    an LP seat in a pool that almost nothing reaches has no capacity, however
    good R is. Same logic as `quote-availability-is-not-trade-availability`."""
    import pumporacle as O
    rows, metas = O.load(path)
    if not rows:
        sys.exit("no tape found at %s" % path)
    states = C.discover_initial_states(rows)
    virgin, _, _ = O.build_mints(rows, metas, states)
    n, grad, peaks, tgrad = 0, 0, [], []
    for v in virgin.values():
        tape = v["tape"]
        pk = max(r["vsol"] for r in tape) / C.LAMPORTS
        peaks.append(pk)
        n += 1
        if pk >= GRAD_VSOL:
            grad += 1
            b = tape[0]
            for r in tape:
                if r["vsol"] / C.LAMPORTS >= GRAD_VSOL:
                    tgrad.append(O.ts_of(r, b))
                    break
    peaks.sort()
    print("\n=== GRADUATION BASE RATE, from the %d curves already on disk ===" % n)
    print("  threshold: peak virtual SOL reserve >= %.0f (30 start + ~85 raised)"
          % GRAD_VSOL)
    print("  reached it: %d of %d = %.2f%%" % (grad, n, 100 * grad / max(n, 1)))
    print("  peak vSOL: p50 %.1f  p90 %.1f  p99 %.1f"
          % (C.median(peaks), peaks[int(0.90 * (len(peaks) - 1))],
             peaks[int(0.99 * (len(peaks) - 1))]))
    if tgrad:
        tgrad.sort()
        print("  time from birth to threshold, seconds: p10 %.0f  p50 %.0f  p90 %.0f"
              % (tgrad[int(0.10 * (len(tgrad) - 1))], C.median(tgrad),
                 tgrad[int(0.90 * (len(tgrad) - 1))]))
    print("  NB the reserve anomaly (trap 1 in FINDINGS_pumpfun.md) inflates a")
    print("  peak taken as a max over states, so this rate is an UPPER bound.")
    return {"n": n, "graduated": grad, "rate": grad / max(n, 1),
            "peak_p50": C.median(peaks),
            "t_to_grad_p50": C.median(sorted(tgrad)) if tgrad else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    ap.add_argument("--graduation", action="store_true")
    ap.add_argument("--json", default="data/pumpamm.json")
    args = ap.parse_args()
    out = {"fee_lp_bps": FEE_LP_BPS, "grad_vsol": GRAD_VSOL,
           "required_turnover": {
               "%.2f" % sd: required_turnover(sd, FEE_LP_BPS / 1e4)
               for sd in (0.25, 0.5, 1.0, 2.0, 3.0)}}
    screen()
    if args.graduation:
        out["graduation"] = graduation(args.tape)
    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
