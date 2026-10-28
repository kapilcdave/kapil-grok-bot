"""Does the BIRTH COHORT's own selling supply the exit that no price-path rule could?

Pre-registered in preregistrations/pumpfun_cohort_exit_20261003.md.

Entry is the pumporacle convention (state left by the last trade at slot <= birth+L),
so the birth trade is never tradeable. The exit is the only thing under test, and it is
always scored as a PAIRED difference against the clip-matched fixed hold on the identical
mint set -- so the clip change (Lever A) cancels exactly and cannot inflate the signal.

    python3 pumpcohort.py --json data/pumpcohort.json
"""
import argparse
import collections
import json
import random
import sys

import pumpcurve as C
import pumporacle as O

H_MAX = 300.0
L_SLOTS = 2
THETAS = [0.0, 0.05, 0.10, 0.25]
FIX_HOLD = 10.0


# ---------------------------------------------------------------- cohort build

def cohort_of(tape, include_birth):
    """Wallets whose FIRST buy on this mint lands in the birth slot.

    Alg 1 of Luo et al. excludes the creator; `include_birth` restores it,
    because my own decomposition locates the profit in the birth transaction.
    """
    birth_slot = tape[0]["slot"]
    birth_wallet = tape[0]["user"]
    first_buy = {}
    for r in tape:
        if r["buy"] and r["user"] not in first_buy:
            first_buy[r["user"]] = r["slot"]
    out = {w for w, s in first_buy.items() if s == birth_slot}
    if not include_birth:
        out.discard(birth_wallet)
    return out, first_buy


def entry_index(tape, L):
    ei = -1
    for i, r in enumerate(tape):
        if r["slot"] <= tape[0]["slot"] + L:
            ei = i
        else:
            break
    return ei


# ----------------------------------------------------------------- exit arms

def trigger_index(tape, ei, t_ent, birth, cohort, theta, lag_slots=0):
    """First trade after entry where cohort sold_frac >= theta.

    Returns (index, elapsed_since_entry) or (None, None) if never reached
    inside H_MAX. Both legs of sold_frac are observable in real time.
    """
    bought = sold = 0.0
    # cohort buys up to and including the entry state already count
    for r in tape[:ei + 1]:
        if r["user"] in cohort:
            if r["buy"]:
                bought += r["tok"]
            else:
                sold += r["tok"]
    for i in range(ei + 1, len(tape)):
        r = tape[i]
        dt = O.ts_of(r, birth) - t_ent
        if dt > H_MAX:
            return None, None
        if r["user"] in cohort:
            if r["buy"]:
                bought += r["tok"]
            else:
                sold += r["tok"]
        if bought > 0 and sold / bought >= theta and sold > 0:
            if lag_slots <= 0:
                return i, dt
            want = r["slot"] + lag_slots
            for j in range(i, len(tape)):
                if tape[j]["slot"] >= want:
                    dj = O.ts_of(tape[j], birth) - t_ent
                    return (j, dj) if dj <= H_MAX else (None, None)
            return None, None
    return None, None


def index_at_elapsed(tape, ei, t_ent, birth, target):
    """Last trade whose elapsed-since-entry is <= target (a time-based exit)."""
    out = ei
    for i in range(ei, len(tape)):
        if O.ts_of(tape[i], birth) - t_ent <= target:
            out = i
        else:
            break
    return out


def pnl(ent, ex, clip_sol, fee, txcost_lamports):
    clip = clip_sol * C.LAMPORTS
    tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
    if tok <= 0:
        return None
    out, _, _ = C.sell_fill(ex["vsol"], ex["vtok"], tok, fee)
    return (out - clip - txcost_lamports) / clip


# ----------------------------------------------------------------- scoring

def build_cells(virgin, fee, txc, clip, include_birth):
    """Per-mint records carrying every arm's P&L on the IDENTICAL mint set."""
    recs = []
    for key, v in virgin.items():
        tape, birth = v["tape"], v["tape"][0]
        ei = entry_index(tape, L_SLOTS)
        if ei < 0:
            continue
        ent = tape[ei]
        t_ent = O.ts_of(ent, birth)
        cohort, first_buy = cohort_of(tape, include_birth)
        base = pnl(ent, tape[index_at_elapsed(tape, ei, t_ent, birth, FIX_HOLD)],
                   clip, fee, txc)
        if base is None:
            continue
        rec = {"w": birth["w"], "mint": key[1], "ei": ei, "t_ent": t_ent,
               "ncoh": len(cohort), "fix": base,
               "hold300": pnl(ent, tape[index_at_elapsed(
                   tape, ei, t_ent, birth, H_MAX)], clip, fee, txc)}
        for th in THETAS:
            i, dt = trigger_index(tape, ei, t_ent, birth, cohort, th)
            if i is None:
                rec["t%g" % th] = rec["hold300"]
                rec["fired%g" % th] = 0
                rec["dt%g" % th] = None
            else:
                rec["t%g" % th] = pnl(ent, tape[i], clip, fee, txc)
                rec["fired%g" % th] = 1
                rec["dt%g" % th] = dt
        # one-slot-late variant of the primary trigger, for the latency row
        i, dt = trigger_index(tape, ei, t_ent, birth, cohort, 0.0, lag_slots=1)
        rec["t0_lag1"] = rec["hold300"] if i is None else pnl(
            ent, tape[i], clip, fee, txc)
        recs.append(rec)
    return recs


def paired(recs, arm, ref="fix"):
    d = [r[arm] - r[ref] for r in recs if r[arm] is not None and r[ref] is not None]
    if len(d) < 3:
        return None
    lo, hi = O.bootstrap_mean_ci(d)
    return {"n": len(d), "mean": sum(d) / len(d), "ci_lo": lo, "ci_hi": hi,
            "arm_mean": sum(r[arm] for r in recs) / len(recs),
            "ref_mean": sum(r[ref] for r in recs) / len(recs)}


def window_block_ci(recs, arm, ref="fix", B=800, seed=11):
    """Block bootstrap over the 16 windows: the wider interval governs."""
    by_w = collections.defaultdict(list)
    for r in recs:
        if r[arm] is not None and r[ref] is not None:
            by_w[r["w"]].append(r[arm] - r[ref])
    ws = list(by_w)
    if len(ws) < 3:
        return float("nan"), float("nan")
    rnd, ms = random.Random(seed), []
    for _ in range(B):
        pool = []
        for _ in range(len(ws)):
            pool.extend(by_w[ws[rnd.randrange(len(ws))]])
        if pool:
            ms.append(sum(pool) / len(pool))
    ms.sort()
    return ms[int(.025 * len(ms))], ms[int(.975 * len(ms))]


# -------------------------------------------------------------------- nulls

def null_time_matched(virgin, keys, theta, fee, txc, clip, include_birth,
                      draws=200, seed=23):
    """Permute the cohort exit ELAPSED TIMES across mints.

    Holds the holding-period distribution exactly fixed and destroys only the
    mint-specific timing (`a-null-must-hold-turnover-fixed` -- roll, never
    redraw). If the arm beats this, the gain is WHEN it exits, not how long.
    """
    pre = []
    for k in keys:
        v = virgin[k]
        tape, birth = v["tape"], v["tape"][0]
        ei = entry_index(tape, L_SLOTS)
        ent = tape[ei]
        t_ent = O.ts_of(ent, birth)
        cohort, _ = cohort_of(tape, include_birth)
        i, dt = trigger_index(tape, ei, t_ent, birth, cohort, theta)
        pre.append((tape, birth, ei, ent, t_ent,
                    H_MAX if dt is None else dt,
                    pnl(ent, tape[index_at_elapsed(tape, ei, t_ent, birth,
                                                   FIX_HOLD)], clip, fee, txc)))
    times = [p[5] for p in pre]
    rnd, out = random.Random(seed), []
    for _ in range(draws):
        perm = times[:]
        rnd.shuffle(perm)
        ds = []
        for p, t in zip(pre, perm):
            tape, birth, ei, ent, t_ent, _, ref = p
            if ref is None:
                continue
            j = index_at_elapsed(tape, ei, t_ent, birth, t)
            x = pnl(ent, tape[j], clip, fee, txc)
            if x is not None:
                ds.append(x - ref)
        if ds:
            out.append(sum(ds) / len(ds))
    out.sort()
    return out


def null_labels(virgin, keys, theta, fee, txc, clip, include_birth,
                draws=200, seed=29):
    """Permute cohort MEMBERSHIP among wallets whose first buy is in the first
    10 slots, then recompute the trigger end-to-end. Tests the cohort IDENTITY
    rather than the shape of the rule."""
    pre = []
    for k in keys:
        v = virgin[k]
        tape, birth = v["tape"], v["tape"][0]
        ei = entry_index(tape, L_SLOTS)
        ent = tape[ei]
        t_ent = O.ts_of(ent, birth)
        cohort, first_buy = cohort_of(tape, include_birth)
        elig = [w for w, s in first_buy.items()
                if s <= birth["slot"] + 10
                and (include_birth or w != birth["user"])]
        ref = pnl(ent, tape[index_at_elapsed(tape, ei, t_ent, birth, FIX_HOLD)],
                  clip, fee, txc)
        pre.append((tape, birth, ei, ent, t_ent, len(cohort), elig, ref))
    rnd, out = random.Random(seed), []
    for _ in range(draws):
        ds = []
        for tape, birth, ei, ent, t_ent, ncoh, elig, ref in pre:
            if ref is None:
                continue
            fake = set(rnd.sample(elig, min(ncoh, len(elig)))) if elig else set()
            i, _ = trigger_index(tape, ei, t_ent, birth, fake, theta)
            j = i if i is not None else index_at_elapsed(
                tape, ei, t_ent, birth, H_MAX)
            x = pnl(ent, tape[j], clip, fee, txc)
            if x is not None:
                ds.append(x - ref)
        if ds:
            out.append(sum(ds) / len(ds))
    out.sort()
    return out


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/w*.jsonl.gz")
    ap.add_argument("--json", default=None)
    ap.add_argument("--draws", type=int, default=200)
    a = ap.parse_args()

    rows, metas = O.load(a.tape)
    print("loaded %d events from %d windows" % (len(rows), len(metas)))
    virgin, seen, states = O.build_mints(rows, metas)
    print("curve configs: %s" % states)
    print("virgin curves born in-window: %d of %d mints seen" % (len(virgin), seen))

    virgin = O.restrict_covered(virgin, L_SLOTS, H_MAX)
    print("with %gs of forward tape after entry (L=%d): %d  [FIXED MINT SET]"
          % (H_MAX, L_SLOTS, len(virgin)))
    txc = O.txcost(virgin)
    fee = C.FEE_BPS / 1e4
    print("median on-chain fee: %.0f lamports;  fee %d bps/side" % (txc, C.FEE_BPS))

    keys = sorted(virgin)
    train = [k for k in keys if virgin[k]["tape"][0]["w"] <= 7]
    test = [k for k in keys if virgin[k]["tape"][0]["w"] >= 8]
    print("walk-forward: %d train mints (w0-7), %d test mints (w8-15)"
          % (len(train), len(test)))
    cons = {k for k in keys if O.reserve_consistent(virgin[k])}
    print("reserve-consistent (own trades only): %d (%.1f%%)"
          % (len(cons), 100.0 * len(cons) / max(1, len(keys))))

    res = {"n_mints": len(keys), "n_train": len(train), "n_test": len(test),
           "n_consistent": len(cons), "txcost_lamports": txc,
           "fee_bps": C.FEE_BPS, "cells": {}, "selection": {}, "nulls": {}}

    # ---- Lever A: the clip arithmetic, declared as a prediction in prereg SS1
    print("\n== LEVER A: clip arithmetic (no signal), fixed hold %gs ==" % FIX_HOLD)
    print("%-8s %8s %10s %10s" % ("clip", "n", "mean", "median"))
    for clip in (1.0, 0.3, 0.1):
        recs = build_cells(virgin, fee, txc, clip, include_birth=False)
        xs = sorted(r["fix"] for r in recs)
        m = sum(xs) / len(xs)
        print("%-8.2f %8d %9.2f%% %9.2f%%" % (clip, len(xs), 100 * m,
                                              100 * C.median(xs)))
        res["cells"]["leverA_clip_%g" % clip] = {"n": len(xs), "mean": m,
                                                 "median": C.median(xs)}

    # ---- Lever B: the cohort trigger, paired against the clip-matched fixed hold
    for clip in (0.3, 1.0):
        for include_birth in (False, True):
            arm = "incl" if include_birth else "excl"
            recs = build_cells(virgin, fee, txc, clip, include_birth)
            by_k = {r["mint"]: r for r in recs}
            tr = [by_k[k[1]] for k in train if k[1] in by_k]
            te = [by_k[k[1]] for k in test if k[1] in by_k]
            print("\n== LEVER B: clip %.2f SOL, cohort=%s ==" % (clip, arm))
            print("  cohort present on %.1f%% of mints, median size %g"
                  % (100.0 * sum(1 for r in recs if r["ncoh"] > 0) / len(recs),
                     C.median(sorted(r["ncoh"] for r in recs))))
            print("  %-10s %8s %9s %9s %9s %18s" %
                  ("arm", "fired%", "arm mean", "fix mean", "paired", "95% CI (mint)"))
            for th in THETAS:
                k = "t%g" % th
                p = paired(recs, k)
                fired = 100.0 * sum(r["fired%g" % th] for r in recs) / len(recs)
                print("  theta>=%-4g %7.1f%% %8.2f%% %8.2f%% %8.2f%%  [%+.2f%%, %+.2f%%]"
                      % (th, fired, 100 * p["arm_mean"], 100 * p["ref_mean"],
                         100 * p["mean"], 100 * p["ci_lo"], 100 * p["ci_hi"]))
                res["cells"]["clip%g_%s_theta%g" % (clip, arm, th)] = dict(
                    p, fired_pct=fired, clip=clip, cohort=arm, theta=th)
            p = paired(recs, "hold300")
            print("  %-10s %7s  %8.2f%% %8.2f%% %8.2f%%  [%+.2f%%, %+.2f%%]"
                  % ("hold 300s", "-", 100 * p["arm_mean"], 100 * p["ref_mean"],
                     100 * p["mean"], 100 * p["ci_lo"], 100 * p["ci_hi"]))
            p = paired(recs, "t0_lag1")
            print("  %-10s %7s  %8.2f%% %8.2f%% %8.2f%%  [%+.2f%%, %+.2f%%]"
                  % ("theta0 +1sl", "-", 100 * p["arm_mean"], 100 * p["ref_mean"],
                     100 * p["mean"], 100 * p["ci_lo"], 100 * p["ci_hi"]))
            if clip == 0.3:
                # walk-forward selection happens on the primary clip only
                best, bestv = None, None
                for th in THETAS:
                    q = paired(tr, "t%g" % th)
                    if q and (bestv is None or q["mean"] > bestv):
                        best, bestv = th, q["mean"]
                q = paired(te, "t%g" % best)
                wlo, whi = window_block_ci(te, "t%g" % best)
                print("  SELECTED on w0-7: theta=%g (train paired %+.2f%%)"
                      % (best, 100 * bestv))
                print("  SCORED  on w8-15: paired %+.2f%%  mint CI [%+.2f%%, %+.2f%%]"
                      "  window CI [%+.2f%%, %+.2f%%]  arm mean %.2f%%"
                      % (100 * q["mean"], 100 * q["ci_lo"], 100 * q["ci_hi"],
                         100 * wlo, 100 * whi, 100 * q["arm_mean"]))
                cr = [r for r in te if ("%s" % r["mint"]) in
                      {k[1] for k in cons}]
                qc = paired(cr, "t%g" % best)
                if qc:
                    print("  reserve-consistent test subset: n=%d paired %+.2f%%"
                          "  [%+.2f%%, %+.2f%%]"
                          % (qc["n"], 100 * qc["mean"], 100 * qc["ci_lo"],
                             100 * qc["ci_hi"]))
                res["selection"][arm] = {
                    "theta": best, "train_paired": bestv, "test": q,
                    "test_window_ci": [wlo, whi],
                    "test_consistent": qc}
                tk = [k for k in test if k[1] in by_k]
                nt = null_time_matched(virgin, tk, best, fee, txc, clip,
                                       include_birth, draws=a.draws)
                nl = null_labels(virgin, tk, best, fee, txc, clip,
                                 include_birth, draws=a.draws)
                print("  NULL time-matched : p50 %+.2f%%  p95 %+.2f%%  max %+.2f%%"
                      % (100 * nt[len(nt) // 2], 100 * nt[int(.95 * len(nt))],
                         100 * nt[-1]))
                print("  NULL label-shuffle: p50 %+.2f%%  p95 %+.2f%%  max %+.2f%%"
                      % (100 * nl[len(nl) // 2], 100 * nl[int(.95 * len(nl))],
                         100 * nl[-1]))
                res["nulls"][arm] = {
                    "theta": best,
                    "time_matched_p50": nt[len(nt) // 2],
                    "time_matched_p95": nt[int(.95 * len(nt))],
                    "time_matched_max": nt[-1],
                    "label_shuffle_p50": nl[len(nl) // 2],
                    "label_shuffle_p95": nl[int(.95 * len(nl))],
                    "label_shuffle_max": nl[-1]}

    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1, default=str)
        print("\nwrote %s" % a.json)


if __name__ == "__main__":
    main()
