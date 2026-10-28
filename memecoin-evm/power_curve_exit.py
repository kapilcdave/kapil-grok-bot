"""How many launches does the BSC Pancake curve-exit test actually need?

Uses the SPENT n=42 evaluation (data/curve_exit_scored.json, already reported in
RESULT_bsc_pancake_curve_exit_20261012.md) to size the one further evaluation the
preregistration amendment allows at n >= 500. This re-uses a published result to plan
sample size; it does NOT score the accumulating tape, which stays sealed until the trigger.

Why not the textbook n = (1.96*sd/half_width)^2: the return distribution is 86% small wins
against a -100% tail, so the mean's sampling distribution is skewed at the n we care about
and a percentile-bootstrap CI does not have the width the normal formula assumes. Power is
therefore simulated the way the test will actually be run -- draw n launches, take a
percentile bootstrap CI of the mean, ask whether it clears zero.

The point estimate is itself the noisy quantity, so power is reported against a RANGE of
assumed true means rather than against +4.84%. Sizing to the observed estimate is how an
underpowered test gets re-run at a sample that only passes if the estimate was lucky.

    python3 power_curve_exit.py
"""
import json

import numpy as np

CELL = "ret_0.01_30"          # best cell at n=42; the one the amendment will re-evaluate
ASSUMED = (0.02, 0.03, 0.0484)
NS = (200, 350, 500, 750, 1000, 1500, 2000)
B_INNER = 1000                # bootstrap draws per simulated study
SIMS = 600                    # simulated studies per (n, assumed mean)
TARGET = 0.80
RNG = np.random.default_rng(11)


def boot_ci_above_zero(sample, b=B_INNER):
    """Percentile bootstrap 95% CI of the mean, strictly above zero? (the frozen rule)"""
    idx = RNG.integers(0, len(sample), size=(b, len(sample)))
    means = sample[idx].mean(axis=1)
    return np.percentile(means, 2.5) > 0


def power(base, n, assumed, sims=SIMS):
    shifted = base - base.mean() + assumed      # same shape and spread, stated mean
    hits = 0
    for _ in range(sims):
        s = shifted[RNG.integers(0, len(shifted), size=n)]
        hits += boot_ci_above_zero(s)
    return hits / sims


def main():
    rows = json.load(open("data/curve_exit_scored.json"))
    r = np.array([x[CELL] for x in rows], dtype=float)
    n0 = len(r)
    sd = r.std(ddof=1)

    print(f"spent evaluation: n={n0}, cell {CELL}")
    print(f"  mean {r.mean()*100:+.2f}%  median {np.median(r)*100:+.2f}%  sd {sd*100:.2f}%")
    print(f"  win rate {(r>0).mean()*100:.0f}%   min {r.min()*100:+.1f}%   "
          f"max {r.max()*100:+.1f}%")
    print(f"  returns below -50%: {(r<-0.5).sum()}/{n0}  "
          f"(tail frequency {(r<-0.5).mean()*100:.1f}%)")
    print(f"  normal-approx n for a +-1.7% half-width: "
          f"{(1.96*sd/0.017)**2:.0f}")
    print(f"  normal-approx n to separate a +3.0% mean from zero at 95%: "
          f"{(1.96*sd/0.03)**2:.0f}\n")

    print("simulated power of the frozen rule (bootstrap 95% CI strictly > 0),")
    print("per assumed TRUE mean, holding the observed shape and spread:\n")
    hdr = "".join(f"{int(a*10000)/100:>9.2f}%" for a in ASSUMED)
    print(f"{'n':>7}{hdr}")
    table = {}
    for n in NS:
        cells = [power(r, n, a) for a in ASSUMED]
        table[n] = cells
        print(f"{n:>7}" + "".join(f"{c*100:>9.0f}%" for c in cells))

    print(f"\nsmallest n reaching {TARGET:.0%} power:")
    for j, a in enumerate(ASSUMED):
        hit = next((n for n in NS if table[n][j] >= TARGET), None)
        print(f"  true mean {a*100:+.2f}%  ->  "
              + (f"n ~ {hit}" if hit else f"not reached by n={NS[-1]}"))

    # The replication arm is the binding constraint: it needs the FIRST HALF alone to clear
    # zero, so the effective sample for condition (2) is n/2.
    print("\nthe replication arm binds: PASS needs the first chronological half's CI > 0,")
    print("so the operative sample size is n/2, not n:")
    print(f"{'total n':>9}{'first half':>12}" + "".join(f"{int(a*10000)/100:>9.2f}%" for a in ASSUMED))
    for n in NS:
        half = n // 2
        cells = [power(r, half, a) for a in ASSUMED]
        print(f"{n:>9}{half:>12}" + "".join(f"{c*100:>9.0f}%" for c in cells))


if __name__ == "__main__":
    main()
