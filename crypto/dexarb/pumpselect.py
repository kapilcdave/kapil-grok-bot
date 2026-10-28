"""Can an ENTRY screen rescue a losing causal exit on a fresh pump.fun curve?

The causal rules in pumpcausal.py enter every virgin curve. The standing
objection is that a filter would fix it. This closes that escape hatch two ways
instead of fitting features one at a time
(`oracle-bound-a-selection-closes-a-model-family`):

1. **Oracle-bound the selection at a fixed rate.** Keep the causal exit, but let
   perfect foresight pick which fraction p of mints to enter. That is the
   ceiling on ANY screen operating at rate p, feature-based or not.
2. **Measure whether entry-time features separate outcomes at all.** For each
   causal feature, quintile the mints and test the between-group SD of the bin
   means against the mean within-group SE
   (`between-group-sd-must-exceed-mean-within-group-se`). An apparent level
   difference smaller than its own noise is not a screen.

The prior is that no screen can help, because the loss is a cost LEVEL, not a
slope, and a filter cannot remove a constant
(`adverse-selection-is-a-level-toll-not-a-slope-killer`). Measuring it is what
turns that from an argument into a finding.

Every feature is computed from tape[0..entry] only, so nothing a screen sees
postdates the decision it informs.

    python3 pumpselect.py
"""
import argparse
import json
import sys

import pumpcurve as C
import pumporacle as O
import pumpcausal as A


def features(v, ei):
    """Entry-time features. Uses ONLY trades at or before the entry index."""
    tape = v["tape"]
    birth, ent = tape[0], tape[ei]
    pre = tape[:ei + 1]
    users = {r["user"] for r in pre}
    buys = [r for r in pre if r["buy"]]
    net = sum(r["sol"] if r["buy"] else -r["sol"] for r in pre)
    return {
        # size of the very first buy: how much the creator/bundle put in
        "birth_sol": birth["sol"] / C.LAMPORTS,
        # how much interest arrived before we could act
        "n_trades_pre": float(len(pre)),
        "n_wallets_pre": float(len(users)),
        # how far the curve has already been pushed off its virgin state
        "vsol_entry": ent["vsol"] / C.LAMPORTS,
        # signed pre-entry flow, and its share
        "flow_pre": net / C.LAMPORTS,
        "buy_share_pre": len(buys) / len(pre),
        # arrival speed: slots from birth to the trade we land behind
        "slot_gap": float(ent["slot"] - birth["slot"]),
        # priority fee real traders were paying to get in
        "fee_pre": C.median(sorted(r.get("fee", 0) for r in pre)) / C.LAMPORTS,
    }


def outcomes(virgin, fee, L_in, L_out, H, clip_sol, txc, rule, u=None, d=None):
    """Per-mint (features, causal net) pairs for one rule cell.

    Reimplements the single-mint path of run_rule so the feature vector and the
    P&L come from the SAME entry index. Computing them separately is how a
    screen ends up conditioning on a different bar than it trades on."""
    clip = clip_sol * C.LAMPORTS
    got = []
    for v in virgin.values():
        tape, birth = v["tape"], v["tape"][0]
        ei, ent = A.entry_of(v, L_in)
        if ent is None or not A.covered(v, ent, H):
            continue
        t_ent = O.ts_of(ent, birth)
        tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
        if tok <= 0:
            continue
        idx = [i for i in range(ei, len(tape))
               if O.ts_of(tape[i], birth) - t_ent <= H]
        if not idx:
            continue

        ex = None
        if rule == "hold":
            ex = tape[idx[-1]]
        elif rule == "trail":
            peak = None
            for i in idx:
                out, _, _ = C.sell_fill(tape[i]["vsol"], tape[i]["vtok"],
                                        tok, fee)
                if peak is None or out > peak:
                    peak = out
                elif out <= (1 - u) * peak:
                    ex = A.exit_after(tape, i, tape[i]["slot"], L_out)
                    break
            if ex is None:
                ex = tape[idx[-1]]
        out, _, _ = C.sell_fill(ex["vsol"], ex["vtok"], tok, fee)
        got.append((features(v, ei), (out - clip - txc) / C.LAMPORTS / clip_sol))
    return got


def se(xs):
    n = len(xs)
    if n < 2:
        return float("nan")
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return (var / n) ** 0.5


def sd(xs):
    n = len(xs)
    if n < 2:
        return float("nan")
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def quintiles(got, key, nbin=5, absolute=False):
    """Bin by a feature and report whether the bin means separate.

    The verdict is between-group SD vs mean within-group SE: if the spread of
    the bin means is smaller than the noise on a single bin mean, the feature
    has not sorted anything."""
    vals = [((abs(f[key]) if absolute else f[key]), p) for f, p in got]
    vals.sort(key=lambda t: t[0])
    n = len(vals)
    if n < nbin * 10:
        return None
    bins, edge = [], []
    for b in range(nbin):
        lo, hi = b * n // nbin, (b + 1) * n // nbin
        chunk = [p for _, p in vals[lo:hi]]
        bins.append(chunk)
        edge.append(vals[lo][0])
    means = [sum(c) / len(c) for c in bins]
    ses = [se(c) for c in bins]
    between = sd(means)
    within = sum(ses) / len(ses)
    return {"key": key + ("(abs)" if absolute else ""), "edges": edge,
            "means": means, "ses": ses, "n_per_bin": [len(c) for c in bins],
            "between_sd": between, "mean_within_se": within,
            "separates": bool(between > within),
            "spread": means[-1] - means[0]}


def placebo(got, keys, nseed=20):
    """Null distribution of the separation test itself.

    Shuffling the P&L against the features destroys any real relation while
    keeping the feature marginals, the bin sizes and the fat P&L tail. Whatever
    "separates" under the shuffle is what the test manufactures on its own. One
    draw would not be enough (`a-placebo-needs-a-distribution-not-a-draw`)."""
    import random
    counts, ratios, spreads = [], [], []
    for seed in range(nseed):
        rnd = random.Random(1000 + seed)
        ps = [p for _, p in got]
        rnd.shuffle(ps)
        sham = [(f, ps[i]) for i, (f, _) in enumerate(got)]
        # sp must start at None, NOT 0.0: when every bin mean is negative -- as
        # it is here, where the whole population loses -- a max() seeded at zero
        # can never be beaten and the null degenerates to a constant 0.00%,
        # which would make the measured value look WORSE than its own null.
        c, mx, sp = 0, 0.0, None
        for k in keys:
            r = quintiles(sham, k)
            if not r:
                continue
            c += bool(r["separates"])
            mx = max(mx, r["between_sd"] / r["mean_within_se"])
            m = max(r["means"])
            sp = m if sp is None else max(sp, m)
        if sp is None:
            continue
        counts.append(c); ratios.append(mx); spreads.append(sp)
    ns = len(spreads) or 1
    return {"n_seed": ns, "count_mean": sum(counts) / (len(counts) or 1),
            "count_min": min(counts) if counts else None,
            "count_max": max(counts) if counts else None,
            "max_ratio_mean": sum(ratios) / (len(ratios) or 1),
            "max_ratio_p95": sorted(ratios)[int(.95 * len(ratios))]
            if ratios else None,
            "best_bin_mean": sum(spreads) / ns,
            "best_bin_p95": sorted(spreads)[int(.95 * ns)]}


def wf_screen(got_tr, got_te, keys, nbin=5):
    """Pick the best (feature, quintile) on train, score it on test.

    The bin EDGES come from train too, so the test set contributes nothing to
    the choice -- neither the ranking nor the cutoff."""
    best = None
    for k in keys:
        vals = sorted((f[k], p) for f, p in got_tr)
        n = len(vals)
        if n < nbin * 10:
            continue
        for b in range(nbin):
            lo, hi = b * n // nbin, (b + 1) * n // nbin
            chunk = [p for _, p in vals[lo:hi]]
            m = sum(chunk) / len(chunk)
            edge_lo = vals[lo][0]
            edge_hi = vals[hi - 1][0] if hi < n else float("inf")
            if best is None or m > best[0]:
                best = (m, k, b, edge_lo, edge_hi, len(chunk))
    if best is None:
        return None
    _, k, b, elo, ehi, ntr = best
    kept = [p for f, p in got_te if elo <= f[k] <= ehi]
    all_te = [p for _, p in got_te]
    if len(kept) < 10:
        return None
    return {"key": k, "bin": b, "edge_lo": elo, "edge_hi": ehi,
            "train_mean": best[0], "n_train": ntr,
            "test_mean": sum(kept) / len(kept), "n_test": len(kept),
            "test_ci": O.bootstrap_mean_ci(kept),
            "test_pooled": sum(all_te) / len(all_te), "n_test_all": len(all_te)}


def oracle_selection(got, rates):
    """Ceiling on any screen at a fixed selection rate.

    Rank mints by their REALISED causal net -- perfect foresight over which to
    enter -- and take the top p. No feature-based screen can beat this at the
    same rate, so if this is small the whole filter family is closed. The
    causal exit is unchanged, so this isolates selection from timing."""
    ps = sorted((p for _, p in got), reverse=True)
    n = len(ps)
    out = []
    for r in rates:
        k = max(1, int(r * n))
        top = ps[:k]
        out.append({"rate": r, "k": k, "mean": sum(top) / k,
                    "worst_kept": top[-1]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    ap.add_argument("--json", default="data/pumpselect.json")
    ap.add_argument("--fee-bps", type=int, default=C.FEE_BPS)
    ap.add_argument("--clip", type=float, default=1.0)
    ap.add_argument("--lin", type=int, default=2)
    ap.add_argument("--rule", default="trail")
    ap.add_argument("--q", type=float, default=0.15)
    ap.add_argument("--hor", type=int, default=300)
    args = ap.parse_args()

    rows, metas = O.load(args.tape)
    if not rows:
        sys.exit("no completed windows yet")
    states = C.discover_initial_states(rows)
    virgin, seen, _ = O.build_mints(rows, metas, states)
    fee = args.fee_bps / 1e4
    txc = O.txcost(virgin) * 2
    print("%d trades, %d windows, %d virgin curves"
          % (len(rows), len(metas), len(virgin)))

    got = outcomes(virgin, fee, args.lin, 2, args.hor, args.clip, txc,
                   args.rule, args.q)
    if not got:
        sys.exit("no qualifying mints")
    ps = [p for _, p in got]
    pooled = sum(ps) / len(ps)
    print("\nreference cell: rule=%s q=%.2f H=%ds L_in=%d clip %.2f SOL"
          % (args.rule, args.q, args.hor, args.lin, args.clip))
    print("  n=%d  pooled mean %+.2f%%  sd %.2f%%  p_win %.3f"
          % (len(ps), 100 * pooled, 100 * sd(ps),
             sum(1 for x in ps if x > 0) / len(ps)))

    out = {"n": len(ps), "pooled_mean": pooled, "cell":
           {"rule": args.rule, "q": args.q, "H": args.hor, "clip": args.clip}}

    print("\n=== ceiling on ANY entry screen: perfect foresight over WHICH")
    print("    mints to enter, causal exit unchanged ===")
    print("  %6s %7s %10s %12s" % ("rate", "k", "mean%", "worst kept%"))
    out["oracle_selection"] = oracle_selection(got, [0.02, 0.05, 0.10, 0.20,
                                                     0.50, 1.00])
    for d in out["oracle_selection"]:
        print("  %5.0f%% %7d %9.2f%% %11.2f%%"
              % (100 * d["rate"], d["k"], 100 * d["mean"],
                 100 * d["worst_kept"]))

    print("\n=== do entry-time features separate outcomes at all? ===")
    print("  verdict is between-group SD vs mean within-group SE; a spread")
    print("  smaller than its own noise is not a screen.")
    print("  %-18s %9s %9s %9s %9s %9s %5s"
          % ("feature", "q1%", "q3%", "q5%", "betwSD", "withSE", "sep?"))
    out["quintiles"] = []
    keys = ["birth_sol", "n_trades_pre", "n_wallets_pre", "vsol_entry",
            "flow_pre", "buy_share_pre", "slot_gap", "fee_pre"]
    for k in keys:
        for ab in ((False, True) if k == "flow_pre" else (False,)):
            r = quintiles(got, k, absolute=ab)
            if not r:
                continue
            if ab and all(abs(a - b) < 1e-12 for a, b in
                          zip(r["means"], out["quintiles"][-1]["means"])):
                # pre-entry flow on a fresh curve is positive by construction
                # (the only holder of tokens is the curve, so early trades are
                # buys), so |flow| is flow and the abs binning is degenerate.
                print("  %-18s degenerate: flow_pre >= 0 for every mint, so "
                      "|flow| adds nothing" % "flow_pre(abs)")
                continue
            out["quintiles"].append(r)
            print("  %-18s %8.2f%% %8.2f%% %8.2f%% %8.2f%% %8.2f%% %5s"
                  % (r["key"], 100 * r["means"][0], 100 * r["means"][2],
                     100 * r["means"][4], 100 * r["between_sd"],
                     100 * r["mean_within_se"],
                     "YES" if r["separates"] else "no"))

    print("\n=== is that separation more than the test manufactures? ===")
    pl = placebo(got, keys)
    out["placebo"] = pl
    real_sep = sum(1 for r in out["quintiles"] if r["separates"]
                   and not r["key"].endswith("(abs)"))
    real_ratio = max(r["between_sd"] / r["mean_within_se"]
                     for r in out["quintiles"])
    real_best = max(max(r["means"]) for r in out["quintiles"])
    print("  %-34s %10s %14s" % ("statistic", "measured", "shuffled (20x)"))
    print("  %-34s %10d %8.1f [%d,%d]"
          % ("features that 'separate'", real_sep, pl["count_mean"],
             pl["count_min"], pl["count_max"]))
    print("  %-34s %10.2f %8.2f (p95 %.2f)"
          % ("max betweenSD/withinSE", real_ratio, pl["max_ratio_mean"],
             pl["max_ratio_p95"]))
    print("  %-34s %9.2f%% %8.2f%% (p95 %.2f%%)"
          % ("best single-bin mean", 100 * real_best,
             100 * pl["best_bin_mean"], 100 * pl["best_bin_p95"]))

    print("\n=== walk-forward: feature AND cutoff picked on earlier windows ===")
    tr_v, te_v = A.split_windows(virgin, metas)
    if tr_v is None:
        print("  need >=4 windows; have %d" % len(metas))
        out["wf_screen"] = None
    else:
        gt = outcomes(tr_v, fee, args.lin, 2, args.hor, args.clip, txc,
                      args.rule, args.q)
        ge = outcomes(te_v, fee, args.lin, 2, args.hor, args.clip, txc,
                      args.rule, args.q)
        w = wf_screen(gt, ge, keys)
        out["wf_screen"] = w
        if w is None:
            print("  too few mints to split")
        else:
            print("  picked %s bin %d, range [%.4g, %.4g]"
                  % (w["key"], w["bin"], w["edge_lo"], w["edge_hi"]))
            print("  train %+.2f%% (n=%d)  ->  TEST %+.2f%% "
                  "CI [%+.2f%%, %+.2f%%] (n=%d of %d)"
                  % (100 * w["train_mean"], w["n_train"],
                     100 * w["test_mean"], 100 * w["test_ci"][0],
                     100 * w["test_ci"][1], w["n_test"], w["n_test_all"]))
            print("  test pooled, no screen: %+.2f%%  ->  screen adds %+.2f pp"
                  % (100 * w["test_pooled"],
                     100 * (w["test_mean"] - w["test_pooled"])))

    nsep = sum(1 for r in out["quintiles"] if r["separates"])
    print("\n  %d of %d features separate. Best single-bin mean over all"
          % (nsep, len(out["quintiles"])))
    best = max((max(r["means"]), r["key"]) for r in out["quintiles"])
    print("  features and bins: %+.2f%% (%s) vs pooled %+.2f%%"
          % (100 * best[0], best[1], 100 * pooled))

    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
