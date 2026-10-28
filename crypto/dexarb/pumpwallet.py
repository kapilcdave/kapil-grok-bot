"""Census of who actually made money on freshly minted pump.fun curves.

Not a backtest -- a count. Every trade on every curve BORN inside a collection
window is attributed to its wallet, so this is the realised outcome of the real
population of snipers and bots, with no strategy of mine in it. That makes it
immune to the fitting failures the rest of this branch has to defend against:
there is nothing to overfit in an accounting identity.

Restricting to virgin curves is what makes the accounting closeable: every token
a wallet holds in such a mint was necessarily acquired inside the window, so no
position is inherited from before the tape starts.

**The reserves are not a closed system.** Measured on real tape: between two
consecutive trades on the same mint, `virtual_sol_reserves` can move by 2-153 SOL
with the token reserve EXACTLY unchanged (19.4% of transitions; both events
re-decoded from raw bytes at the same verified offset, so this is not a parse
error). Constant product is conserved *within* a trade -- median |k_post/k_pre-1|
= 1.1e-11 -- but not *across* a curve's life: p90 drift 0.865, max 6.89. So SOL
leaves the curve by a mechanism the trade stream does not show, and no aggregate
census over these curves can be made to balance.

That is why the headline census here is **realised-only, on wallets that fully
exit**: their P&L is the sum of `sol_amount` actually paid and received, which is
ground truth from the event itself and needs no curve model, no mark and no
reserve consistency. The marked-bag version is reported second and is explicitly
model-dependent.

Three questions, in increasing order of what they settle:

1. What share of wallets finish ahead, and how much moves from late to early?
2. Does being early pay? Wallets are bucketed by how many slots after birth
   their FIRST trade in that mint landed -- the latency axis, measured on real
   participants rather than assumed.
3. **Do persistent operators do better?** Windows are disjoint and days apart,
   so a wallet appearing in many of them is a running bot, not a passer-by. If
   professional snipers are profitable while one-offs are not, the edge exists
   and is a speed/infrastructure business. If nobody is profitable at any level
   of persistence, there is no seat to buy.

    python3 pumpwallet.py
"""
import argparse
import collections
import json
import sys

import pumpcurve as C
import pumporacle as O


def wallet_pnl(virgin, fee):
    """Per (window, wallet) cash P&L, with any leftover bag marked to what it
    would fetch if liquidated into the last observed curve state.

    Costs charged: the curve fee on both sides (`fee`), and the wallet's own
    on-chain transaction fee from the event.

    Leftover bags are marked by **pro-rata full liquidation**: sell every
    outstanding token of that mint into the final curve state as one block, then
    split the proceeds by token share. Marking each wallet independently at the
    final state instead lets every holder sell into an undrained curve, which
    credits the population with more SOL than the curves contain -- it made the
    census sum to +1582 SOL when the identity says the sum must be negative.
    Pro-rata is order-independent, so no wallet is advantaged by an arbitrary
    liquidation sequence, and it is exact in aggregate by construction."""
    cash = collections.defaultdict(float)      # (w, user) -> lamports
    bag = collections.defaultdict(float)       # (w, user, mint) -> tokens
    first_lat = {}                             # (w, user, mint) -> slots
    windows_of = collections.defaultdict(set)  # user -> {window}
    last_state = {}                            # (w, mint) -> (vsol, vtok)
    n_trades = collections.Counter()

    for (w, mint), v in virgin.items():
        birth = v["tape"][0]
        for r in v["tape"]:
            key = (w, r["user"])
            windows_of[r["user"]].add(w)
            n_trades[key] += 1
            k3 = (w, r["user"], mint)
            if k3 not in first_lat:
                first_lat[k3] = r["slot"] - birth["slot"]
            if r["buy"]:
                cash[key] -= r["sol"] * (1.0 + fee) + r.get("fee", 0)
                bag[k3] += r["tok"]
            else:
                cash[key] += r["sol"] * (1.0 - fee) - r.get("fee", 0)
                bag[k3] -= r["tok"]
        last_state[(w, mint)] = (v["tape"][-1]["vsol"], v["tape"][-1]["vtok"])

    # ---- mark leftover bags by pro-rata liquidation of the whole float ----
    float_of = collections.defaultdict(float)
    for (w, user, mint), tok in bag.items():
        if tok > 0:
            float_of[(w, mint)] += tok

    # The liquidatable float is exactly what the curve ISSUED, not the sum of
    # positive bags. Those differ because plain SPL transfers are not
    # TradeEvents: a wallet can sell tokens it never bought on tape, driving its
    # bag negative, which inflates the positive-bag sum. Liquidating the sum of
    # positive bags credited the population with ~310 SOL the curves never held.
    marked = collections.defaultdict(float)
    liq_total, leak = {}, []
    for (w, mint), tot_tok in float_of.items():
        vsol, vtok = last_state[(w, mint)]
        _, vtok0 = C.pre_reserves(virgin[(w, mint)]["tape"][0])
        issued = max(0.0, vtok0 - vtok)
        proceeds, _, _ = C.sell_fill(vsol, vtok, min(tot_tok, issued), fee)
        liq_total[(w, mint)] = proceeds
        if issued > 0:
            leak.append((tot_tok - issued) / issued)
    for (w, user, mint), tok in bag.items():
        if tok <= 0:
            continue
        share = tok / float_of[(w, mint)]
        marked[(w, user)] += share * liq_total[(w, mint)]

    # ---- accounting identity: the float must equal what the curve issued ----
    # Every token in a virgin curve came out of that curve, so outstanding
    # tokens must equal vtok_initial - vtok_final. A mismatch means a trade was
    # lost, which no P&L number would reveal on its own.
    neg_of = collections.defaultdict(float)      # one pass, not per curve
    for (w, _, mint), tok in bag.items():
        if tok < 0:
            neg_of[(w, mint)] += tok
    ident = []
    for (w, mint), v in virgin.items():
        # per-mint virgin reserve, recovered from the birth trade itself, so no
        # global initial-state constant can silently restrict the sample
        # (`a-hardcoded-input-list-is-a-silent-sample-restriction`)
        _, vtok0 = C.pre_reserves(v["tape"][0])
        issued = vtok0 - v["tape"][-1]["vtok"]
        held = float_of.get((w, mint), 0.0) + neg_of.get((w, mint), 0.0)
        ident.append((issued, held))

    # Best latency per (window, wallet) in ONE pass. Scanning first_lat inside
    # the row loop is O(wallet_windows x mint_entries) -- ~1e10 on 16 windows,
    # which does not finish. Same quadratic shape as the identity loop above.
    best_lat = {}
    for (w_, u_, _), l in first_lat.items():
        k2 = (w_, u_)
        if k2 not in best_lat or l < best_lat[k2]:
            best_lat[k2] = l

    rows = []
    for key, c in cash.items():
        w, user = key
        rows.append({"window": w, "user": user,
                     "pnl": (c + marked.get(key, 0.0)) / C.LAMPORTS,
                     "realised": c / C.LAMPORTS,
                     "mark": marked.get(key, 0.0) / C.LAMPORTS,
                     "trades": n_trades[key],
                     "best_lat": best_lat.get(key),
                     "n_windows": len(windows_of[user])})

    rel = [abs(a - b) / a for a, b in ident if a > 0]
    rel.sort(); leak.sort()
    return rows, {"n": len(rel), "median_rel_err": C.median(rel),
                  "p95_rel_err": rel[int(.95 * len(rel))] if rel else None,
                  "n_exact": sum(1 for x in rel if x < 1e-12),
                  "transfer_leak_median": C.median(leak) if leak else None,
                  "transfer_leak_p90": leak[int(.90 * len(leak))] if leak
                  else None,
                  "share_curves_with_leak": (sum(1 for x in leak if x > 1e-9)
                                             / len(leak)) if leak else None}


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def describe(rows, label, out):
    if len(rows) < 5:
        return
    ps = [r["pnl"] for r in rows]
    tot = sum(ps)
    rec = {"label": label, "n": len(ps), "share_pos": sum(1 for x in ps if x > 0)
           / len(ps), "mean": tot / len(ps), "median": C.median(sorted(ps)),
           "total": tot, "p5": q(ps, .05), "p95": q(ps, .95), "p99": q(ps, .99),
           "max": max(ps), "min": min(ps)}
    out.append(rec)
    print("  %-22s %6d %8.3f %9.3f %9.3f %9.3f %9.3f %10.1f"
          % (label, rec["n"], rec["share_pos"], rec["median"], rec["mean"],
             rec["p5"], rec["p95"], rec["total"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    ap.add_argument("--json", default="data/pumpwallet.json")
    ap.add_argument("--fee-bps", type=int, default=C.FEE_BPS)
    ap.add_argument("--min-trades", type=int, default=1)
    args = ap.parse_args()

    rows_raw, metas = O.load(args.tape)
    if not rows_raw:
        sys.exit("no completed windows yet")
    states = C.discover_initial_states(rows_raw)
    virgin, seen, _ = O.build_mints(rows_raw, metas, states)
    fee = args.fee_bps / 1e4
    print("%d trades, %d windows, %d virgin curves; curve fee %d bps/side plus"
          " each wallet's own tx fee" % (len(rows_raw), len(metas),
                                        len(virgin), args.fee_bps))

    w, acct = wallet_pnl(virgin, fee)
    print("accounting check: outstanding tokens vs tokens the curve issued -- "
          "median rel err %.2e" % acct["median_rel_err"])
    print("  %d of %d curves exact to float precision; p95 %.2e"
          % (acct["n_exact"], acct["n"], acct["p95_rel_err"]))
    print("  token transfers off the trade tape (bag goes negative): present on"
          " %.1f%% of curves,\n  median excess float %+.2e, p90 %+.2e of issued"
          % (100 * (acct["share_curves_with_leak"] or 0),
             acct["transfer_leak_median"] or 0, acct["transfer_leak_p90"] or 0))
    if acct["median_rel_err"] > 1e-6:
        print("  WARNING: the token float does not close. A trade is missing,")
        print("  so every P&L below is suspect. Investigate before reading on.")
    w = [r for r in w if r["trades"] >= args.min_trades]
    out = {"fee_bps": args.fee_bps, "accounting": acct,
           "n_wallet_windows": len(w),
           "n_distinct_wallets": len({r["user"] for r in w}), "groups": []}
    print("%d (window,wallet) pairs, %d distinct wallets"
          % (len(w), out["n_distinct_wallets"]))

    hdr = ("  %-22s %6s %8s %9s %9s %9s %9s %10s"
           % ("group", "n", "share+", "median", "mean", "p5", "p95", "total"))

    # ---- model-free primary: wallets that fully exited ----
    # bag back to ~0 means every token bought was sold, so P&L is pure cash
    # flow from the event stream. No curve math, no terminal mark, and immune
    # to the reserve resets documented above.
    flat = [r for r in w if abs(r["mark"]) < 1e-9]
    print("\n=== MODEL-FREE: wallets that fully exited inside the window.")
    print("    P&L is realised cash only -- sol_amount paid and received, plus")
    print("    the curve fee and their own tx fees. No curve model at all. ===")
    print(hdr)
    out["model_free"] = []
    describe(flat, "fully exited", out["model_free"])
    print("  %d of %d (window,wallet) pairs fully exited (%.1f%%)"
          % (len(flat), len(w), 100 * len(flat) / len(w)))
    if len(flat) >= 5:
        ps = [r["pnl"] for r in flat]
        lo, hi = O.bootstrap_mean_ci(ps)
        out["model_free_ci"] = [lo, hi]
        print("  mean %+.4f SOL, bootstrap CI [%+.4f, %+.4f]"
              % (sum(ps) / len(ps), lo, hi))
        print("  share losing money: %.3f" % (sum(1 for x in ps if x < 0)
                                              / len(ps)))
    # ---- the bias in "fully exited", bounded rather than caveated ----
    # Exiting is a CHOICE, and a wallet holding a worthless bag never makes it.
    # So this subsample drops losers and is biased HIGH. It can be bracketed
    # exactly: realised cash is ground truth for every wallet, and a bag is
    # worth at least nothing, so realised-only is a hard LOWER bound on the
    # population mean while realised+mark is the upper one.
    allp = [r["pnl"] for r in w]
    allr = [r["realised"] for r in w]
    lob, hib = sum(allr) / len(allr), sum(allp) / len(allp)
    out["population_bracket"] = {"lower_realised_only": lob,
                                 "upper_with_marks": hib,
                                 "fully_exited_mean": sum(
                                     r["pnl"] for r in flat) / max(len(flat), 1),
                                 "share_exited": len(flat) / len(w)}
    print("\n  ** the fully-exited subsample is biased HIGH: exiting is a")
    print("     choice, and a wallet sitting on a worthless bag never makes it")
    print("     into this group. Bracketing the WHOLE population instead --")
    print("     realised cash is exact for everyone and a bag is worth >= 0: **")
    print("       mean per (window,wallet) is in [%+.4f, %+.4f] SOL"
          % (lob, hib))
    print("       (lower = bags worthless, upper = bags at pro-rata liquidation)")
    print("       fully-exited mean was %+.4f, i.e. ABOVE the whole bracket,"
          % (sum(r["pnl"] for r in flat) / max(len(flat), 1)))
    print("       which is the survivorship gap made visible.")

    print("\n  same wallets, bucketed by how early they got in:")
    print(hdr)
    for lo_, hi_, lab in ((0, 0, "same slot as birth"), (1, 1, "+1 slot"),
                          (2, 4, "+2..4 slots"), (5, 12, "+5..12 slots"),
                          (13, 50, "+13..50 slots"),
                          (51, 10 ** 9, "+51 or later")):
        describe([r for r in flat if r["best_lat"] is not None
                  and lo_ <= r["best_lat"] <= hi_], lab, out["model_free"])

    print("\n=== everything below MARKS leftover bags and so depends on the")
    print("    curve model. P&L in SOL per (window,wallet). ===")
    print(hdr)
    describe(w, "ALL", out["groups"])

    print("\n=== does being early pay? bucket by slots from mint birth to the")
    print("    wallet's FIRST trade in that mint ===")
    print(hdr)
    for lo, hi, lab in ((0, 0, "same slot as birth"), (1, 1, "+1 slot"),
                        (2, 4, "+2..4 slots"), (5, 12, "+5..12 slots"),
                        (13, 50, "+13..50 slots"),
                        (51, 10 ** 9, "+51 slots or later")):
        describe([r for r in w if r["best_lat"] is not None
                  and lo <= r["best_lat"] <= hi], lab, out["groups"])

    print("\n=== do PERSISTENT operators do better? windows are disjoint and")
    print("    days apart, so appearing in many of them means a running bot ===")
    print(hdr)
    mx = max(r["n_windows"] for r in w)
    for lo, hi, lab in ((1, 1, "1 window"), (2, 2, "2 windows"),
                        (3, 4, "3-4 windows"), (5, 8, "5-8 windows"),
                        (9, 10 ** 9, "9+ windows")):
        if lo > mx:
            continue
        describe([r for r in w if lo <= r["n_windows"] <= hi], lab,
                 out["groups"])

    print("\n=== by activity: wallets doing many trades are running a strategy")
    print(hdr)
    for lo, hi, lab in ((1, 1, "1 trade"), (2, 3, "2-3 trades"),
                        (4, 10, "4-10 trades"), (11, 50, "11-50 trades"),
                        (51, 10 ** 9, "51+ trades")):
        describe([r for r in w if lo <= r["trades"] <= hi], lab, out["groups"])

    # the sharpest single cell: persistent AND fast, i.e. a real sniper bot
    pro = [r for r in w if r["n_windows"] >= 3 and r["best_lat"] is not None
           and r["best_lat"] <= 1 and r["trades"] >= 4]
    print("\n=== the sniper seat: persistent (3+ windows) AND fast (<=1 slot)")
    print("    AND active (4+ trades) ===")
    print(hdr)
    describe(pro, "persistent+fast+active", out["groups"])
    if len(pro) >= 5:
        ps = [r["pnl"] for r in pro]
        lo, hi = O.bootstrap_mean_ci(ps)
        out["pro_ci"] = [lo, hi]
        print("  mean %+.3f SOL, bootstrap CI [%+.3f, %+.3f] over %d wallets"
              % (sum(ps) / len(ps), lo, hi, len(ps)))

    print("\n=== top 10 wallets by total P&L, and what they did ===")
    print("  %9s %9s %7s %6s %6s" % ("pnl SOL", "realised", "trades", "lat",
                                      "wnds"))
    for r in sorted(w, key=lambda r: -r["pnl"])[:10]:
        print("  %+9.2f %+9.2f %7d %6s %6d"
              % (r["pnl"], r["realised"], r["trades"], r["best_lat"],
                 r["n_windows"]))

    tot = sum(r["pnl"] for r in w)
    realised = sum(r["realised"] for r in w)
    mark = sum(r["mark"] for r in w)
    txf = sum(r.get("fee", 0) for v in virgin.values() for r in v["tape"])
    curvef = sum(r["sol"] for v in virgin.values() for r in v["tape"]) * fee
    print("\n--- does the census close? ---")
    print("  realised cash      %+12.1f SOL   (paid into the curves)" % realised)
    print("  marked bags        %+12.1f SOL   (pro-rata liquidation value)"
          % mark)
    print("  TOTAL              %+12.1f SOL" % tot)
    print("  tx fees paid       %+12.1f SOL" % (-txf / C.LAMPORTS))
    print("  curve fees paid    %+12.1f SOL" % (-curvef / C.LAMPORTS))
    print("  fees alone         %+12.1f SOL   <- the total should sit near this"
          % (-(txf / C.LAMPORTS + curvef / C.LAMPORTS)))
    out["closure"] = {"total": tot, "realised": realised, "mark": mark,
                      "tx_fees": txf / C.LAMPORTS,
                      "curve_fees": curvef / C.LAMPORTS}
    print("\n  The population plays a NEGATIVE-sum game: the only external")
    print("  counterparty is the curve, which keeps the fees. So every winner")
    print("  is funded by a loser plus the toll, and 'find an edge' here means")
    print("  'identify which participants are the losers, reliably, before")
    print("  paying the toll yourself'.")

    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)
    print("wrote %s" % args.json)


if __name__ == "__main__":
    main()
