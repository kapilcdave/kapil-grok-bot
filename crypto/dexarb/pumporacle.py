"""Oracle bound on scalping a freshly minted pump.fun curve.

Pre-registered in preregistrations/pumpfun_first_trade_oracle_20260918.md.

The strategy is handed PERFECT FORESIGHT over its exit and the tape is held
fixed while our clip is inserted. Both make the result optimistic, which is the
only reason a nonpositive answer is decisive.

    python3 pumporacle.py                       # all complete windows
    python3 pumporacle.py --json data/pumporacle.json
"""
import argparse
import collections
import glob
import gzip
import json
import math
import os
import random
import sys

import pumpcurve as C

SLOT_SEC = 0.4


def load(pattern):
    """Rows from every window that finished. Returns (rows, window_meta)."""
    rows, metas = [], []
    for path in sorted(glob.glob(pattern)):
        got, meta = [], None
        try:
            with gzip.open(path, "rt") as fh:
                for line in fh:
                    d = json.loads(line)
                    if d.get("DONE"):
                        meta = d
                    else:
                        got.append(d)
        except (OSError, EOFError, json.JSONDecodeError) as e:
            print("  skip %s (%s)" % (os.path.basename(path), type(e).__name__))
            continue
        if meta is None:
            print("  skip %s (still being written)" % os.path.basename(path))
            continue
        for r in got:
            r["w"] = meta["window"]
        rows.extend(got)
        metas.append(meta)
    return rows, metas


def build_mints(rows, metas, states=None):
    """Group into per-mint tapes; keep only curves born inside a window."""
    if states is None:
        states = C.discover_initial_states(rows)
    by_w = {m["window"]: m for m in metas}
    seq = collections.defaultdict(list)
    for i, r in enumerate(rows):
        seq[(r["w"], r["mint"])].append((r["slot"], i, r))

    virgin, seen = {}, 0
    for key, lst in seq.items():
        lst.sort()
        tape = [r for _, _, r in lst]
        seen += 1
        if not C.is_virgin(tape[0], states):
            continue                     # curve predates the window
        w = by_w[key[0]]
        end_slot = w["lo_slot"] + w["slots"]
        # blockTime is wall clock; fall back to slot arithmetic if absent.
        t0 = tape[0].get("bt")
        end_ts = (t0 + (end_slot - tape[0]["slot"]) * SLOT_SEC) if t0 else None
        virgin[key] = {"tape": tape, "end_slot": end_slot, "end_ts": end_ts}
    return virgin, seen, states


def ts_of(r, base):
    """Seconds since the birth trade. Prefers blockTime, falls back to slots."""
    if r.get("bt") and base.get("bt"):
        return r["bt"] - base["bt"]
    return (r["slot"] - base["slot"]) * SLOT_SEC


def oracle_cell(virgin, fee, L_slots, H_sec, clip_sol, txcost_lamports):
    """Mean/median oracle net for one (L, H, clip) cell.

    Entry: the curve state left by the last trade at slot <= birth + L. That
    models our transaction landing in slot birth+L, behind everything already
    in the ledger. Entry may never use the birth state at L>0 -- the birth trade
    is what informs us, so it is not tradeable
    (`the-most-eligible-bar-is-the-one-you-cannot-see`).

    Exit: the single best reachable curve state in (entry, entry+H], chosen with
    perfect foresight. The entry state itself is a candidate, so the floor of
    the bound is the pure cost of an instant round trip -- the oracle must
    trade, it only picks when to leave. Letting it decline to trade would be
    selection on the outcome.
    """
    nets, unenterable, uncovered = [], 0, 0
    for v in virgin.values():
        tape, birth = v["tape"], v["tape"][0]
        entry_slot = birth["slot"] + L_slots

        if L_slots < 0:
            # Absolute physical ceiling: we land immediately behind the birth
            # trade itself, ahead of every other wallet in the birth slot. Not
            # reachable without being in the mint bundle; reported as a ceiling.
            ei = 0
        else:
            # last trade at or before entry_slot. At L=0 this puts us behind
            # everyone else already in the birth slot, which is what a
            # same-block entry actually means.
            ei = -1
            for i, r in enumerate(tape):
                if r["slot"] <= entry_slot:
                    ei = i
                else:
                    break
        if ei < 0:
            unenterable += 1
            continue

        ent = tape[ei]
        t_ent = ts_of(ent, birth)
        # the window must actually contain H seconds of forward tape
        if v["end_ts"] is not None and birth.get("bt") is not None:
            if (v["end_ts"] - birth["bt"]) - t_ent < H_sec:
                uncovered += 1
                continue
        else:
            if (v["end_slot"] - ent["slot"]) * SLOT_SEC < H_sec:
                uncovered += 1
                continue

        clip = clip_sol * C.LAMPORTS
        tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
        if tok <= 0:
            unenterable += 1
            continue

        best = None
        for r in tape[ei:]:
            if ts_of(r, birth) - t_ent > H_sec:
                break
            out, _, _ = C.sell_fill(r["vsol"], r["vtok"], tok, fee)
            if best is None or out > best:
                best = out
        if best is None:
            unenterable += 1
            continue

        nets.append((best - clip - txcost_lamports) / C.LAMPORTS)

    if not nets:
        return None
    nets_sorted = sorted(nets)
    n = len(nets)
    mean = sum(nets) / n
    lo, hi = bootstrap_mean_ci(nets)
    return {"n": n, "unenterable": unenterable, "uncovered": uncovered,
            "mean_sol": mean, "median_sol": C.median(nets_sorted),
            "mean_frac": mean / clip_sol,
            "median_frac": C.median(nets_sorted) / clip_sol,
            "ci_lo_frac": lo / clip_sol, "ci_hi_frac": hi / clip_sol,
            "p_win": sum(1 for x in nets if x > 0) / n,
            "p5_frac": nets_sorted[int(.05 * n)] / clip_sol,
            "p95_frac": nets_sorted[min(n - 1, int(.95 * n))] / clip_sol}


def reserve_consistent(v, tol=1e-6):
    """True iff every consecutive transition in this curve's tape moves the SOL
    reserve by exactly the reported trade size.

    19.4% of transitions fail this: `virtual_sol_reserves` moves 2-153 SOL with
    the token reserve bit-identical, so SOL leaves the curve outside the trade
    stream (`per-observation-invariance-does-not-validate-the-state-machine`).
    That matters here for a specific reason: the oracle takes the MAX over
    states, so it preferentially selects states whose vsol jumped up, which
    inflates the bound. Filtering is itself outcome-correlated -- the jumpy
    curves are disproportionately the winners -- so this is reported as a
    robustness row alongside the full sample, never as the headline."""
    t = v["tape"]
    for i in range(1, len(t)):
        prev, r = t[i - 1], t[i]
        if prev["vtok"] != C.pre_reserves(r)[1]:
            return False
        d = r["vsol"] - prev["vsol"]
        s = r["sol"] * (1 if r["buy"] else -1)
        if abs(d - s) > tol * max(1.0, abs(s)):
            return False
    return True


def restrict_covered(virgin, L_slots, H_max):
    """Keep only mints with H_max seconds of forward tape after entry.

    A horizon sweep whose sample shrinks with H does not measure a horizon
    effect: the H=900 cell holds only mints born in the first half of a window,
    so the gradient mixes a real time dependence with a change of sample
    (`capture-is-an-identity-so-pooled-capture-is-a-spread-mix`). Holding the
    mint set fixed at the longest horizon separates the two."""
    out = {}
    for k, v in virgin.items():
        tape, birth = v["tape"], v["tape"][0]
        if L_slots < 0:
            ei = 0
        else:
            ei = -1
            for i, r in enumerate(tape):
                if r["slot"] <= birth["slot"] + L_slots:
                    ei = i
                else:
                    break
        if ei < 0:
            continue
        ent = tape[ei]
        t_ent = ts_of(ent, birth)
        if v["end_ts"] is not None and birth.get("bt") is not None:
            ok = (v["end_ts"] - birth["bt"]) - t_ent >= H_max
        else:
            ok = (v["end_slot"] - ent["slot"]) * SLOT_SEC >= H_max
        if ok:
            out[k] = v
    return out


def bootstrap_mean_ci(xs, B=800, seed=7):
    rnd = random.Random(seed)
    n = len(xs)
    if n < 3:
        return float("nan"), float("nan")
    ms = []
    for _ in range(B):
        s = 0.0
        for _ in range(n):
            s += xs[rnd.randrange(n)]
        ms.append(s / n)
    ms.sort()
    return ms[int(.025 * B)], ms[int(.975 * B)]


def txcost(virgin):
    """Median on-chain fee actually paid by real traders in a mint's first 60 s.

    This is base + priority fee. Jito tips are separate SOL transfers and are
    NOT visible here, so this UNDERSTATES the true cost of landing a
    competitive snipe -- another way the bound is optimistic."""
    fees = []
    for v in virgin.values():
        birth = v["tape"][0]
        for r in v["tape"]:
            if ts_of(r, birth) <= 60:
                fees.append(r.get("fee", 0))
    fees.sort()
    return C.median(fees) if fees else 5000.0


# --------------------------------------------------------------------------
# secondary: is greenness anything other than net inflow?

def identity_check(virgin):
    """On a deterministic curve, price rises iff net SOL flow into the curve is
    positive. So 'the first candle is green' should be EXACTLY 'net flow was
    positive', with no residual. Measured, not asserted."""
    out = {}
    for H in (10, 30, 60):
        up = flow_pos = agree = n = 0
        for v in virgin.values():
            tape, birth = v["tape"], v["tape"][0]
            if (v["end_slot"] - birth["slot"]) * SLOT_SEC < H:
                continue
            last = None
            net = 0
            for r in tape:
                if ts_of(r, birth) > H:
                    break
                last = r
                net += r["sol"] if r["buy"] else -r["sol"]
            if last is None:
                continue
            n += 1
            u = C.mid(last["vsol"], last["vtok"]) > C.mid(birth["vsol"],
                                                          birth["vtok"])
            f = net > 0
            up += u; flow_pos += f; agree += (u == f)
        if n:
            out[H] = {"n": n, "p_up": up / n, "p_netflow_pos": flow_pos / n,
                      "agreement": agree / n}
    return out


def base_rates(virgin):
    """Where do these curves end up? Peak vsol is the money raised proxy."""
    peaks, births, trades = [], [], []
    for v in virgin.values():
        tape = v["tape"]
        peaks.append(max(r["vsol"] for r in tape) / C.LAMPORTS)
        births.append(tape[0]["vsol"] / C.LAMPORTS)
        trades.append(len(tape))
    peaks.sort(); trades.sort()
    n = len(peaks)
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]
    return {"n": n,
            "peak_vsol_p50": C.median(peaks), "peak_vsol_p90": q(peaks, .90),
            "peak_vsol_p99": q(peaks, .99), "peak_vsol_max": peaks[-1],
            "trades_p50": C.median(trades), "trades_p90": q(trades, .90),
            "share_peak_under_35sol": sum(1 for p in peaks if p < 35) / n,
            "share_peak_over_100sol": sum(1 for p in peaks if p > 100) / n}


def birth_slot_share(virgin):
    """How much of the first 60 s of buying comes from wallets already present
    in the birth slot -- i.e. how much of the green opening was gone before any
    outside observer could have seen the mint at all."""
    num = den = 0
    for v in virgin.values():
        tape, birth = v["tape"], v["tape"][0]
        insiders = {r["user"] for r in tape if r["slot"] == birth["slot"]}
        for r in tape:
            if ts_of(r, birth) > 60:
                break
            if r["buy"]:
                den += r["sol"]
                if r["user"] in insiders:
                    num += r["sol"]
    return num / den if den else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    ap.add_argument("--json", default="data/pumporacle.json")
    ap.add_argument("--clips", default="0.1,0.5,1.0,5.0")
    ap.add_argument("--fee-bps", type=int, default=C.FEE_BPS)
    ap.add_argument("--lat", default="-1,0,1,2,4,8,16")
    ap.add_argument("--hor", default="10,30,60,300,900")
    args = ap.parse_args()

    rows, metas = load(args.tape)
    if not rows:
        sys.exit("no completed windows yet")
    print("%d trades from %d complete windows (%d blocks, %d skipped, %d failed)"
          % (len(rows), len(metas), sum(m["blocks_ok"] for m in metas),
             sum(m["skipped"] for m in metas), sum(m["failed"] for m in metas)))

    states = C.discover_initial_states(rows)
    print("\ncurve configurations discovered in tape (virgin pre-trade vtok):")
    for st in states:
        print("   %18d  (%.1fM tokens)" % (st, st / 1e6 / 1e6))
    virgin, seen, _ = build_mints(rows, metas, states)
    print("%d distinct (window,mint) tapes; %d are curves BORN in-window"
          % (seen, len(virgin)))
    if not virgin:
        sys.exit("no virgin curves")

    # Confirm the fee sits OUTSIDE sol_amount before charging it.
    bf, sf = C.fee_estimates(rows)
    print("\nimplied fee if sol_amount were gross: buy %.5f (n=%d), "
          "sell %.5f (n=%d)" % (C.median(bf), len(bf), C.median(sf), len(sf)))
    print("  ~0 confirms sol_amount is the CURVE-MOVING amount.")
    fee = args.fee_bps / 1e4
    print("  charging %d bps per side (read from fee_basis_points; p90 is %d "
          "with creator fee)" % (args.fee_bps, C.FEE_BPS_P90))

    tc = txcost(virgin)
    print("on-chain fee actually paid, first 60 s, median %.0f lamports "
          "(%.6f SOL); charged on BOTH legs. Jito tips not included."
          % (tc, tc / C.LAMPORTS))

    clips = [float(x) for x in args.clips.split(",")]
    lats = [int(x) for x in args.lat.split(",")]
    hors = [int(x) for x in args.hor.split(",")]

    out = {"n_virgin": len(virgin), "fee": fee, "txcost_lamports": tc,
           "windows": len(metas), "cells": []}

    for clip in clips:
        print("\n=== clip %.2f SOL === mean oracle net as %% of clip "
              "(perfect-foresight exit)" % clip)
        print("  %-6s" % "L\\H" + "".join("%12s" % ("%ds" % h) for h in hors))
        for L in lats:
            line = "  %-6s" % ("bundle" if L < 0 else "%d" % L)
            for H in hors:
                c = oracle_cell(virgin, fee, L, H, clip, tc * 2)
                if c is None:
                    line += "%12s" % "-"
                    continue
                c.update(clip=clip, L=L, H=H)
                out["cells"].append(c)
                line += "%11.2f%%" % (100 * c["mean_frac"])
            print(line)

    # detail at the realistic-latency row committed in the prereg
    print("\n--- L=2 slots (Geyser-class latency), clip 1.0 SOL, detail ---")
    print("  %5s %7s %10s %10s %10s %8s %9s %9s"
          % ("H", "n", "mean%", "ci_lo%", "ci_hi%", "p_win", "p5%", "p95%"))
    for H in hors:
        c = oracle_cell(virgin, fee, 2, H, 1.0, tc * 2)
        if not c:
            continue
        print("  %5d %7d %9.2f%% %9.2f%% %9.2f%% %8.3f %8.1f%% %8.1f%%"
              % (H, c["n"], 100 * c["mean_frac"], 100 * c["ci_lo_frac"],
                 100 * c["ci_hi_frac"], c["p_win"], 100 * c["p5_frac"],
                 100 * c["p95_frac"]))

    print("\n--- the SAME horizon sweep on a FIXED mint set (all covered to")
    print("    %ds), so the gradient is not a change of sample ---" % max(hors))
    fixed = restrict_covered(virgin, 2, max(hors))
    print("  %d of %d virgin curves survive the %ds coverage gate"
          % (len(fixed), len(virgin), max(hors)))
    out["fixed_sample"] = []
    if len(fixed) >= 30:
        print("  %5s %7s %10s %10s %8s %11s"
              % ("H", "n", "mean%", "ci_lo%", "p_win", "vs varying"))
        for H in hors:
            a = oracle_cell(fixed, fee, 2, H, 1.0, tc * 2)
            b = oracle_cell(virgin, fee, 2, H, 1.0, tc * 2)
            if not a:
                continue
            out["fixed_sample"].append({"H": H, "n": a["n"],
                                        "mean_frac": a["mean_frac"],
                                        "varying_mean": b["mean_frac"] if b
                                        else None})
            print("  %5d %7d %9.2f%% %9.2f%% %8.3f %10.2f%%"
                  % (H, a["n"], 100 * a["mean_frac"], 100 * a["ci_lo_frac"],
                     a["p_win"], 100 * b["mean_frac"] if b else float("nan")))
    else:
        print("  too few to compare")

    print("\n--- fee sensitivity: %d bps vs p90 %d bps, L=2, clip 1.0 SOL ---"
          % (args.fee_bps, C.FEE_BPS_P90))
    out["fee_sensitivity"] = []
    for H in hors:
        a = oracle_cell(virgin, fee, 2, H, 1.0, tc * 2)
        b = oracle_cell(virgin, C.FEE_BPS_P90 / 1e4, 2, H, 1.0, tc * 2)
        if not a or not b:
            continue
        out["fee_sensitivity"].append({"H": H, "at_fee": a["mean_frac"],
                                       "at_p90": b["mean_frac"]})
        print("  H=%4ds  mean %7.2f%% -> %7.2f%%  (delta %+.2f pp)"
              % (H, 100 * a["mean_frac"], 100 * b["mean_frac"],
                 100 * (b["mean_frac"] - a["mean_frac"])))

    print("\n--- secondary 1: is 'green' anything but net inflow? ---")
    ident = identity_check(virgin)
    out["identity"] = ident
    print("  %5s %7s %9s %13s %11s" % ("H", "n", "P(up)", "P(netflow>0)",
                                       "agreement"))
    for H, d in sorted(ident.items()):
        print("  %5d %7d %8.3f %12.3f %10.3f"
              % (H, d["n"], d["p_up"], d["p_netflow_pos"], d["agreement"]))

    print("\n--- secondary 2: base rates ---")
    br = base_rates(virgin)
    out["base_rates"] = br
    print("  peak virtual SOL reserve: p50 %.1f  p90 %.1f  p99 %.1f  max %.1f"
          % (br["peak_vsol_p50"], br["peak_vsol_p90"], br["peak_vsol_p99"],
             br["peak_vsol_max"]))
    print("  trades per curve: p50 %.0f  p90 %.0f" % (br["trades_p50"],
                                                      br["trades_p90"]))
    print("  share whose curve never passes 35 vSOL (started at 30): %.3f"
          % br["share_peak_under_35sol"])
    print("  share exceeding 100 vSOL: %.4f" % br["share_peak_over_100sol"])

    print("\n--- secondary 3: who is on the other side ---")
    bs = birth_slot_share(virgin)
    out["birth_slot_buy_share"] = bs
    print("  share of first-60s BUY volume from wallets present in the birth"
          " slot: %.3f" % bs)

    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
