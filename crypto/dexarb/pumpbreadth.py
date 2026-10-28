"""Does BUYER INDEPENDENCE sort pump.fun entries where wallet COUNT did not?

Prereg: preregistrations/pumpfun_buyer_independence_20261001.md

The closed screen in FINDINGS_pumpfun.md counted how many wallets bought before
our entry; it never asked who they were. This adds concentration, clip size and
wallet-recurrence features on the identical frame (clip 1 SOL, L_in=2, trail
q=0.15, H=300s), so the numbers are directly comparable to that closure.

Leakage guard: a wallet is "recurring" only if its first appearance anywhere in
the corpus sits at a slot strictly before this mint's birth slot. "Appears in
another window" would read the future -- the corpus spans 80 days both ways.

    python3 pumpbreadth.py
"""
import argparse
import collections
import json
import random
import sys

import pumpcausal as A
import pumpcurve as C
import pumporacle as O
import pumpselect as S

SIGNS = {
    "n_buyers_pre": +1,
    "buyer_hhi_pre": -1,
    "max_buyer_share_pre": -1,
    "dev_share_pre": -1,
    "recurring_buyer_share_pre": +1,
    "recurring_sol_share_pre": +1,
    "median_buy_sol_pre": -1,
}


def first_seen(virgin):
    """Earliest slot at which each wallet appears anywhere in the corpus."""
    seen = {}
    for v in virgin.values():
        for r in v["tape"]:
            slot = r["slot"]
            if r["user"] not in seen or slot < seen[r["user"]]:
                seen[r["user"]] = slot
    return seen


def breadth_features(v, ei, seen):
    tape, birth = v["tape"], v["tape"][0]
    pre = tape[:ei + 1]
    buys = [r for r in pre if r["buy"]]
    if not buys:
        return None
    sol_by_user = collections.defaultdict(float)
    for r in buys:
        sol_by_user[r["user"]] += r["sol"]
    total = sum(sol_by_user.values())
    if total <= 0:
        return None
    shares = [s / total for s in sol_by_user.values()]
    dev = birth["user"]
    recurring = [u for u in sol_by_user if seen.get(u, birth["slot"]) < birth["slot"]]
    clips = sorted(r["sol"] / C.LAMPORTS for r in buys)
    return {
        "n_buyers_pre": float(len(sol_by_user)),
        "buyer_hhi_pre": sum(s * s for s in shares),
        "max_buyer_share_pre": max(shares),
        "dev_share_pre": sol_by_user.get(dev, 0.0) / total,
        "recurring_buyer_share_pre": len(recurring) / len(sol_by_user),
        "recurring_sol_share_pre": sum(sol_by_user[u] for u in recurring) / total,
        "median_buy_sol_pre": C.median(clips),
    }


def rows(virgin, seen, fee, l_in, l_out, horizon, clip_sol, trail):
    """(window, features, net return) per mint, features and P&L at one index."""
    clip = clip_sol * C.LAMPORTS
    out = []
    for (window, _mint), v in virgin.items():
        tape, birth = v["tape"], v["tape"][0]
        ei, ent = A.entry_of(v, l_in)
        if ent is None or not A.covered(v, ent, horizon):
            continue
        feats = breadth_features(v, ei, seen)
        if feats is None:
            continue
        t_ent = O.ts_of(ent, birth)
        tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
        if tok <= 0:
            continue
        idx = [i for i in range(ei, len(tape))
               if O.ts_of(tape[i], birth) - t_ent <= horizon]
        if not idx:
            continue
        txc = ent.get("fee", 0)
        exit_row, peak = None, None
        for i in idx:
            got, _, _ = C.sell_fill(tape[i]["vsol"], tape[i]["vtok"], tok, fee)
            if peak is None or got > peak:
                peak = got
            elif got <= (1 - trail) * peak:
                exit_row = A.exit_after(tape, i, tape[i]["slot"], l_out)
                break
        if exit_row is None:
            exit_row = tape[idx[-1]]
        got, _, _ = C.sell_fill(exit_row["vsol"], exit_row["vtok"], tok, fee)
        out.append((window, feats, (got - clip - txc) / C.LAMPORTS / clip_sol))
    return out


def quintile_table(data, key, nbin=5):
    vals = sorted(((f[key], p) for _, f, p in data), key=lambda t: t[0])
    n = len(vals)
    if n < nbin * 10:
        return None
    bins = [vals[i * n // nbin:(i + 1) * n // nbin] for i in range(nbin)]
    means = [sum(p for _, p in b) / len(b) for b in bins]
    ses = [S.se([p for _, p in b]) for b in bins]
    between = S.sd(means)
    within = sum(ses) / len(ses)
    return {
        "edges": [b[0][0] for b in bins] + [vals[-1][0]],
        "means": means,
        "ses": ses,
        "n_per_bin": [len(b) for b in bins],
        "between_sd": between,
        "mean_within_se": within,
        "separates": bool(between > within),
        "spread_top_minus_bottom": means[-1] - means[0],
    }


def best_bin_on(train, nbin=5):
    """Best (feature, bin) on the training half, by bin mean."""
    best = None
    for key in SIGNS:
        vals = sorted(((f[key], p) for _, f, p in train), key=lambda t: t[0])
        n = len(vals)
        if n < nbin * 10:
            continue
        for b in range(nbin):
            chunk = vals[b * n // nbin:(b + 1) * n // nbin]
            mean = sum(p for _, p in chunk) / len(chunk)
            lo, hi = chunk[0][0], chunk[-1][0]
            if best is None or mean > best["train_mean"]:
                best = {"key": key, "bin": b, "lo": lo, "hi": hi,
                        "train_mean": mean, "n_train": len(chunk)}
    return best


def score_test(test, pick):
    kept = [p for _, f, p in test if pick["lo"] <= f[pick["key"]] <= pick["hi"]]
    return kept


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--json", default="data/pumpbreadth.json")
    parser.add_argument("--fee-bps", type=int, default=C.FEE_BPS)
    parser.add_argument("--clip", type=float, default=1.0)
    parser.add_argument("--lin", type=int, default=2)
    parser.add_argument("--lout", type=int, default=2)
    parser.add_argument("--hor", type=int, default=300)
    parser.add_argument("--trail", type=float, default=0.15)
    parser.add_argument("--null-draws", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20261001)
    args = parser.parse_args()

    raw, metas = O.load(args.tape)
    if not raw:
        sys.exit("no completed windows")
    virgin, _, _ = O.build_mints(raw, metas, C.discover_initial_states(raw))
    seen = first_seen(virgin)
    data = rows(virgin, seen, args.fee_bps / 1e4, args.lin, args.lout,
                args.hor, args.clip, args.trail)
    if not data:
        sys.exit("no eligible mints")

    windows = sorted({w for w, _, _ in data})
    half = len(windows) // 2
    train_w, test_w = set(windows[:half]), set(windows[half:])
    train = [d for d in data if d[0] in train_w]
    test = [d for d in data if d[0] in test_w]

    tables = {key: quintile_table(data, key) for key in SIGNS}
    separating = {k: v for k, v in tables.items() if v and v["separates"]}
    signed = {
        k: (v["spread_top_minus_bottom"] * SIGNS[k] > 0)
        for k, v in separating.items()
    }

    pick = best_bin_on(train)
    kept = score_test(test, pick) if pick else []
    test_pooled = sum(p for _, _, p in test) / len(test)
    test_mean = sum(kept) / len(kept) if kept else None

    rnd = random.Random(args.seed)
    null = []
    for _ in range(args.null_draws):
        values = {k: [f[k] for _, f, _ in train] for k in SIGNS}
        for k in values:
            rnd.shuffle(values[k])
        shuffled = [
            (w, {k: values[k][i] for k in SIGNS}, p)
            for i, (w, _f, p) in enumerate(train)
        ]
        spick = best_bin_on(shuffled)
        skept = score_test(test, spick) if spick else []
        if skept:
            null.append(sum(skept) / len(skept))
    null.sort()
    null_p95 = null[int(0.95 * len(null))] if null else None

    gates = {
        "G1_separation": bool(separating),
        "G2_sign": bool(separating) and any(signed.values()),
        "G3_walk_forward": bool(test_mean is not None and test_mean > 0
                                and test_mean > test_pooled),
        "G4_null": bool(test_mean is not None and null_p95 is not None
                        and test_mean > null_p95),
        "G5_every_bin_reported": all(t is not None for t in tables.values()),
    }
    output = {
        "cell": {"clip_sol": args.clip, "L_in": args.lin, "L_out": args.lout,
                 "horizon_s": args.hor, "trail": args.trail,
                 "fee_bps": args.fee_bps},
        "n_mints": len(data),
        "train_windows": sorted(train_w), "test_windows": sorted(test_w),
        "preregistered_signs": SIGNS,
        "quintiles": tables,
        "separating_features": sorted(separating),
        "separating_with_predicted_sign": signed,
        "selected_on_train": pick,
        "test_mean_of_selection": test_mean,
        "n_test_selected": len(kept),
        "test_pooled_mean": test_pooled,
        "shuffle_null": {"draws": len(null), "p50": null[len(null) // 2] if null else None,
                         "p95": null_p95, "max": null[-1] if null else None},
        "gates": gates,
        "verdict": "SELECTION-REOPENS" if all(gates.values()) else "NO-SELECTION",
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
