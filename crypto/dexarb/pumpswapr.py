"""The LP screen for PumpSwap pools, reduced to one path statistic.

Prereg: preregistrations/pumpswap_lp_seat_20260918.md commits the verdict on

    R = 8 * f * (Q/V) / sigma^2          LP beats rebalancing iff R > 1

For a constant-product pool that ratio collapses to a pure property of the
price path, which removes volume, TVL and any annualisation from the problem.
With x*y=k and p=y/x:

    y = sqrt(k*p)   =>   dy = y * dlog(p) / 2   =>   |dSOL| = V*|dlog p| / 4

so over a window, with r the per-swap log price changes:

    Q/V    = sum|r| / 4
    sigma^2 = sum r^2                       (realised variance, same window)
    R      = 8f * (sum|r|/4) / sum r^2 = 2f * sum|r| / sum r^2

Two things fall out, and both matter:

1. **R is horizon-invariant.** Numerator and denominator both scale linearly
   with elapsed time under iid increments, so R measured over 80 seconds is the
   same quantity as R over a day. No sqrt-T scaling is needed anywhere, which
   sidesteps `tenor-claims-need-venue-calendar` and `gbm-failure-lives-outside-the-tick`
   entirely -- those traps live in the annualisation step this does not have.
2. Since sum|r|/sum r^2 ~ 1/|r|_typical, **R > 1 iff the typical per-swap price
   move is smaller than 2f.** At f=20bps that bar is 40 bps. This is the classic
   maker condition in disguise: you keep the fee iff the price moves less than
   the fee per trade. Same shape as
   `equity-touch-spread-is-one-second-of-adverse-selection`.

So the measurement is one distribution: per-swap |dlog p| in live pools.

    python3 pumpswapr.py --blocks 400
"""
import argparse
import collections
import json
import math
import sys

from pumpprobe import load_env, rpc
import pumpswap as S

# LP share of the swap fee. ASSUMPTION flagged in prereg section 5; the screen
# is reported across a range so a wrong f cannot by itself decide the branch.
F_GRID = [(0.0020, "20bps (assumed PumpSwap LP share)"),
          (0.0025, "25bps (entire swap fee to LP)"),
          (0.0100, "100bps (a 1% exotic tier, for contrast)")]

TOK_OFF, SOL_OFF, KEY_OFF = 40, 48, 112

# CORRECTION (2026-09-18). The names above are wrong for most pools: @40 and @48
# are the BASE and QUOTE reserves positionally, and pumpswapmint.py showed WSOL
# is the base mint in 51 of 93 pools, the quote mint in 34, and absent in 8. So
# "SOL_OFF" is the token leg more often than not.
#
# R itself is unaffected, and not by luck: R depends only on the price RATIO, and
# inverting a price negates log p while leaving |r| and r^2 untouched. That
# invariance is exactly why the bug survived a screen -- the headline statistic
# could not see it (`tapered-tick-means-cents-invert-a-verdict` in reverse: here
# holding the statistic fixed hid a changed input).
#
# The VOLUME WEIGHTS below are not invariant, because Q_sol is measured in the
# leg's own units. The published volume-weighted R = 0.306 is therefore void.
# Recomputed with the resolved leg on the 900-block window: R = 0.270 at f=20bps
# and 0.338 at f=25bps. The verdict is unchanged -- R < 1 either way, so the
# pooled screen still fails -- but the number should not be quoted as 0.306.
SOL_SIDE_MAP = "data/pumpswap_mints.json"


def sol_leg(path=SOL_SIDE_MAP):
    """{pool key b58: 'base'|'quote'} if the mints have been resolved, else {}.

    Read from JSON rather than imported from pumpswapinc, which imports THIS
    module; a cycle here would break every caller."""
    try:
        g = json.load(open(path))["groups"]
    except (IOError, ValueError, KeyError):
        return {}
    out = {}
    for k in g.get("43", []):
        out[k] = "base"
    for k in g.get("75", []):
        out[k] = "quote"
    return out


def series(evs, min_swaps=12):
    """Per-pool reserve series, deduped, with a consistency gate.

    Exact (slot, body) duplicates are dropped: identical reserves AND identical
    amounts in the same slot is a repeated decode, not two real swaps. Then each
    pool's constant product must be non-decreasing on most steps -- fees accrue
    into the reserves, so k that FALLS is a sign the grouping key is not the
    pool. Pools failing the gate are excluded and COUNTED, never dropped
    silently (`an-event-floor-must-say-which-events`)."""
    seen, rows = set(), []
    dup = 0
    for s, bt, d, b in evs:
        if len(b) < KEY_OFF + 32:
            continue
        k = (s, b)
        if k in seen:
            dup += 1
            continue
        seen.add(k)
        import struct
        tok = struct.unpack_from("<Q", b, TOK_OFF)[0]
        sol = struct.unpack_from("<Q", b, SOL_OFF)[0]
        if tok <= 0 or sol <= 0:
            continue
        rows.append((b[KEY_OFF:KEY_OFF + 32], s, bt, tok, sol))
    pools = collections.defaultdict(list)
    for key, s, bt, tok, sol in rows:
        pools[key].append((s, bt, tok, sol))
    good, excl = {}, collections.Counter()
    for key, v in pools.items():
        if len(v) < min_swaps:
            excl["under %d swaps" % min_swaps] += 1
            continue
        v.sort(key=lambda r: r[0])
        ks = [t * so for _s, _b, t, so in v]
        falls = sum(1 for a, c in zip(ks, ks[1:]) if c < a)
        if falls > 0.25 * (len(ks) - 1):
            excl["k falls on >25% of steps"] += 1
            continue
        good[key] = v
    return good, excl, dup, len(rows)


def stats(good, side=None):
    """Per-pool R, plus the raw ingredient: the per-swap |dlog p| distribution.

    `side` maps a pool to which reserve is SOL. Pools with no WSOL leg are
    excluded from the VOLUME WEIGHT (their Q is not denominated in SOL at all)
    but keep their R, which needs no units."""
    if side is None:
        side = sol_leg()
    out, allr = [], []
    import pumptape as _T
    for key, v in good.items():
        rs = []
        for (_s0, _b0, t0, y0), (_s1, _b1, t1, y1) in zip(v, v[1:]):
            p0, p1 = y0 / t0, y1 / t1
            if p0 > 0 and p1 > 0:
                r = math.log(p1 / p0)
                if r != 0.0:
                    rs.append(r)
        if len(rs) < 8:
            continue
        s1 = sum(abs(r) for r in rs)
        s2 = sum(r * r for r in rs)
        if s2 <= 0:
            continue
        allr.extend(abs(r) for r in rs)
        # V and Q measured directly, as an independent check on the identity
        # Q/V = sum|r|/4 that the whole reduction rests on.
        # Pick the leg that is actually SOL. `side` empty means the mints were
        # never resolved, in which case fall back to the old @48 assumption and
        # let the caller know the weights are unresolved.
        sd = side.get(_T.b58encode(key))
        i = 2 if sd == "base" else 3          # tuple is (slot, bt, res@40, res@48)
        V = 2.0 * (sum(r[i] for r in v) / len(v)) / S.LAMPORTS
        Q = sum(abs(b[i] - a[i]) for a, b in zip(v, v[1:])) / S.LAMPORTS
        out.append({"n_swaps": len(v), "sum_abs_r": s1, "sum_r2": s2,
                    "turn_ident": s1 / 4.0, "turn_direct": Q / V if V else 0.0,
                    "V_sol": V, "Q_sol": Q, "sol_side": sd,
                    "med_abs_r": sorted(abs(r) for r in rs)[len(rs) // 2]})
    return out, allr


def report(out, allr, excl, dup, n_ev):
    if not out:
        sys.exit("no pool cleared the gates")
    allr.sort()
    q = lambda a: allr[min(int(a * (len(allr) - 1)), len(allr) - 1)]
    print("\n=== per-swap |dlog p| in %d pools, %d swap-to-swap moves ==="
          % (len(out), len(allr)))
    print("  p10 %.2f%%   p25 %.2f%%   MEDIAN %.2f%%   p75 %.2f%%   p90 %.2f%%"
          % (100 * q(.10), 100 * q(.25), 100 * q(.50), 100 * q(.75),
             100 * q(.90)))
    print("  the bar the LP must beat is 2f = %.2f%% (f=20bps)" % (100 * 0.004))
    print("  share of moves under that bar: %.1f%%"
          % (100 * sum(1 for r in allr if r < 0.004) / len(allr)))

    print("\n=== identity check: Q/V from the path vs Q/V measured directly ===")
    print("    the reduction rests on |dSOL| = V*|dlog p|/4, so these must agree")
    ti = [o["turn_ident"] for o in out]
    td = [o["turn_direct"] for o in out]
    rat = sorted(a / b for a, b in zip(ti, td) if b > 0)
    print("  median identity/direct ratio: %.3f  (p25 %.3f, p75 %.3f)"
          % (rat[len(rat) // 2], rat[len(rat) // 4], rat[3 * len(rat) // 4]))

    print("\n=== R = 2f * sum|r| / sum r^2, volume-weighted ===")
    print("    volume-weighted because a per-pool mean over dead pools is not")
    print("    the R of the capital that would be deployed (`edge-falls-with-volume`)")
    print("  %-40s %9s %9s %9s" % ("", "vol-wtd R", "median R", "best pool"))
    n_unres = sum(1 for o in out if not o.get("sol_side"))
    if n_unres:
        print("    WEIGHTS UNRESOLVED for %d of %d pools (no WSOL leg, or "
              "pumpswapmint.py not run):" % (n_unres, len(out)))
        print("    their Q is not in SOL, so they are dropped from the weight.")
    res = {}
    for f, lab in F_GRID:
        Rs = [(2 * f * o["sum_abs_r"] / o["sum_r2"], o["Q_sol"]) for o in out
              if o.get("sol_side")]
        if not Rs:
            Rs = [(2 * f * o["sum_abs_r"] / o["sum_r2"], o["Q_sol"]) for o in out]
        wsum = sum(w for _r, w in Rs) or 1.0
        vw = sum(r * w for r, w in Rs) / wsum
        med = sorted(r for r, _w in Rs)[len(Rs) // 2]
        best = max(r for r, _w in Rs)
        res["%.4f" % f] = {"vw": vw, "median": med, "best": best}
        print("  %-40s %9.3f %9.3f %9.3f" % (lab, vw, med, best))
    print("\n  excluded pools: %s" % (dict(excl) or "none"))
    print("  deduped %d exact repeat events of %d decoded (%.1f%%)"
          % (dup, n_ev + dup, 100 * dup / max(n_ev + dup, 1)))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=400)
    ap.add_argument("--json", default="data/pumpswap_r.json")
    args = ap.parse_args()
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    if not url:
        sys.exit("SOLANA_RPC_URL not set")
    head, err, _ = rpc(url, "getSlot", [{"commitment": "finalized"}])
    if err:
        sys.exit("getSlot failed: %s" % err)
    lo = head - 400 - args.blocks
    slots = list(range(lo, lo + args.blocks))
    print("head %d; %d slots from %d (~%.0f s of chain)"
          % (head, len(slots), lo, 0.4 * len(slots)))
    evs = S.collect_events(url, slots)
    good, excl, dup, n_ev = series(evs)
    out, allr = stats(good)
    res = report(out, allr, excl, dup, n_ev)
    with open(args.json, "w") as fh:
        json.dump({"n_pools": len(out), "n_moves": len(allr), "R": res,
                   "excluded": dict(excl), "dup": dup,
                   "pools": out[:200]}, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
