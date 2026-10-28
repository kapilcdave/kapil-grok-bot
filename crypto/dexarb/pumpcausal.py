"""What a CAUSAL exit keeps of the oracle bound on a fresh pump.fun curve.

The oracle in pumporacle.py picks the exit with perfect foresight, so it is a
ceiling. This measures rules that use only information available at the time,
and reports the realisation fraction causal/oracle
(`gate-the-realisation-product-not-one-factor`).

The rule that decides whether this is honest: a trigger is not a fill. When
some other trade pushes the curve through our target, we cannot sell at that
price -- our transaction lands L_out slots later, at whatever the curve is by
then (`an-exit-price-must-not-know-the-exit-decision`,
`a-threshold-is-not-a-fill-price`).

    python3 pumpcausal.py
"""
import argparse
import json
import sys

import pumpcurve as C
import pumporacle as O


def entry_of(v, L_slots):
    """Same entry model as the oracle: the state left by the last trade at or
    before birth+L. Returns (index, row) or (None, None)."""
    tape, birth = v["tape"], v["tape"][0]
    if L_slots < 0:
        return 0, tape[0]
    ei = -1
    for i, r in enumerate(tape):
        if r["slot"] <= birth["slot"] + L_slots:
            ei = i
        else:
            break
    return (ei, tape[ei]) if ei >= 0 else (None, None)


def covered(v, ent, H):
    birth = v["tape"][0]
    t_ent = O.ts_of(ent, birth)
    if v["end_ts"] is not None and birth.get("bt") is not None:
        return (v["end_ts"] - birth["bt"]) - t_ent >= H
    return (v["end_slot"] - ent["slot"]) * O.SLOT_SEC >= H


def exit_after(tape, j, trigger_slot, L_out):
    """First tradeable state at or after trigger_slot + L_out.

    Scanning forward from j means the exit can only ever use a state that
    exists later in the tape than the decision to leave."""
    want = trigger_slot + L_out
    for i in range(j, len(tape)):
        if tape[i]["slot"] >= want:
            return tape[i]
    return None


def run_rule(virgin, fee, L_in, L_out, H, clip_sol, txc, rule, u=None, d=None):
    """rule: 'hold' exits at the first state >= H seconds after entry.
             'tpsl' exits when proceeds cross (1+u) or (1-d) of the clip,
                    filled L_out slots after the trigger, else at H."""
    clip = clip_sol * C.LAMPORTS
    nets, orc, n_trig, n_tp, n_timeout = [], [], 0, 0, 0
    for v in virgin.values():
        tape, birth = v["tape"], v["tape"][0]
        ei, ent = entry_of(v, L_in)
        if ent is None or not covered(v, ent, H):
            continue
        t_ent = O.ts_of(ent, birth)
        tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
        if tok <= 0:
            continue

        # window of states we are allowed to see
        idx = [i for i in range(ei, len(tape))
               if O.ts_of(tape[i], birth) - t_ent <= H]
        if not idx:
            continue

        # oracle over the same window, for the realisation ratio
        best = max(C.sell_fill(tape[i]["vsol"], tape[i]["vtok"], tok, fee)[0]
                   for i in idx)
        orc.append((best - clip - txc) / C.LAMPORTS)

        ex = None
        if rule == "hold":
            # hold for H seconds, then leave at the last state observable by
            # then. No foresight: the exit time was fixed at entry.
            ex = tape[idx[-1]]
        elif rule == "trail":
            # Steelman for a lottery-shaped payoff: ride the winners, cut at a
            # fraction q below the RUNNING MAX of proceeds. Uses only the past.
            peak = None
            for i in idx:
                out, _, _ = C.sell_fill(tape[i]["vsol"], tape[i]["vtok"],
                                        tok, fee)
                if peak is None or out > peak:
                    peak = out
                elif out <= (1 - u) * peak:
                    n_trig += 1
                    ex = exit_after(tape, i, tape[i]["slot"], L_out)
                    break
            if ex is None:
                ex = tape[idx[-1]]
                n_timeout += 1
        else:
            for i in idx:
                out, _, _ = C.sell_fill(tape[i]["vsol"], tape[i]["vtok"],
                                        tok, fee)
                hit_tp = u is not None and out >= (1 + u) * clip
                hit_sl = d is not None and out <= (1 - d) * clip
                if hit_tp or hit_sl:
                    n_trig += 1
                    n_tp += bool(hit_tp)
                    ex = exit_after(tape, i, tape[i]["slot"], L_out)
                    break
            if ex is None:
                ex = tape[idx[-1]]
                n_timeout += 1

        out, _, _ = C.sell_fill(ex["vsol"], ex["vtok"], tok, fee)
        nets.append((out - clip - txc) / C.LAMPORTS)

    if not nets:
        return None
    n = len(nets)
    mean = sum(nets) / n
    omean = sum(orc) / len(orc)
    lo, hi = O.bootstrap_mean_ci(nets)
    return {"n": n, "mean_frac": mean / clip_sol,
            "median_frac": C.median(sorted(nets)) / clip_sol,
            "ci_lo_frac": lo / clip_sol, "ci_hi_frac": hi / clip_sol,
            "p_win": sum(1 for x in nets if x > 0) / n,
            "oracle_frac": omean / clip_sol,
            "realisation": mean / omean if omean else float("nan"),
            "trig_rate": n_trig / n, "tp_share": n_tp / max(n_trig, 1),
            "timeout_rate": n_timeout / n}


def split_windows(virgin, metas):
    """Chronological split: earlier windows train, later windows test.

    Any parameter picked on the same data that scores it is an in-sample
    optimum (`oracle-bound-a-selection-closes-a-model-family`). The window
    structure makes an honest out-of-sample read available for free."""
    order = sorted(m["window"] for m in metas)
    if len(order) < 4:
        return None, None
    cut = len(order) // 2
    tr, te = set(order[:cut]), set(order[cut:])
    return ({k: v for k, v in virgin.items() if k[0] in tr},
            {k: v for k, v in virgin.items() if k[0] in te})


GRID = [("trail", q, None, H) for q in (0.05, 0.10, 0.15, 0.20, 0.30, 0.50)
        for H in (60, 300, 900)] + \
       [("hold", None, None, H) for H in (10, 30, 60, 300, 900)] + \
       [("tpsl", u, d, 300) for u in (0.2, 0.5, 1.0) for d in (0.1, 0.2, 0.5)]


def cost_floor(S, r, t):
    """Minimum round-trip cost as a fraction of clip, over ALL clip sizes.

    `f(c) = 2c/(S+2c) + 2r + t/c` on a curve holding S virtual SOL: own impact
    rises with the clip, the fixed transaction cost falls with it, so f has an
    interior minimum at `c* = sqrt(S*t/2)`. This is what makes "no clip size
    works" a provable statement rather than a sweep result -- a sweep can only
    report the clips it tried (`a-cap-that-doesnt-scale-reads-as-a-quiet-market`
    is the mirror error: a cost that does not scale with size).

    Returns (c_star, f_at_c_star, fee_share_of_floor)."""
    cs = (S * t / 2.0) ** 0.5
    f = 2 * cs / (S + 2 * cs) + 2 * r + t / cs
    return cs, f, 2 * r / f


def decompose(virgin, fee, L_in, txc, H, rule, u=None, d=None, tiny=1e-4):
    """Split one rule's P&L into the raw price move and each cost leg, by
    re-running the SAME rule with costs switched off one at a time.

    Own impact is the one cost that cannot be turned off inside the fill
    function, so the no-impact legs use a clip of `tiny` SOL, where impact is
    0.0007% -- three orders below the smallest effect being read.

    The point of this is that the cost side is closed form, so the branch turns
    on ONE measured quantity: the raw move. Comparing it to `cost_floor` decides
    the branch without any further fitting."""
    S = 30.0
    cs, floor, feeshare = cost_floor(S, fee, txc / C.LAMPORTS)
    # NB "gross", not "the price move available": this is the rule's own P&L
    # with costs removed, so the exit TIMING is still baked in. A negative gross
    # is a statement about the rule, not about whether the price ever rose.
    legs = [("gross P&L of the rule (no costs at all)", tiny, 0.0, 0.0),
            ("+ curve fee",                             tiny, fee, 0.0),
            ("+ own impact at c*",                      cs,   fee, 0.0),
            ("+ tx fee (2x observed p50)",              cs,   fee, txc),
            ("same at a 1.0 SOL clip",                  1.0,  fee, txc)]
    print("  cost floor over ALL clips: c* = %.4f SOL, f(c*) = %.2f%%, of which"
          "\n  %.0f%% is the protocol fee -- a constant no sizing can move"
          % (cs, 100 * floor, 100 * feeshare))
    print("  %-42s %8s %9s %9s %7s"
          % ("", "mean%", "ci_lo%", "ci_hi%", "p_win"))
    got, prev = [], None
    for lab, clip, fe, tx in legs:
        c = run_rule(virgin, fe, L_in, 2, H, clip, tx, rule, u=u, d=d)
        if not c:
            continue
        m = 100 * c["mean_frac"]
        print("  %-42s %7.2f%% %8.2f%% %8.2f%% %6.3f%s"
              % (lab, m, 100 * c["ci_lo_frac"], 100 * c["ci_hi_frac"],
                 c["p_win"], "" if prev is None else "   (%+.2fpp)" % (m - prev)))
        got.append(dict(leg=lab, clip=clip, **c))
        prev = m
    raw = got[0] if got else None
    if raw:
        lo, hi = 100 * raw["ci_lo_frac"], 100 * raw["ci_hi_frac"]
        # verdict per the committed rule in prereg section 5b
        if lo > 100 * floor:
            v = ("REOPEN: the raw move's LOWER bound clears the cost floor")
        elif hi < 0:
            # Distinct from "closed on cost", and the distinction is the whole
            # diagnosis: a NEGATIVE gross move means the rule loses before any
            # fee, so the cost floor is irrelevant and no cost reduction --
            # cheaper venue, bigger size, faster fill -- can help it.
            v = ("CLOSED as ADVERSE: the gross move is significantly NEGATIVE "
                 "(%+.2f%%, CI [%+.2f%%, %+.2f%%]), so the rule loses before a "
                 "single cost is charged and the %.2f%% floor never binds"
                 % (100 * raw["mean_frac"], lo, hi, 100 * floor))
        elif lo <= 0 <= hi:
            v = ("CLOSED as ABSENT: the raw move's CI includes zero, so there "
                 "is no move to monetise")
        elif hi < 100 * floor:
            v = ("CLOSED on COST: a real move of %+.2f%% strictly below the "
                 "%.2f%% floor" % (100 * raw["mean_frac"], 100 * floor))
        else:
            v = ("UNDECIDED: the raw move's CI straddles the %.2f%% floor"
                 % (100 * floor))
        print("  -> %s" % v)
    return {"floor": floor, "c_star": cs, "fee_share": feeshare,
            "legs": got, "verdict": v if raw else None}


def walkforward(virgin, metas, fee, L_in, clip, txc):
    tr, te = split_windows(virgin, metas)
    if tr is None:
        print("  need >=4 windows for a walk-forward split; have %d"
              % len(metas))
        return None
    print("  train %d curves (%d windows) / test %d curves (%d windows)"
          % (len(tr), len(metas) // 2, len(te), len(metas) - len(metas) // 2))
    scored = []
    for rule, u, d, H in GRID:
        a = run_rule(tr, fee, L_in, 2, H, clip, txc, rule, u, d)
        b = run_rule(te, fee, L_in, 2, H, clip, txc, rule, u, d)
        if a and b:
            scored.append((a["mean_frac"], b["mean_frac"], rule, u, d, H,
                           a["n"], b["n"], b["ci_lo_frac"], b["ci_hi_frac"]))
    if not scored:
        return None
    best_tr = max(scored, key=lambda t: t[0])
    best_te = max(scored, key=lambda t: t[1])
    lab = lambda t: "%s q/u=%s d=%s H=%ds" % (t[2], t[3], t[4], t[5])
    print("\n  selected ON TRAIN : %-32s train %+7.2f%% (n=%d)"
          % (lab(best_tr), 100 * best_tr[0], best_tr[6]))
    print("  its OUT-OF-SAMPLE : %-32s test  %+7.2f%%  CI [%+.2f%%, %+.2f%%] "
          "(n=%d)" % ("", 100 * best_tr[1], 100 * best_tr[8],
                      100 * best_tr[9], best_tr[7]))
    print("  best on TEST (not selectable in advance): %-18s %+7.2f%%"
          % (lab(best_te), 100 * best_te[1]))
    print("  cells with positive TEST mean: %d of %d"
          % (sum(1 for t in scored if t[1] > 0), len(scored)))
    return {"selected": {"rule": best_tr[2], "u": best_tr[3], "d": best_tr[4],
                         "H": best_tr[5], "train": best_tr[0],
                         "test": best_tr[1], "test_ci":
                         [best_tr[8], best_tr[9]], "n_test": best_tr[7]},
            "n_positive_test": sum(1 for t in scored if t[1] > 0),
            "n_cells": len(scored)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    ap.add_argument("--json", default="data/pumpcausal.json")
    ap.add_argument("--fee-bps", type=int, default=C.FEE_BPS)
    ap.add_argument("--clip", type=float, default=1.0)
    ap.add_argument("--lin", type=int, default=2)
    args = ap.parse_args()

    rows, metas = O.load(args.tape)
    if not rows:
        sys.exit("no completed windows yet")
    states = C.discover_initial_states(rows)
    virgin, seen, _ = O.build_mints(rows, metas, states)
    fee = args.fee_bps / 1e4
    txc = O.txcost(virgin) * 2
    print("%d trades, %d windows, %d virgin curves; fee %d bps/side, "
          "txcost %.6f SOL round trip"
          % (len(rows), len(metas), len(virgin), args.fee_bps,
             txc / C.LAMPORTS))
    print("blocks: %d ok, %d skipped, %d FAILED"
          % (sum(m["blocks_ok"] for m in metas),
             sum(m["skipped"] for m in metas),
             sum(m["failed"] for m in metas)))

    out = {"clip": args.clip, "L_in": args.lin, "fee_bps": args.fee_bps,
           "hold": [], "tpsl": []}

    # ---- the headline: cost side is closed form, so ONE number decides ----
    print("\n=== DECOMPOSITION: the rule's GROSS P&L vs the analytic cost floor.")
    print("    Best cell of the grid (trail q=15%, H=900s). Costs are switched")
    print("    off one leg at a time on the SAME rule and sample. Verdict rule")
    print("    committed in preregistrations/...20260918.md section 5b. ===")
    out["decomp"] = decompose(virgin, fee, args.lin, txc, 900, "trail", u=0.15)

    print("\n=== rule A: fixed hold, no foresight. clip %.2f SOL, L_in=%d ==="
          % (args.clip, args.lin))
    print("  %6s %7s %9s %9s %9s %8s %9s %8s"
          % ("hold", "n", "mean%", "ci_lo%", "ci_hi%", "p_win", "oracle%",
             "realis."))
    for H in (10, 30, 60, 300, 900):
        c = run_rule(virgin, fee, args.lin, 0, H, args.clip, txc, "hold")
        if not c:
            continue
        c["H"] = H
        out["hold"].append(c)
        print("  %5ds %7d %8.2f%% %8.2f%% %8.2f%% %8.3f %8.2f%% %7.3f"
              % (H, c["n"], 100 * c["mean_frac"], 100 * c["ci_lo_frac"],
                 100 * c["ci_hi_frac"], c["p_win"], 100 * c["oracle_frac"],
                 c["realisation"]))

    print("\n=== rule C: trailing stop q below running max of proceeds,")
    print("    exit filled L_out slots after the trigger. clip %.2f SOL ==="
          % args.clip)
    print("  %5s %6s %6s %7s %9s %9s %8s %8s %9s %8s"
          % ("q", "H", "L_out", "n", "mean%", "ci_lo%", "p_win", "trig",
             "oracle%", "realis."))
    out["trail"] = []
    for q in (0.15, 0.30, 0.50):
        for H in (60, 300, 900):
            c = run_rule(virgin, fee, args.lin, 2, H, args.clip, txc,
                         "trail", q)
            if not c:
                continue
            c.update(q=q, H=H, L_out=2)
            out["trail"].append(c)
            print("  %4.0f%% %5ds %6d %7d %8.2f%% %8.2f%% %8.3f %7.3f "
                  "%8.2f%% %7.3f"
                  % (100 * q, H, 2, c["n"], 100 * c["mean_frac"],
                     100 * c["ci_lo_frac"], c["p_win"], c["trig_rate"],
                     100 * c["oracle_frac"], c["realisation"]))

    print("\n=== rule B: take-profit / stop, exit filled L_out slots AFTER the")
    print("    trigger (a trigger is not a fill). H=300 s, clip %.2f SOL ==="
          % args.clip)
    print("  %5s %5s %6s %7s %9s %9s %8s %8s %9s %8s"
          % ("tp", "sl", "L_out", "n", "mean%", "ci_lo%", "p_win", "trig",
             "oracle%", "realis."))
    for u, d in ((0.20, 0.10), (0.50, 0.20), (1.00, 0.30), (0.20, 0.50)):
        for L_out in (0, 2, 8):
            c = run_rule(virgin, fee, args.lin, L_out, 300, args.clip, txc,
                         "tpsl", u, d)
            if not c:
                continue
            c.update(u=u, d=d, L_out=L_out, H=300)
            out["tpsl"].append(c)
            print("  %4.0f%% %4.0f%% %6d %7d %8.2f%% %8.2f%% %8.3f %7.3f "
                  "%8.2f%% %7.3f"
                  % (100 * u, 100 * d, L_out, c["n"], 100 * c["mean_frac"],
                     100 * c["ci_lo_frac"], c["p_win"], c["trig_rate"],
                     100 * c["oracle_frac"], c["realisation"]))

    print("\n=== rule A again on a FIXED mint set (all covered to 900s), so")
    print("    the horizon gradient is not a change of sample ===")
    fixed = O.restrict_covered(virgin, args.lin, 900)
    print("  %d of %d curves survive" % (len(fixed), len(virgin)))
    out["hold_fixed"] = []
    if len(fixed) >= 30:
        print("  %6s %7s %9s %9s %8s %9s %8s"
              % ("hold", "n", "mean%", "ci_lo%", "p_win", "oracle%", "realis."))
        for H in (10, 30, 60, 300, 900):
            c = run_rule(fixed, fee, args.lin, 0, H, args.clip, txc, "hold")
            if not c:
                continue
            c["H"] = H
            out["hold_fixed"].append(c)
            print("  %5ds %7d %8.2f%% %8.2f%% %8.3f %8.2f%% %7.3f"
                  % (H, c["n"], 100 * c["mean_frac"], 100 * c["ci_lo_frac"],
                     c["p_win"], 100 * c["oracle_frac"], c["realisation"]))

    clean = {k: v for k, v in virgin.items() if O.reserve_consistent(v)}
    print("\n=== ROBUSTNESS: curves whose reserves move ONLY by their trades")
    print("    (%d of %d, %.1f%%). The oracle takes a MAX over states, so a"
          % (len(clean), len(virgin), 100 * len(clean) / max(len(virgin), 1)))
    print("    spuriously high vsol inflates it; this removes that. NOTE the")
    print("    filter drops winners, so it should read WORSE, not better. ===")
    out["clean"] = []
    if len(clean) >= 30:
        print("  %6s %7s %9s %9s %8s %9s %8s"
              % ("hold", "n", "mean%", "ci_lo%", "p_win", "oracle%", "realis."))
        for H in (10, 60, 300, 900):
            c = run_rule(clean, fee, args.lin, 0, H, args.clip, txc, "hold")
            if not c:
                continue
            c["H"] = H
            out["clean"].append(c)
            print("  %5ds %7d %8.2f%% %8.2f%% %8.3f %8.2f%% %7.3f"
                  % (H, c["n"], 100 * c["mean_frac"], 100 * c["ci_lo_frac"],
                     c["p_win"], 100 * c["oracle_frac"], c["realisation"]))
        print("  trailing stop q=15%:")
        for H in (60, 300, 900):
            c = run_rule(clean, fee, args.lin, 2, H, args.clip, txc, "trail",
                         0.15)
            if not c:
                continue
            c.update(q=0.15, H=H, rule="trail")
            out["clean"].append(c)
            print("  %5ds %7d %8.2f%% %8.2f%% %8.3f %8.2f%% %7.3f"
                  % (H, c["n"], 100 * c["mean_frac"], 100 * c["ci_lo_frac"],
                     c["p_win"], 100 * c["oracle_frac"], c["realisation"]))

    print("\n=== walk-forward: parameter picked on EARLIER windows, scored on")
    print("    LATER ones. clip %.2f SOL, L_in=%d, %d cells ==="
          % (args.clip, args.lin, len(GRID)))
    out["walkforward"] = walkforward(virgin, metas, fee, args.lin, args.clip,
                                     txc)

    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
