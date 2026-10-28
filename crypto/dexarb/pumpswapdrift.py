"""The drift term, measured over holding periods instead of inside one window.

pumpswaplp.py left this branch on exactly one unknown. An LP's return splits into

    LP = sqrt(p1/p0) * exp(f*sum|r|/4)

and the fee-minus-LVR excess is measurable in 330 seconds (it is a sum over
hundreds of increments) while the drift is not (it is ONE increment). Measured
over a single 330 s window the drift came out +0.56% with an apparently tight
bootstrap CI -- and that CI was meaningless, because 90% of the 42 pools drifted
the same way. One market-wide move was being resampled 42 times. The effective
sample for a drift is the number of independent TIME PERIODS, not pools.

So: snapshot the same pools at N times spread over many hours, take the drift of
each pool across each consecutive pair, and form the confidence interval OVER
PERIODS. The endpoint serves blocks at least 55 h back, so this is a historical
measurement and needs no waiting.

Two things come out that the single window could not give:

1. The drift over a realistic holding period, with an honest CI.
2. SURVIVAL. A seat has to exist for as long as the capital is deposited, and a
   graduated memecoin pool can die between snapshots. A pool that vanishes is not
   a pool with zero drift, so disappearance is counted separately and never
   averaged in as a zero (`an-event-floor-must-say-which-events`).

    python3 pumpswapdrift.py --snaps 16 --hours 36 --win 30
"""
import argparse
import collections
import json
import math
import random
import sys

from pumpprobe import load_env, rpc
import pumpswap as S
import pumpswapr as R
from pumpswapinc import SLOT_SEC, DAY, sol_side_map
import pumptape as T

TVL_FLOOR = 1000.0
MIN_EV = 4              # events needed to price a pool inside one snapshot
WSOL = T.b58decode("So11111111111111111111111111111111111111112")
SIDE_CACHE = "data/pumpswap_side_cache.json"


def resolve_sides(url, keys_b58, cache_path=SIDE_CACHE, batch=100):
    """{pool b58: 'base'|'quote'|None} for ARBITRARY pools, cached on disk.

    This exists because of a look-ahead that produced a fake +114%/day. The
    first version took its pool universe from sol_side_map(), which only holds
    the pools discovered in a window at the END of the sample. Every historical
    period was therefore restricted to pools still trading actively 36 h later --
    i.e. to the pools that SURVIVED -- and a survivor's price path is positive by
    construction. The universe has to be resolvable at each period's own start,
    so mints are fetched for whatever keys a snapshot contains.

    Same shape as `a-closed-position-subsample-is-selection-on-behaviour`: the
    sample was conditioned on an outcome that had not happened yet."""
    import os
    known = {}
    if os.path.exists(cache_path):
        try:
            known = json.load(open(cache_path))
        except ValueError:
            known = {}
    todo = [k for k in keys_b58 if k not in known]
    if todo:
        print("  resolving mints for %d new pools (%d cached)"
              % (len(todo), len(known)), flush=True)
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            r, err, _ = rpc(url, "getMultipleAccounts",
                            [chunk, {"encoding": "base64"}], timeout=60)
            if err or not r:
                continue
            import base64
            for k, acc in zip(chunk, r.get("value") or []):
                sd = None
                if acc:
                    d = (acc.get("data") or [None])[0]
                    if d:
                        try:
                            raw = base64.b64decode(d)
                            if raw[43:75] == WSOL:
                                sd = "base"
                            elif raw[75:107] == WSOL:
                                sd = "quote"
                        except Exception:                      # noqa: BLE001
                            sd = None
                known[k] = sd
        with open(cache_path, "w") as fh:
            json.dump(known, fh)
    return known


def snapshot(url, centre, win, cache, min_ev=MIN_EV):
    """Median price and SOL reserve per pool, in a short window of blocks."""
    slots = list(range(centre, centre + win))
    evs = S.cached_events(url, slots, cache, quiet=True)
    by = collections.defaultdict(list)
    for (s, _bt, _d, b) in evs:
        if len(b) < R.KEY_OFF + 32:
            continue
        import struct
        a = struct.unpack_from("<Q", b, R.TOK_OFF)[0]
        q = struct.unpack_from("<Q", b, R.SOL_OFF)[0]
        if a > 0 and q > 0:
            by[b[R.KEY_OFF:R.KEY_OFF + 32]].append((a, q))
    out = {}
    for k, v in by.items():
        if len(v) < min_ev:
            continue
        ps = sorted(q / a for a, q in v)
        out[k] = {"p": ps[len(ps) // 2], "n": len(v),
                  "a": sorted(a for a, _q in v)[len(v) // 2],
                  "q": sorted(q for _a, q in v)[len(v) // 2]}
    return out


def periods_for(snaps, side, gap_h, verbose=True):
    """Per-period capital-weighted drift, both arms of the survivorship bracket."""
    if verbose:
        print("  %5s %8s %8s %9s %12s %12s %10s"
              % ("per", "gap h", "pools", "survive", "survivors", "bracketed",
                 "med pool"))
    per = []
    for i in range(len(snaps) - 1):
        a, b = snaps[i], snaps[i + 1]
        rows = []
        for k, va in a.items():
            sd = side.get(T.b58encode(k))
            if not sd:
                continue
            tvl = 2.0 * (va["a"] if sd == "base" else va["q"]) / S.LAMPORTS
            if tvl < TVL_FLOOR:
                continue
            vb = b.get(k)
            if not vb:
                rows.append((None, tvl))       # did not survive: counted, not 0
                continue
            d = math.log(vb["p"] / va["p"])
            if sd == "base":
                d = -d
            rows.append((d, tvl))
        alive = [(d, w) for d, w in rows if d is not None]
        if len(rows) < 5 or not alive:
            continue
        surv = len(alive) / float(len(rows))
        w = sum(x for _d, x in alive) or 1.0
        cw = sum(d * x for d, x in alive) / w
        med = sorted(d for d, _x in alive)[len(alive) // 2]
        # A pool that stopped trading is NOT a pool with zero drift, and dropping
        # it keeps only survivors. Bracket it instead: the optimistic arm is the
        # survivors-only mean above, the pessimistic arm marks each vanished pool
        # at the worst drift actually observed among survivors this period. The
        # truth is inside the bracket (`a-closed-position-subsample-is-selection-on-behaviour`).
        worst = min(d for d, _x in alive)
        rows_b = [(d if d is not None else worst, x) for d, x in rows]
        wb = sum(x for _d, x in rows_b) or 1.0
        cw_b = sum(d * x for d, x in rows_b) / wb
        per.append({"gap_h": gap_h, "n": len(rows), "surv": surv,
                    "cw": cw, "cw_bracket": cw_b, "med": med})
        if verbose:
            print("  %5d %8.2f %8d %8.0f%% %+11.4f%% %+11.4f%% %+9.4f%%"
                  % (i, gap_h, len(rows), 100 * surv, 100 * cw, 100 * cw_b,
                     100 * med))
    return per


def gate_sweep(url, centres, args, side, gap_h, gates=(1, 2, 4, 8, 16)):
    """THE DECISIVE TEST: is the drift a market fact or an artefact of the filter?

    A pool can only be priced from trades, so requiring `min_ev` trades inside a
    12 s window conditions the sample on the pool being ACTIVE at that instant --
    and for a memecoin, activity is not independent of the price move. If that is
    what drives the drift, then tightening the gate must RAISE the measured drift
    while lowering survival, and the number is a property of the observation
    filter rather than of the market. This is the same failure as
    `quote-availability-is-not-trade-availability` and
    `close-ts-is-outcome-dependent-in-inplay-binaries`."""
    print("\n=== is the drift a market fact, or a function of the ACTIVITY GATE? ===")
    print("  a pool is priced only if it TRADED, so the gate selects on activity")
    print("  %8s %11s %9s %14s %13s"
          % ("trades", "survivors", "survival", "bracketed/day", "periods +"))
    rows = []
    for g in gates:
        snaps = [snapshot(url, c, args.win, args.cache, g) for c in centres]
        per = periods_for(snaps, side, gap_h, verbose=False)
        if len(per) < 4:
            continue
        m = sum(p["cw"] for p in per) / len(per)
        mb = sum(p["cw_bracket"] for p in per) / len(per)
        sv = sum(p["surv"] for p in per) / len(per)
        np_ = sum(1 for p in per if p["cw"] > 0)
        rows.append({"min_ev": g, "drift": m, "bracket": mb, "surv": sv})
        print("  %8d %+10.4f%% %8.0f%% %+13.4f%% %8d/%d"
              % (g, 100 * m, 100 * sv, 100 * mb * (24.0 / gap_h), np_, len(per)))
    if len(rows) < 3:
        return rows
    mono = all(rows[i + 1]["drift"] > rows[i]["drift"] for i in range(len(rows) - 1))
    span = (min(r["bracket"] for r in rows) * (24.0 / gap_h),
            max(r["bracket"] for r in rows) * (24.0 / gap_h))
    print("\n  drift rises monotonically with the gate: %s" % ("YES" if mono else "no"))
    print("  survival falls from %.0f%% to %.0f%% across the same sweep"
          % (100 * rows[0]["surv"], 100 * rows[-1]["surv"]))
    print("  the bracketed drift spans [%+.1f%%, %+.1f%%]/day over the sweep alone"
          % (100 * span[0], 100 * span[1]))
    if mono:
        print("\n  -> THE DRIFT IS NOT MEASURABLE FROM THIS DATA. Every value of the")
        print("     LP's dominant term is reachable by choosing the activity gate,")
        print("     and the gate is an arbitrary analyst choice, not a market")
        print("     parameter. The positive %d-of-%d record is manufactured by"
              % (sum(1 for p in rows if p['drift'] > 0), len(rows)))
        print("     conditioning on the pool still being traded.")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snaps", type=int, default=16)
    ap.add_argument("--hours", type=float, default=36.0)
    ap.add_argument("--win", type=int, default=30)
    ap.add_argument("--cache", default="data/pumpswap_events.jsonl")
    ap.add_argument("--json", default="data/pumpswap_drift.json")
    ap.add_argument("--f", type=float, default=0.0020)
    # Without this the window is defined by a live getSlot, so every re-run
    # asks for DIFFERENT blocks and misses the cache entirely -- and a verdict
    # that cannot be recomputed on the same blocks is not a verdict.
    ap.add_argument("--head", type=int, default=0)
    # The activity gate is the suspect, so it has to be a knob. If the survivors
    # arm's drift RISES with min_ev while survival falls, the positive drift is
    # manufactured by conditioning on being traded, not observed in the market.
    ap.add_argument("--minev", type=int, default=MIN_EV)
    ap.add_argument("--sweep", action="store_true",
                    help="sweep the activity gate; this is the decisive test")
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
    total = int(args.hours * 3600 / SLOT_SEC)
    step = total // max(args.snaps - 1, 1)
    centres = [head - 600 - total + i * step for i in range(args.snaps)]
    gap_h = step * SLOT_SEC / 3600.0
    print("%d snapshots of %d blocks, %.2f h apart, spanning %.1f h back from %d"
          % (args.snaps, args.win, gap_h, args.hours, head))
    print("fetching %d blocks total\n" % (args.snaps * args.win))

    snaps = []
    for i, c in enumerate(centres):
        sn = snapshot(url, c, args.win, args.cache, args.minev)
        snaps.append(sn)
        print("  snap %2d  slot %d  %4d pools priced" % (i, c, len(sn)),
              flush=True)
    # Universe resolved from the snapshots THEMSELVES, not from a window at the
    # end of the sample. See resolve_sides() for the bias this replaces.
    allkeys = sorted({T.b58encode(k) for sn in snaps for k in sn})
    print("\n%d distinct pools across all snapshots" % len(allkeys))
    side = resolve_sides(url, allkeys)
    print("  with a WSOL leg: %d"
          % sum(1 for v in side.values() if v))

    # per-period, per-pool drift. Sign convention: price of the TOKEN in SOL.
    print("\n=== per-period drift of the TOKEN price in SOL ===")
    per = periods_for(snaps, side, gap_h)

    if len(per) < 4:
        sys.exit("only %d usable periods" % len(per))

    cws = [p["cw"] for p in per]
    cwb = [p["cw_bracket"] for p in per]
    rnd = random.Random(7)
    boots = []
    for _ in range(4000):
        s = [cws[rnd.randrange(len(cws))] for _ in cws]
        boots.append(sum(s) / len(s))
    boots.sort()
    lo, hi = boots[100], boots[3899]
    mean = sum(cws) / len(cws)
    npos = sum(1 for c in cws if c > 0)
    print("\n=== the drift, with the CI taken over %d PERIODS ===" % len(per))
    print("  mean per-period drift: %+.4f%%   CI [%+.4f%%, %+.4f%%]"
          % (100 * mean, 100 * lo, 100 * hi))
    print("  periods positive: %d of %d" % (npos, len(cws)))
    mb = sum(cwb) / len(cwb)
    print("  survivorship-BRACKETED mean: %+.4f%% per period (%+.4f%%/day)"
          % (100 * mb, 100 * mb * (24.0 / gap_h)))
    print("  per period of %.2f h, so per day: %+.4f%%"
          % (gap_h, 100 * mean * (24.0 / gap_h)))
    surv = sum(p["surv"] for p in per) / len(per)
    print("  mean survival of a TVL>=%.0f SOL pool across %.2f h: %.0f%%"
          % (TVL_FLOOR, gap_h, 100 * surv))

    # compare against the excess the selection test produced
    excess_day = 0.002060          # +0.206%/day, pumpswapnet.py top-10% OOS
    cost_day = abs(mean / 2) * (24.0 / gap_h)
    print("\n=== does the drift cost swamp the selected excess? ===")
    print("  LP drift cost is sqrt(), i.e. half the log drift: %+.4f%%/day"
          % (100 * (mean / 2) * (24.0 / gap_h)))
    print("  the selected excess from pumpswapnet.py was      %+.4f%%/day"
          % (100 * excess_day))
    print("  |cost| / excess = %.1fx" % (cost_day / excess_day))
    # THE VERDICT MUST READ THE BRACKET, NOT THE SURVIVORS-ONLY ARM. The
    # survivors arm is +12.3% in 15 of 15 periods, which is not a market fact:
    # pricing a pool requires trades inside the snapshot window, so "priced at
    # t1" conditions on the pool still being ACTIVE at t1, and activity is
    # outcome-correlated for a memecoin. That is the same defect as
    # `quote-availability-is-not-trade-availability` and
    # `close-ts-is-outcome-dependent-in-inplay-binaries`: the observation is
    # caused by what is being measured. The bracket's two arms are the two
    # defensible readings, so the drift is identified only if BOTH share a sign.
    straddles = (lo < 0 < hi) or (mean > 0) != (mb > 0)
    print("\n  bracket on the per-period drift: [%+.4f%%, %+.4f%%]"
          % (100 * min(mean, mb), 100 * max(mean, mb)))
    if (mean > 0) != (mb > 0):
        print("  the two arms DISAGREE ON SIGN, so the drift is not identified:")
        print("  survival was %.0f%%, and the verdict turns entirely on how the"
              % (100 * surv))
        print("  %.0f%% of pools that stopped trading are marked."
              % (100 * (1 - surv)))
    if straddles:
        print("\n  The drift CI still straddles zero over %.1f h of periods." % args.hours)
        print("  That is now a REAL null rather than an artefact: the LP's")
        print("  dominant term has no measurable sign, so the seat's return is")
        print("  dominated by an unforecastable coin flip %.1fx the size of the"
              % (cost_day / excess_day))
        print("  edge. A seat like that is not a business even with a positive")
        print("  and selectable excess.")
    elif mean < 0:
        print("\n  The drift is significantly NEGATIVE: the LP is long a falling")
        print("  token and the excess cannot pay for it. CLOSED as ADVERSE.")
    else:
        print("\n  The drift is significantly POSITIVE over this sample, which is")
        print("  a directional bet on memecoins, not a market-making edge. The")
        print("  seat would be a long position wearing a fee coupon.")

    sweep = gate_sweep(url, centres, args, side, gap_h) if args.sweep else []

    with open(args.json, "w") as fh:
        json.dump({"snaps": args.snaps, "hours": args.hours, "gap_h": gap_h,
                   "head": head, "min_ev": args.minev,
                   "periods": per, "mean_drift": mean, "ci": [lo, hi],
                   "n_pos": npos, "survival": surv, "bracket_mean": mb,
                   "cost_over_excess": cost_day / excess_day,
                   "gate_sweep": sweep,
                   "drift_identified": not straddles}, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
