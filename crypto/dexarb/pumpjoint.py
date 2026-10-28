"""Bound the JOINT pre-entry feature family on pump.fun, rather than test one combiner.

Prereg: preregistrations/pumpfun_joint_feature_bound_20261001.md

Three arms:
  A  a gradient-boosted combiner fit on train, top decile selected on test;
  B  the same pipeline refit on PERMUTED train labels -- arm A's capacity-matched
     null, which is the only correct benchmark for a selection procedure;
  C  a decision tree fit to the TEST labels, i.e. the supremum any function of
     these features at that depth could reach. If C is negative the family is
     closed at its supremum and no combiner can rescue it.

    python3 pumpjoint.py
"""
import argparse
import json
import sys

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.tree import DecisionTreeRegressor

import pumpbreadth as B
import pumpcausal as A
import pumpcurve as C
import pumporacle as O
import pumpselect as S

KEYS = ["birth_sol", "n_trades_pre", "n_wallets_pre", "vsol_entry", "flow_pre",
        "buy_share_pre", "slot_gap", "fee_pre",
        "n_buyers_pre", "buyer_hhi_pre", "max_buyer_share_pre", "dev_share_pre",
        "recurring_buyer_share_pre", "recurring_sol_share_pre",
        "median_buy_sol_pre"]


def joint_rows(virgin, seen, fee, l_in, l_out, horizon, clip_sol, trail):
    """(window, feature vector, net return) with features and P&L at one index."""
    clip = clip_sol * C.LAMPORTS
    out = []
    for (window, _mint), v in virgin.items():
        tape, birth = v["tape"], v["tape"][0]
        ei, ent = A.entry_of(v, l_in)
        if ent is None or not A.covered(v, ent, horizon):
            continue
        marginal = S.features(v, ei)
        breadth = B.breadth_features(v, ei, seen)
        if breadth is None:
            continue
        feats = dict(marginal, **breadth)
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
        out.append((window, [feats[k] for k in KEYS],
                    (got - clip - txc) / C.LAMPORTS / clip_sol))
    return out


def model(seed):
    return GradientBoostingRegressor(n_estimators=300, max_depth=3,
                                     learning_rate=0.05, random_state=seed)


def top_decile_mean(fitted, x_test, y_test, rate=0.10):
    pred = fitted.predict(x_test)
    k = max(1, int(round(rate * len(y_test))))
    keep = np.argsort(-pred)[:k]
    return float(y_test[keep].mean()), int(k)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--json", default="data/pumpjoint.json")
    parser.add_argument("--clip", type=float, default=1.0)
    parser.add_argument("--lin", type=int, default=2)
    parser.add_argument("--lout", type=int, default=2)
    parser.add_argument("--hor", type=int, default=300)
    parser.add_argument("--trail", type=float, default=0.15)
    parser.add_argument("--rate", type=float, default=0.10)
    parser.add_argument("--null-draws", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261001)
    args = parser.parse_args()

    raw, metas = O.load(args.tape)
    if not raw:
        sys.exit("no completed windows")
    virgin, _, _ = O.build_mints(raw, metas, C.discover_initial_states(raw))
    seen = B.first_seen(virgin)
    data = joint_rows(virgin, seen, C.FEE_BPS / 1e4, args.lin, args.lout,
                      args.hor, args.clip, args.trail)
    if not data:
        sys.exit("no eligible mints")

    windows = sorted({w for w, _, _ in data})
    half = len(windows) // 2
    train_w = set(windows[:half])
    x_tr = np.array([f for w, f, _ in data if w in train_w], dtype=float)
    y_tr = np.array([p for w, _, p in data if w in train_w], dtype=float)
    x_te = np.array([f for w, f, _ in data if w not in train_w], dtype=float)
    y_te = np.array([p for w, _, p in data if w not in train_w], dtype=float)

    # Arm A
    fitted = model(args.seed).fit(x_tr, y_tr)
    a_mean, n_sel = top_decile_mean(fitted, x_te, y_te, args.rate)
    train_fit_mean, _ = top_decile_mean(fitted, x_tr, y_tr, args.rate)

    # Arm B -- capacity-matched null: permute train labels, refit, re-select
    rng = np.random.default_rng(args.seed)
    null = []
    for _ in range(args.null_draws):
        shuffled = rng.permutation(y_tr)
        nm = model(args.seed).fit(x_tr, shuffled)
        m, _ = top_decile_mean(nm, x_te, y_te, args.rate)
        null.append(m)
    null = np.sort(np.array(null))

    # Arm C -- the family bound: fit the TEST labels, report the best leaf
    k_rate = max(1, int(round(args.rate * len(y_te))))
    unrestricted = float(np.sort(y_te)[-k_rate:].mean())
    bound = {}
    for depth in (2, 3, 5, 8):
        tree = DecisionTreeRegressor(max_depth=depth, min_samples_leaf=100,
                                     random_state=args.seed).fit(x_te, y_te)
        leaves = tree.apply(x_te)
        best, best_n = None, None
        for leaf in np.unique(leaves):
            mask = leaves == leaf
            m = float(y_te[mask].mean())
            if best is None or m > best:
                best, best_n = m, int(mask.sum())
        # rate-matched: take leaves by descending mean until `rate` of the mass
        ranked = sorted(((float(y_te[leaves == l].mean()), int((leaves == l).sum()))
                         for l in np.unique(leaves)), reverse=True)
        picked, taken = [], 0
        for mean_l, count in ranked:
            if taken >= k_rate:
                break
            picked.append((mean_l, count))
            taken += count
        rate_matched = sum(m * c for m, c in picked) / taken
        bound["depth_%d" % depth] = {
            "best_leaf_mean": best, "n_in_leaf": best_n,
            "n_leaves": int(len(np.unique(leaves))),
            "rate_matched_mean": rate_matched,
            "rate_matched_n": taken,
        }

    pooled = float(y_te.mean())
    gates = {
        "G1_positive": bool(a_mean > 0),
        "G2_beats_pooled": bool(a_mean > pooled),
        "G3_beats_capacity_null": bool(a_mean > float(null[int(0.95 * len(null))])),
        "G4_family_bound_positive": bool(bound["depth_3"]["best_leaf_mean"] > 0),
    }
    output = {
        "cell": {"clip_sol": args.clip, "L_in": args.lin, "L_out": args.lout,
                 "horizon_s": args.hor, "trail": args.trail,
                 "fee_bps": C.FEE_BPS, "selection_rate": args.rate},
        "features": KEYS,
        "n_mints": len(data), "n_train": int(len(y_tr)), "n_test": int(len(y_te)),
        "train_windows": sorted(train_w),
        "arm_A_combiner": {
            "test_top_decile_mean": a_mean, "n_selected": n_sel,
            "train_top_decile_mean_in_sample": train_fit_mean,
        },
        "arm_B_capacity_matched_null": {
            "draws": int(len(null)), "p50": float(null[len(null) // 2]),
            "p95": float(null[int(0.95 * len(null))]), "max": float(null[-1]),
        },
        "arm_C_family_bound_fit_on_test_labels": bound,
        "unrestricted_oracle_at_rate": unrestricted,
        "test_pooled_mean": pooled,
        "gates": gates,
        "verdict": ("JOINT-SELECTION-WORKS" if all(gates.values())
                    else ("FAMILY-CLOSED-AT-SUPREMUM"
                          if not gates["G4_family_bound_positive"]
                          else "NO-JOINT-SELECTION")),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
