"""Total LP return in SOL, against three benchmarks. The economics, not the excess.

pumpswapnet.py established something real: at a TVL >= 1000 SOL floor, ranking
pools on the first half's net and holding them through the second pays
+0.206%/day out of sample, against a -33%/day baseline and a placebo median of
-26%, with Spearman +0.970 and 75-95% overlap with perfect foresight.

None of that is income yet, and the reason is the benchmark. `net` is the LVR
excess -- LP versus CONTINUOUSLY REBALANCING a 50/50 book. A full-range LP in a
memecoin pool is long the token, and this corpus already knows the pump.fun
price path is badly negative in the curve phase (`pumpfun-fresh-curve-scalp-dossier`:
P(up) = 0.240 at 10 s, gross -2.89%). Beating a benchmark that is itself falling
is not a business.

For a constant-product pool, per-share LP value in SOL decomposes exactly:

    LP(t1)/LP(t0) = sqrt(p1/p0) * (fee accrual)      fee accrual = exp(f*sum|r|/4)

so against the three benchmarks an LP actually has:

    hold 100% SOL    1
    hold 100% token  p1/p0
    hold 50/50       (1 + p1/p0)/2
    LP               sqrt(p1/p0) * fee accrual

The sqrt() is the whole problem: it is concave, so ANY price move costs the LP
relative to holding, and a falling token costs it in SOL terms outright. The
decomposition is used rather than tracking reserves directly because reserve
deltas also contain LP DEPOSITS and withdrawals, which move k without any trade;
price is deposit-invariant (a proportional deposit leaves p unchanged), so the
price path is the clean channel.

WHAT THIS WINDOW CAN AND CANNOT SETTLE. Over 330 s the fee and LVR terms are
sums of many increments and are estimated well -- that is why R is
horizon-invariant. The DRIFT term is one increment, log(p1/p0), and 330 s of it
is almost pure noise. So this reports the drift with a CI and says plainly
whether it is identified, instead of quoting a total return that is really a
drift guess (`tenor-claims-need-venue-calendar`).

    python3 pumpswaplp.py --head 448205742 --blocks 900
"""
import argparse
import json
import math
import random
import sys

from pumpprobe import load_env
import pumpswap as S
import pumpswapr as R
from pumpswapinc import SLOT_SEC, DAY, sol_side_map
import pumptape as T

TVL_FLOOR = 1000.0
N_BOOT = 2000


def path(v, sol_side):
    """(log price change over the window, sum|r|, sum r^2, mean SOL reserve)."""
    ps, sols = [], []
    for (_s, _bt, a, b) in v:
        if a > 0 and b > 0:
            ps.append(b / a)
            sols.append(a if sol_side == "base" else b)
    if len(ps) < 9:
        return None
    rs = [math.log(p1 / p0) for p0, p1 in zip(ps, ps[1:]) if p0 > 0 and p1 > 0]
    rs = [r for r in rs if r != 0.0]
    if len(rs) < 8:
        return None
    # price of the TOKEN in SOL: if SOL is the base leg the ratio b/a is
    # token-per-SOL, so invert to keep every pool on the same convention
    drift = math.log(ps[-1] / ps[0])
    if sol_side == "base":
        drift = -drift
    return {"drift": drift, "s1": sum(abs(r) for r in rs),
            "s2": sum(r * r for r in rs),
            "V": 2.0 * (sum(sols) / len(sols)) / S.LAMPORTS, "n": len(rs)}


def boot_mean(xs, w=None, n=N_BOOT, seed=1):
    """Bootstrap CI for a (optionally weighted) mean."""
    rnd = random.Random(seed)
    m = len(xs)
    if m < 3:
        return None
    out = []
    idx = range(m)
    for _ in range(n):
        pick = [rnd.randrange(m) for _ in idx]
        if w:
            sw = sum(w[i] for i in pick)
            if sw <= 0:
                continue
            out.append(sum(xs[i] * w[i] for i in pick) / sw)
        else:
            out.append(sum(xs[i] for i in pick) / m)
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=900)
    ap.add_argument("--head", type=int, required=True)
    ap.add_argument("--cache", default="data/pumpswap_events.jsonl")
    ap.add_argument("--f", type=float, default=0.0020)
    ap.add_argument("--json", default="data/pumpswap_lp.json")
    args = ap.parse_args()
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    lo = args.head - 400 - args.blocks
    slots = list(range(lo, lo + args.blocks))
    evs = S.cached_events(url, slots, args.cache, offline=True)
    if not evs:
        sys.exit("cache empty")
    span = (max(s for s, _b, _d, _y in evs)
            - min(s for s, _b, _d, _y in evs)) * SLOT_SEC
    side, _n = sol_side_map()
    good, _e, _d, _nn = R.series(evs, min_swaps=24)
    rows = []
    for k, v in good.items():
        sd = side.get(T.b58encode(k))
        if not sd:
            continue
        p = path(v, sd)
        if p and p["V"] >= TVL_FLOOR:
            rows.append(p)
    if len(rows) < 12:
        sys.exit("only %d pools above the TVL floor" % len(rows))
    f = args.f
    print("window %.0f s; %d pools with TVL >= %.0f SOL; f = %d bps"
          % (span, len(rows), TVL_FLOOR, 1e4 * f))

    scale = DAY / max(span, 1e-9)
    print("\n=== the three terms of an LP's return, per day on capital ===")
    print("    fee and LVR are sums over ~%d moves each, so they are estimated;"
          % (sum(x["n"] for x in rows) // len(rows)))
    print("    drift is ONE increment per pool and is what needs a long window.")
    fee = [f * x["s1"] / 4.0 * scale for x in rows]
    lvr = [x["s2"] / 8.0 * scale for x in rows]
    dft = [x["drift"] * scale for x in rows]
    wv = [x["V"] for x in rows]
    for lab, xs in (("fee income", fee), ("LVR (rebalancing)", lvr),
                    ("price drift", dft)):
        s = sorted(xs)
        ci = boot_mean(xs, wv)
        print("  %-20s cap-wtd %+11.4f%%/day   median %+11.4f%%/day%s"
              % (lab, 100 * sum(x * w for x, w in zip(xs, wv)) / sum(wv),
                 100 * s[len(s) // 2],
                 "   CI [%+.4f%%, %+.4f%%]" % (100 * ci[0], 100 * ci[1])
                 if ci else ""))

    # The comparison that matters, in SOL, over the ACTUAL window (no annualising)
    print("\n=== LP vs the three benchmarks, over the actual %.0f s, in SOL ==="
          % span)
    print("    LP = sqrt(p1/p0) * exp(f*sum|r|/4); the sqrt is the LP's cost of"
          " being long")
    print("  %-22s %14s %14s" % ("", "cap-wtd", "median pool"))
    lp, h50, htok = [], [], []
    for x in rows:
        pr = math.exp(x["drift"])
        acc = math.exp(f * x["s1"] / 4.0)
        lp.append(math.sqrt(pr) * acc - 1.0)
        h50.append((1.0 + pr) / 2.0 - 1.0)
        htok.append(pr - 1.0)
    for lab, xs in (("LP position", lp), ("hold 50/50", h50),
                    ("hold 100% token", htok), ("hold 100% SOL", [0.0] * len(lp))):
        s = sorted(xs)
        print("  %-22s %+13.4f%% %+13.4f%%"
              % (lab, 100 * sum(x * w for x, w in zip(xs, wv)) / sum(wv),
                 100 * s[len(s) // 2]))

    print("\n=== is the drift identified at this horizon? ===")
    ci = boot_mean([x["drift"] for x in rows], wv)
    dm = sum(x["drift"] * w for x, w in zip(rows, wv)) / sum(wv)
    lvr_win = sum((x["s2"] / 8.0) * w for x, w in zip(rows, wv)) / sum(wv)
    fee_win = sum((f * x["s1"] / 4.0) * w for x, w in zip(rows, wv)) / sum(wv)
    print("  drift over the window, cap-wtd: %+.4f%%  CI [%+.4f%%, %+.4f%%]"
          % (100 * dm, 100 * ci[0], 100 * ci[1]))
    print("  the LP's drift COST is sqrt(): about half of it, %+.4f%%"
          % (100 * dm / 2))
    print("  the fee-minus-LVR excess over the same window: %+.4f%%"
          % (100 * (fee_win - lvr_win)))
    ratio = abs(dm / 2) / max(abs(fee_win - lvr_win), 1e-12)
    print("  |drift cost| / |excess| = %.1fx" % ratio)

    # THE CI ABOVE IS NOT A CI FOR THE DRIFT. It resamples POOLS, which is only
    # valid if pools drift independently. These 42 pools all live in the same 330
    # seconds, so a single market-wide move is counted 42 times. The share of
    # pools sharing the drift's sign measures exactly that: under independence it
    # sits near 50%, and far from 50% means one common factor and an effective
    # sample of ONE WINDOW.
    up = sum(1 for x in rows if x["drift"] > 0) / float(len(rows))
    common = abs(up - 0.5) > 0.15
    print("\n  share of pools drifting the SAME way as the aggregate: %.0f%%"
          % (100 * (up if dm > 0 else 1 - up)))
    print("  under pool independence this would sit near 50%")
    if common:
        print("  -> the pools move TOGETHER, so the CI above is resampling one")
        print("     market move 42 times. Effective n for the drift is 1 WINDOW,")
        print("     not %d pools, and the drift is NOT identified." % len(rows))
    straddles = (ci[0] < 0 < ci[1]) or common
    if straddles:
        print("\n  VERDICT: the drift CI straddles zero, so at %.0f s the sign of"
              % span)
        print("  the LP's dominant term is UNKNOWN. The excess being positive and")
        print("  selectable does NOT make the seat profitable, because the term")
        print("  that is %.1fx larger is unmeasured. This branch cannot be closed" % ratio)
        print("  OR opened on this window: it needs a drift measurement over a")
        print("  horizon of hours to days, which is a different collection.")
    else:
        print("\n  VERDICT: the drift is signed at this horizon (%s)."
              % ("positive" if dm > 0 else "NEGATIVE"))
        if dm < 0:
            print("  A negative drift means the LP is long a falling token and the")
            print("  positive excess is irrelevant: CLOSED as ADVERSE.")

    with open(args.json, "w") as fh:
        json.dump({"span_sec": span, "n_pools": len(rows), "f": f,
                   "drift_capwtd_win": dm, "drift_ci_win": list(ci),
                   "fee_win": fee_win, "lvr_win": lvr_win,
                   "drift_cost_over_excess": ratio,
                   "drift_identified": not straddles}, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
