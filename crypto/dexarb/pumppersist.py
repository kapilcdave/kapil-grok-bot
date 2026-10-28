"""Out-of-sample persistence of pump.fun wallet profitability.

Prereg: preregistrations/pumpfun_wallet_persistence_20261001.md

The question the leaderboards raise: are the wallets that made money in one
period the wallets that make money in a later, disjoint period? Selection uses
the earlier half of the archived windows only; the later half is scored once.

This test writes NO new accounting. Records come from pumpsameslot.wallet_mint_rows,
which already charges the 95 bps curve fee on both sides and the wallet's own
transaction fee, so `roi` is realised cash over cash spent.

    python3 pumppersist.py
"""
import argparse
import collections
import json
import random
import sys

import pumpcurve as C
import pumporacle as O
import pumpsameslot as S

SLOT_SEAT = ("birth_transaction", "same_slot_later_transaction")


def split_windows(virgin):
    """Windows ordered by the earliest birth slot in each; earlier half is A."""
    first = {}
    for (window, _mint), v in virgin.items():
        slot = v["tape"][0]["slot"]
        first[window] = min(first.get(window, slot), slot)
    order = sorted(first, key=lambda w: first[w])
    half = len(order) // 2
    return set(order[:half]), set(order[half:])


def per_wallet(rows, a_windows):
    """(a_rows, b_rows) per wallet."""
    state = collections.defaultdict(lambda: {"a": [], "b": []})
    for row in rows:
        side = "a" if row["window"] in a_windows else "b"
        state[row["user"]][side].append(row)
    return state


def select(state, min_a, top_share):
    """Top `top_share` of wallets with >= min_a A-records, by mean A-ROI."""
    eligible = [(u, v) for u, v in state.items() if len(v["a"]) >= min_a]
    scored = sorted(
        eligible,
        key=lambda kv: sum(r["roi"] for r in kv[1]["a"]) / len(kv[1]["a"]),
        reverse=True,
    )
    take = max(1, int(round(top_share * len(scored))))
    chosen = {u for u, _ in scored[:take]}
    return chosen, {u for u, _ in scored}


def b_rows(state, users):
    out = []
    for user in users:
        out.extend(state[user]["b"])
    return out


def clustered_mean_ci(rows, field="roi", draws=2000, seed=20261001):
    return S.clustered_means(rows, field, draws=draws, seed=seed)


def clustered_contrast(left, right, draws=2000, seed=20261001):
    return S.clustered_contrast(left, right, field="roi", draws=draws, seed=seed)


def describe(rows):
    if not rows:
        return None
    out = S.describe(rows)
    out["roi_ci"] = clustered_mean_ci(rows)
    return out


def shuffle_null(state, population_b, min_a, top_share, draws, seed):
    """Permute A-ROI labels among eligible wallets, re-select, re-score B."""
    eligible = [(u, v) for u, v in state.items() if len(v["a"]) >= min_a]
    labels = [sum(r["roi"] for r in v["a"]) / len(v["a"]) for _, v in eligible]
    users = [u for u, _ in eligible]
    take = max(1, int(round(top_share * len(users))))
    pop_mean = sum(r["roi"] for r in population_b) / len(population_b)
    rnd = random.Random(seed)
    values = []
    for _ in range(draws):
        rnd.shuffle(labels)
        ranked = sorted(zip(users, labels), key=lambda kv: kv[1], reverse=True)
        picked = b_rows(state, [u for u, _ in ranked[:take]])
        if not picked:
            continue
        values.append(sum(r["roi"] for r in picked) / len(picked) - pop_mean)
    values.sort()
    return {
        "draws": len(values),
        "p50": values[len(values) // 2],
        "p95": values[int(0.95 * len(values))],
        "max": values[-1],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--json", default="data/pumppersist.json")
    parser.add_argument("--min-a", type=int, default=5)
    parser.add_argument("--top-share", type=float, default=0.10)
    parser.add_argument("--draws", type=int, default=2000)
    parser.add_argument("--null-draws", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20261001)
    args = parser.parse_args()

    raw, metas = O.load(args.tape)
    if not raw:
        sys.exit("no completed windows")
    states = C.discover_initial_states(raw)
    virgin, _, _ = O.build_mints(raw, metas, states)
    rows = S.wallet_mint_rows(virgin, C.FEE_BPS / 1e4)
    a_windows, b_windows = split_windows(virgin)
    state = per_wallet(rows, a_windows)

    population_b = [r for r in rows if r["window"] in b_windows]
    leaders, eligible = select(state, args.min_a, args.top_share)
    leader_b = b_rows(state, leaders)
    rest_b = b_rows(state, eligible - leaders)

    strata = {}
    for label, min_a, share in (
        ("primary_top_decile_min_a_5", args.min_a, args.top_share),
        ("top_quintile_min_a_5", args.min_a, 0.20),
        ("top_decile_min_a_3", 3, args.top_share),
        ("top_decile_min_a_10", 10, args.top_share),
    ):
        chosen, pool = select(state, min_a, share)
        scored = b_rows(state, chosen)
        strata[label] = {
            "n_eligible_wallets": len(pool),
            "n_selected_wallets": len(chosen),
            "b": describe(scored),
            "contrast_vs_population_ci": (
                clustered_contrast(scored, population_b, draws=args.draws)
                if scored else None
            ),
        }

    by_group = collections.defaultdict(float)
    for row in leader_b:
        by_group[row["group"]] += row["pnl_sol"]
    total_positive = sum(v for v in by_group.values() if v > 0)
    slot_share = (
        sum(max(0.0, by_group[g]) for g in SLOT_SEAT) / total_positive
        if total_positive > 0 else None
    )

    primary = strata["primary_top_decile_min_a_5"]["b"]
    vs_pop = strata["primary_top_decile_min_a_5"]["contrast_vs_population_ci"]
    vs_rest = clustered_contrast(leader_b, rest_b, draws=args.draws) if rest_b else None
    null = shuffle_null(state, population_b, args.min_a, args.top_share,
                        args.null_draws, args.seed)
    observed_contrast = (
        primary["mean_roi"] - sum(r["roi"] for r in population_b) / len(population_b)
    )

    gates = {
        "G1_power": bool(primary and primary["n"] >= 200 and primary["windows"] >= 6),
        "G2_sign": bool(primary and primary["mean_roi"] > 0 and primary["roi_ci"][0] > 0),
        "G3_attributable": bool(vs_pop and vs_pop[0] > 0),
        "G4_neighbours": all(
            strata[k]["b"] and strata[k]["b"]["mean_roi"] > 0
            for k in strata if k != "primary_top_decile_min_a_5"
        ),
        "G5_null": bool(observed_contrast > null["p95"]),
        "G6_magnitude": bool(vs_pop and vs_pop[0] > 0.0032),
    }
    falsifier_fired = bool(slot_share is not None and slot_share >= 0.60)

    if not all(gates.values()):
        verdict = "NO-PERSISTENCE"
    elif falsifier_fired:
        verdict = "PERSISTENCE-IS-THE-SLOT-SEAT"
    else:
        verdict = "COPYABLE-LEADERS-EXIST"

    output = {
        "fee_bps": C.FEE_BPS,
        "seed": args.seed,
        "a_windows": sorted(a_windows),
        "b_windows": sorted(b_windows),
        "n_records": len(rows),
        "n_wallets": len(state),
        "n_population_b_records": len(population_b),
        "population_b_mean_roi": sum(r["roi"] for r in population_b) / len(population_b),
        "strata": strata,
        "leaders_vs_unselected_contrast_ci": vs_rest,
        "observed_contrast_vs_population": observed_contrast,
        "shuffle_null": null,
        "leaders_b_pnl_by_entry_group": dict(sorted(by_group.items())),
        "leaders_slot_seat_share_of_positive_pnl": slot_share,
        "gates": gates,
        "falsifier_fired": falsifier_fired,
        "verdict": verdict,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
