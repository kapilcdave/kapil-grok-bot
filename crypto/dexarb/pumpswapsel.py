"""Can a pool with R>1 be CHOSEN in advance? The selection test.

`pumpswapr.py` found volume-weighted R = 0.306 but a median of 0.706 and a best
pool of 451, so the branch is not closed on the pooled screen -- per the
committed rule in preregistrations/pumpswap_lp_seat_20260918.md it proceeds.
But "the best pool had R=451" is an in-sample optimum and means nothing on its
own (`oracle-bound-a-selection-closes-a-model-family`).

Three tests, cheapest first:

1. **Oracle-bound the selection.** Pick pools by their SECOND-half R -- perfect
   foresight -- and see what that would pay. Bounds every possible selector.
2. **Persistence.** Split the window; rank pools by first-half R; score them on
   the second half. This is the only thing a real LP could do.
3. **What the high-R pools actually are.** A huge R means tiny per-swap price
   moves, which is also what a dust-traded or pinned pool looks like. A pool you
   cannot deposit size into is not a seat (`quote-availability-is-not-trade-availability`).

    python3 pumpswapsel.py --blocks 1200
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

F = 0.0020


def pool_R(v):
    """R and its ingredients for one pool's reserve series."""
    rs = []
    for (_s0, _b0, t0, y0), (_s1, _b1, t1, y1) in zip(v, v[1:]):
        if t0 > 0 and t1 > 0 and y0 > 0 and y1 > 0:
            r = math.log((y1 / t1) / (y0 / t0))
            if r != 0.0:
                rs.append(r)
    if len(rs) < 8:
        return None
    s1 = sum(abs(x) for x in rs)
    s2 = sum(x * x for x in rs)
    if s2 <= 0:
        return None
    vol = sum(abs(b1 - b0) for (_a, _b, _c, b0), (_d, _e, _f, b1)
              in zip(v, v[1:])) / S.LAMPORTS
    return {"R": 2 * F * s1 / s2, "n": len(rs), "Q": vol,
            "V": 2.0 * (sum(y for _s, _b, _t, y in v) / len(v)) / S.LAMPORTS,
            "med_abs_r": sorted(abs(x) for x in rs)[len(rs) // 2]}


def split_half(good):
    """Same pools, two disjoint time halves, split on slot."""
    a, b = {}, {}
    allslots = sorted({s for v in good.values() for s, _b, _t, _y in v})
    if len(allslots) < 20:
        sys.exit("window too short to split")
    cut = allslots[len(allslots) // 2]
    for k, v in good.items():
        va = [r for r in v if r[0] < cut]
        vb = [r for r in v if r[0] >= cut]
        if len(va) >= 10:
            a[k] = va
        if len(vb) >= 10:
            b[k] = vb
    return a, b, cut


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=900)
    ap.add_argument("--json", default="data/pumpswap_sel.json")
    ap.add_argument("--cache", default="data/pumpswap_events.jsonl")
    ap.add_argument("--offline", action="store_true",
                    help="analyse only what the cache already holds")
    ap.add_argument("--head", type=int, default=0,
                    help="pin the head slot so a resumed run reuses the cache")
    args = ap.parse_args()
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    if not url:
        sys.exit("SOLANA_RPC_URL not set")
    head = args.head
    if not head:
        head, err, _ = rpc(url, "getSlot", [{"commitment": "finalized"}])
        if err:
            sys.exit("getSlot failed: %s" % err)
    lo = head - 400 - args.blocks
    slots = list(range(lo, lo + args.blocks))
    print("head %d; %d slots (~%.0f s of chain, split into two halves)"
          % (head, len(slots), 0.4 * len(slots)))
    print("  (--head %d reuses this exact window from the cache)" % head)
    evs = S.cached_events(url, slots, args.cache, offline=args.offline)
    good, excl, dup, n_ev = R.series(evs, min_swaps=24)
    print("pools clearing the gate: %d  (excluded %s; deduped %d)"
          % (len(good), dict(excl), dup))
    ha, hb, cut = split_half(good)
    ra = {k: pool_R(v) for k, v in ha.items()}
    rb = {k: pool_R(v) for k, v in hb.items()}
    both = [k for k in ra if ra[k] and rb.get(k)]
    print("pools measurable in BOTH halves: %d" % len(both))
    if len(both) < 15:
        sys.exit("too few pools in both halves to test selection")

    vwb = (sum(rb[k]["R"] * rb[k]["Q"] for k in both)
           / (sum(rb[k]["Q"] for k in both) or 1.0))
    print("\n=== baseline: no selection, second half, volume-weighted "
          "R = %.3f ===" % vwb)

    print("\n=== 1. ORACLE-BOUNDED SELECTION: pools picked on their SECOND-half")
    print("    R, i.e. perfect foresight. No real selector can beat this. ===")
    order = sorted(both, key=lambda k: -rb[k]["R"])
    print("  %8s %8s %12s %12s %10s" % ("top", "n pools", "vol-wtd R",
                                        "median R", "TVL SOL"))
    orc = {}
    for frac in (0.05, 0.10, 0.25, 0.50, 1.00):
        sel = order[:max(1, int(frac * len(order)))]
        w = sum(rb[k]["Q"] for k in sel) or 1.0
        vw = sum(rb[k]["R"] * rb[k]["Q"] for k in sel) / w
        med = sorted(rb[k]["R"] for k in sel)[len(sel) // 2]
        tvl = sum(rb[k]["V"] for k in sel) / len(sel)
        orc["%.2f" % frac] = {"vw": vw, "med": med, "n": len(sel)}
        print("  %7.0f%% %8d %12.3f %12.3f %10.1f"
              % (100 * frac, len(sel), vw, med, tvl))

    print("\n=== 2. PERSISTENCE: pools ranked on the FIRST half, scored on the")
    print("    SECOND. This is the only selection an LP could actually make. ===")
    order_a = sorted(both, key=lambda k: -ra[k]["R"])
    print("  %8s %8s %12s %12s %14s" % ("top", "n pools", "vol-wtd R",
                                        "median R", "vs baseline"))
    per = {}
    for frac in (0.05, 0.10, 0.25, 0.50, 1.00):
        sel = order_a[:max(1, int(frac * len(order_a)))]
        w = sum(rb[k]["Q"] for k in sel) or 1.0
        vw = sum(rb[k]["R"] * rb[k]["Q"] for k in sel) / w
        med = sorted(rb[k]["R"] for k in sel)[len(sel) // 2]
        per["%.2f" % frac] = {"vw": vw, "med": med, "n": len(sel)}
        print("  %7.0f%% %8d %12.3f %12.3f %+13.3f"
              % (100 * frac, len(sel), vw, med, vw - vwb))
    # rank correlation, Spearman by hand
    xa = sorted(both, key=lambda k: ra[k]["R"])
    xb = sorted(both, key=lambda k: rb[k]["R"])
    pa = {k: i for i, k in enumerate(xa)}
    pb = {k: i for i, k in enumerate(xb)}
    n = len(both)
    d2 = sum((pa[k] - pb[k]) ** 2 for k in both)
    rho = 1 - 6 * d2 / (n * (n * n - 1))
    print("  Spearman rho(first-half R, second-half R) = %+.3f on %d pools"
          % (rho, n))

    print("\n=== 3. WHAT THE HIGH-R POOLS ARE (top 8 by second-half R) ===")
    print("  %10s %8s %8s %10s %12s" % ("R", "swaps", "TVL SOL", "vol SOL",
                                        "med|dlog p|"))
    for k in order[:8]:
        o = rb[k]
        print("  %10.2f %8d %8.1f %10.2f %11.3f%%"
              % (o["R"], o["n"], o["V"], o["Q"], 100 * o["med_abs_r"]))
    print("\n  A large R with a tiny TVL and tiny volume is not a seat: it is a")
    print("  pool nobody trades, where |dlog p| is small because nothing moves.")

    with open(args.json, "w") as fh:
        json.dump({"n_pools": len(both), "baseline_vw": vwb, "oracle": orc,
                   "persistence": per, "spearman": rho, "cut_slot": cut},
                  fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
