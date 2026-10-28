"""R ranks DEAD pools first. Rank on income per unit of capital instead.

pumpswapsel.py settled the selection question and produced a trap instead of a
seat. Rank persistence is real -- Spearman rho = +0.902 between a pool's
first-half and second-half R -- but the pools R ranks highest traded 0.00 to
7.63 SOL in 327 seconds. R = 2f*sum|r|/sum r^2 is a RATIO of fee income to LVR,
and for a pool nobody trades BOTH terms go to zero while their ratio explodes.
So a persistent ranking on R is a persistent ranking of deadness.

The decision variable therefore is not R. Over a window of length `T` seconds,
per unit of TVL:

    fee income / V = f * (Q/V) = f * sum|r| / 4
    LVR      / V = sigma^2 / 8 = sum r^2 / 8
    net return   = f*sum|r|/4 - sum r^2 / 8          (R > 1 iff this is > 0)

`R > 1` is exactly `net > 0`, so R was never wrong -- it was just SIGN-ONLY, and
a sign carries no magnitude. Scaling net by 86400/T gives a daily return on
capital, which is rankable, comparable to the USDC lending floor the corpus uses
as its bar (`crypto-defi-yield-closures`), and cannot be won by a dead pool.

ONE DECODE ASSUMPTION HAS TO BE CHECKED FIRST. R is scale-invariant: it depends
only on the price RATIO y/x, so a wrong decimal on either leg cancels. Income is
NOT -- it needs V and Q in real units, which assumes the quote leg is SOL in
lamports. If some pools are quoted in USDC (6 dp, not 9), their TVL is wrong by
~1000x and they would dominate any income ranking. So the quote mint is read off
the body and verified against the WSOL mint, and non-SOL pools are excluded and
counted rather than silently converted.

    python3 pumpswapinc.py --head 448205742 --blocks 900
"""
import argparse
import collections
import json
import math
import struct
import sys

from pumpprobe import load_env, rpc
import pumpswap as S
import pumpswapr as R
import pumptape as T

WSOL = T.b58decode("So11111111111111111111111111111111111111112")
SLOT_SEC = 0.4
DAY = 86400.0
F_GRID = [0.0020, 0.0025]
USDC_FLOOR_APR = 0.05      # the corpus bar: idle USDC lending, ~5%/yr

# WHICH EVENT FIELD IS THE SOL LEG DEPENDS ON THE POOL, and getting this wrong
# is what this module exists to fix. pumpswapmint.py resolved the account layout
# to base_mint@43 / quote_mint@75, and the event carries the two reserves
# positionally: base@40, quote@48. So a pool whose WSOL sits at @43 has its SOL
# in the BASE reserve @40, and a pool whose WSOL sits at @75 has it at @48.
# Measured medians confirm the flip: group @43 reads 1.7e11 at @40 (171 SOL) and
# 2.0e14 at @48 (tokens); group @75 reads the mirror image.
#
# pumpswapr.py assumed @48 was SOL for EVERY pool, which was wrong for 51 of 93.
# R survived it -- inverting a price negates log p, leaving |r| and r^2
# untouched, so R is invariant to the leg order -- but the VOLUME WEIGHTS did
# not, so the published volume-weighted R = 0.306 is recomputed here.
SOL_AT_BASE = "43"        # WSOL is the base mint  -> SOL reserve is @40
SOL_AT_QUOTE = "75"       # WSOL is the quote mint -> SOL reserve is @48


def sol_side_map(path="data/pumpswap_mints.json"):
    """{pool key b58: 'base'|'quote'} from the resolved mint groups."""
    g = json.load(open(path))["groups"]
    out = {}
    for k in g.get(SOL_AT_BASE, []):
        out[k] = "base"
    for k in g.get(SOL_AT_QUOTE, []):
        out[k] = "quote"
    return out, len(g.get("none", []))


def pool_income(v, f, T_sec, sol_side):
    """Daily net LP return on capital for one pool's reserve series.

    `v` rows are (slot, blockTime, reserve@40, reserve@48); `sol_side` says
    which of the two is SOL."""
    rs, sols = [], []
    for (_s, _bt, r40, r48) in v:
        if r40 <= 0 or r48 <= 0:
            continue
        sols.append(r40 if sol_side == "base" else r48)
    for (_s0, _b0, a0, b0), (_s1, _b1, a1, b1) in zip(v, v[1:]):
        if a0 > 0 and b0 > 0 and a1 > 0 and b1 > 0:
            # sign of r is irrelevant to |r| and r^2, so the leg order does not
            # matter here -- only for the SOL-denominated quantities below
            r = math.log((b1 / a1) / (b0 / a0))
            if r != 0.0:
                rs.append(r)
    if len(rs) < 8 or not sols:
        return None
    s1 = sum(abs(x) for x in rs)
    s2 = sum(x * x for x in rs)
    if s2 <= 0:
        return None
    scale = DAY / max(T_sec, 1e-9)
    fee_ret = f * s1 / 4.0                 # per unit TVL, over the window
    lvr_ret = s2 / 8.0
    V = 2.0 * (sum(sols) / len(sols)) / S.LAMPORTS
    Q = sum(abs(y1 - y0) for y0, y1 in zip(sols, sols[1:])) / S.LAMPORTS
    return {"R": 2 * f * s1 / s2, "n": len(v),
            "fee_day": fee_ret * scale, "lvr_day": lvr_ret * scale,
            "net_day": (fee_ret - lvr_ret) * scale,
            "V": V, "Q_day": Q * scale, "Q_win": Q,
            "fee_sol_day": f * Q * scale, "sol_side": sol_side,
            "med_abs_r": sorted(abs(x) for x in rs)[len(rs) // 2]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=900)
    ap.add_argument("--head", type=int, required=True)
    ap.add_argument("--cache", default="data/pumpswap_events.jsonl")
    ap.add_argument("--json", default="data/pumpswap_income.json")
    args = ap.parse_args()
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    lo = args.head - 400 - args.blocks
    slots = list(range(lo, lo + args.blocks))
    evs = S.cached_events(url, slots, args.cache, offline=True)
    if not evs:
        sys.exit("cache empty for this window")
    span = (max(s for s, _b, _d, _y in evs)
            - min(s for s, _b, _d, _y in evs)) * SLOT_SEC
    print("window actually covered: %.0f s of chain" % span)

    side, n_nowsol = sol_side_map()
    good, excl, dup, n_ev = R.series(evs, min_swaps=24)
    sol_pools, nonsol = {}, 0
    for k, v in good.items():
        s = side.get(T.b58encode(k))
        if s:
            sol_pools[k] = (v, s)
        else:
            nonsol += 1
    print("\npools clearing the swap gate: %d  (excluded %s; deduped %d)"
          % (len(good), dict(excl), dup))
    print("  with a WSOL leg: %d;  no WSOL leg, EXCLUDED: %d"
          % (len(sol_pools), nonsol))
    nb = sum(1 for _v, s in sol_pools.values() if s == "base")
    print("  SOL in the BASE reserve @40: %d;  in the QUOTE reserve @48: %d"
          % (nb, len(sol_pools) - nb))
    if not sol_pools:
        sys.exit("no pool with a WSOL leg cleared the gate")

    out = {}
    for f in F_GRID:
        rows = [x for x in (pool_income(v, f, span, s)
                            for v, s in sol_pools.values()) if x]
        if not rows:
            continue
        # The two groups are the two readings of the account layout. If they
        # agree, the base/quote naming never had to be resolved.
        print("\n=== f = %d bps : the two leg-order groups, side by side ==="
              % (1e4 * f))
        print("  %12s %7s %12s %14s %14s"
              % ("SOL leg", "pools", "med TVL SOL", "vol-wtd R", "cap-wtd net/d"))
        for lab in ("base", "quote"):
            grp = [x for x in rows if x["sol_side"] == lab]
            if not grp:
                continue
            wq = sum(x["Q_win"] for x in grp) or 1.0
            wv = sum(x["V"] for x in grp) or 1.0
            tv = sorted(x["V"] for x in grp)
            print("  %12s %7d %12.1f %14.3f %+13.4f%%"
                  % (lab, len(grp), tv[len(tv) // 2],
                     sum(x["R"] * x["Q_win"] for x in grp) / wq,
                     100 * sum(x["net_day"] * x["V"] for x in grp) / wv))
        print("\n=== f = %d bps : NET LP return per day, on capital ===" % (1e4 * f))
        print("    (the LVR benchmark is holding 50/50, so 'net' is the EXCESS")
        print("     over rebalancing, not a total return)")
        tot_v = sum(x["V"] for x in rows) or 1.0
        vw_net = sum(x["net_day"] * x["V"] for x in rows) / tot_v
        nets = sorted(x["net_day"] for x in rows)
        print("  capital-weighted net: %+.4f%%/day   median pool: %+.4f%%/day"
              % (100 * vw_net, 100 * nets[len(nets) // 2]))
        print("  pools with net > 0: %d of %d (%.0f%%)"
              % (sum(1 for x in rows if x["net_day"] > 0), len(rows),
                 100 * sum(1 for x in rows if x["net_day"] > 0) / len(rows)))

        print("\n  --- ranked by R (what pumpswapsel.py selected) ---")
        print("  %9s %10s %10s %10s %12s" % ("R", "TVL SOL", "net %/day",
                                             "fee SOL/d", "vol SOL/d"))
        for x in sorted(rows, key=lambda z: -z["R"])[:6]:
            print("  %9.1f %10.1f %+9.4f%% %10.3f %12.1f"
                  % (x["R"], x["V"], 100 * x["net_day"], x["fee_sol_day"],
                     x["Q_day"]))

        print("\n  --- ranked by NET INCOME IN SOL/day (what an LP is paid) ---")
        print("  %9s %10s %10s %10s %12s" % ("R", "TVL SOL", "net %/day",
                                             "net SOL/d", "vol SOL/d"))
        for x in sorted(rows, key=lambda z: -(z["net_day"] * z["V"]))[:6]:
            print("  %9.1f %10.1f %+9.4f%% %+10.3f %12.1f"
                  % (x["R"], x["V"], 100 * x["net_day"],
                     x["net_day"] * x["V"], x["Q_day"]))

        # The bar: a seat has to beat idle USDC, on capital you can actually
        # deposit. A pool with 5 SOL of TVL paying 40%/day is not a business.
        bar = USDC_FLOOR_APR / 365.0
        print("\n  --- pools beating the %.3f%%/day USDC floor, by min TVL ---"
              % (100 * bar))
        print("  %10s %8s %14s %16s" % ("min TVL", "n pools", "capital-wtd net",
                                        "total net SOL/d"))
        tiers = {}
        for mv in (0, 10, 100, 1000, 10000):
            sel = [x for x in rows if x["V"] >= mv and x["net_day"] > bar]
            if not sel:
                print("  %10d %8d %14s %16s" % (mv, 0, "-", "-"))
                tiers[str(mv)] = {"n": 0}
                continue
            w = sum(x["V"] for x in sel)
            vw = sum(x["net_day"] * x["V"] for x in sel) / w
            tot = sum(x["net_day"] * x["V"] for x in sel)
            tiers[str(mv)] = {"n": len(sel), "vw_net_day": vw,
                              "net_sol_day": tot, "tvl": w}
            print("  %10d %8d %+13.4f%% %+16.3f" % (mv, len(sel), 100 * vw, tot))
        out["%.4f" % f] = {"vw_net_day": vw_net, "n_pools": len(rows),
                           "median_net_day": nets[len(nets) // 2],
                           "tiers": tiers}

    print("\n  Read the two rankings against each other: if the R ranking pays")
    print("  ~0 SOL/day while the income ranking is a different set of pools,")
    print("  then R is a dead-pool detector and selection on it is worthless.")
    with open(args.json, "w") as fh:
        json.dump({"span_sec": span, "n_sol_pools": len(sol_pools),
                   "n_nonsol": nonsol, "n_no_wsol_leg": n_nowsol,
                   "f": out}, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
