"""Audit whether funder persistence survives creator/edge deduplication.

Preregistration:
    preregistrations/pumpfun_funder_independence_audit_20261004.md
"""
import argparse
import collections
import json
import random

import pumpcurve as C
import pumpdeployer as D
import pumpfundinganalyze as F
import pumpfunderreach as R
import pumporacle as O


def creator_windows(virgin, audit):
    rows = []
    for (window, mint), value in virgin.items():
        first = value["tape"][0]
        birth = audit.get((mint, first["sig"]))
        if birth and birth["status"] in D.VERIFIED_BIRTH:
            rows.append({
                "window": window,
                "mint": mint,
                "slot": birth["create_slot"],
                "creator": birth["creator"],
            })
    return rows


def first_by(rows, key):
    kept = {}
    for row in sorted(rows, key=lambda item: (item["slot"], item["mint"])):
        kept.setdefault(key(row), row)
    return list(kept.values())


def cluster_bootstrap_mean(rows, key, draws=5000, seed=20261004):
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row["roi"])
    clusters = sorted(grouped, key=str)
    if not clusters:
        return None
    rnd = random.Random(seed)
    values = []
    for _ in range(draws):
        sample = []
        for _cluster in clusters:
            sample.extend(grouped[rnd.choice(clusters)])
        values.append(sum(sample) / len(sample))
    values.sort()
    return [
        values[int(0.025 * draws)],
        values[int(0.975 * draws)],
    ]


def equal_funder_mean(rows):
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[row["funder"]].append(row["roi"])
    means = [
        sum(values) / len(values) for values in grouped.values()
    ]
    return sum(means) / len(means) if means else None


def summarize_subset(rows, b_windows, top, bottom, draws):
    seat = R.fixed_seat(rows, b_windows, top, bottom, draws)
    top_rows = [
        row for row in rows
        if row["window"] in b_windows and row["funder"] in top
    ]
    seat["equal_funder_weighted_mean"] = equal_funder_mean(top_rows)
    seat["funder_clustered_ci"] = cluster_bootstrap_mean(
        top_rows, lambda row: row["funder"], draws=draws
    )
    return seat


def concentration(rows, b_windows, top):
    selected = [
        row for row in rows
        if row["window"] in b_windows and row["funder"] in top
    ]
    creators = collections.Counter(row["creator"] for row in selected)
    signatures = collections.Counter(
        row["funding_sig"] for row in selected
    )
    pairs = collections.Counter(
        (row["funder"], row["creator"]) for row in selected
    )
    return {
        "launches": len(selected),
        "funders": len({row["funder"] for row in selected}),
        "creators": len(creators),
        "funding_signatures": len(signatures),
        "funder_creator_pairs": len(pairs),
        "max_launches_per_creator": max(creators.values(), default=0),
        "max_launches_per_funding_signature": max(
            signatures.values(), default=0
        ),
        "max_launches_per_funder_creator_pair": max(
            pairs.values(), default=0
        ),
    }


def selected_only_cache_replay(rows, b_windows, top):
    """Lower bound on stale matches if later nonselected funding is unseen."""
    selected_edges = collections.defaultdict(set)
    for row in rows:
        if row["funder"] in top:
            selected_edges[row["creator"]].add((
                row["funding_slot"],
                row["funding_sig"],
                row["funder"],
            ))
    counts = collections.Counter()
    for row in sorted(rows, key=lambda item: item["slot"]):
        if row["window"] not in b_windows:
            continue
        cached = any(
            funding_slot < row["slot"]
            for funding_slot, _signature, _funder
            in selected_edges[row["creator"]]
        )
        actual = row["funder"] in top
        if cached and actual:
            counts["true_positive"] += 1
        elif cached:
            counts["false_positive"] += 1
        elif actual:
            counts["false_negative"] += 1
        else:
            counts["true_negative"] += 1
    predicted = counts["true_positive"] + counts["false_positive"]
    actual_positive = counts["true_positive"] + counts["false_negative"]
    return {
        **counts,
        "precision": (
            counts["true_positive"] / predicted if predicted else None
        ),
        "recall": (
            counts["true_positive"] / actual_positive
            if actual_positive else None
        ),
        "requires_later_funding_invalidation": bool(
            counts["false_positive"]
        ),
    }


def primary_pass(result):
    top = result.get("top_b") or {}
    top_population = result.get("top_vs_population_ci")
    funder_ci = result.get("funder_clustered_ci")
    return bool(
        top.get("n", 0) >= 30
        and top.get("windows", 0) >= 6
        and top.get("mean_roi", 0) > 0
        and top.get("clustered_ci")
        and top["clustered_ci"][0] > 0
        and top_population
        and top_population[0] > 0
        and funder_ci
        and funder_ci[0] > 0
    )


def run(args):
    raw, metas = O.load(args.tape)
    if not raw:
        raise ValueError("no completed windows")
    virgin, _, _ = O.build_mints(
        raw, metas, C.discover_initial_states(raw)
    )
    audit = F.latest(args.birth_audit, ("mint", "first_sig"))
    funding = F.latest(args.funding, ("mint", "create_sig"))
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2
    a_windows, b_windows = D.split_windows(virgin)
    rows_by_ahead = {
        ahead: R.funded_rows(
            virgin,
            audit,
            funding,
            fee,
            txc,
            args.clip,
            lambda value, n=ahead:
                R.entry_after_up_to_same_slot_trades(value, n),
        )
        for ahead in (0, 1, 2)
    }
    rows = rows_by_ahead[0]
    _state, top, bottom, _scores = R.fixed_groups(
        rows, a_windows, args
    )

    launches = creator_windows(virgin, audit)
    creators_a = {
        row["creator"] for row in launches
        if row["window"] in a_windows
    }
    unseen_b = [
        row for row in rows
        if row["window"] in b_windows
        and row["creator"] not in creators_a
    ]
    seen_b = [
        row for row in rows
        if row["window"] in b_windows
        and row["creator"] in creators_a
    ]
    primary_creator_keys = {
        row["creator"]
        for row in first_by(unseen_b, lambda row: row["creator"])
    }
    primary_by_ahead = {}
    for ahead, delayed_rows in rows_by_ahead.items():
        eligible = [
            row for row in delayed_rows
            if row["window"] in b_windows
            and row["creator"] in primary_creator_keys
        ]
        primary_rows = first_by(
            eligible, lambda row: row["creator"]
        )
        primary_by_ahead[str(ahead)] = summarize_subset(
            primary_rows, b_windows, top, bottom, args.draws
        )
    pair_rows = first_by(
        [row for row in rows if row["window"] in b_windows],
        lambda row: (row["funder"], row["creator"]),
    )
    signature_rows = first_by(
        [row for row in rows if row["window"] in b_windows],
        lambda row: row["funding_sig"],
    )

    primary = primary_by_ahead["0"]
    one_ahead = primary_by_ahead["1"]
    output = {
        "preregistration": (
            "preregistrations/"
            "pumpfun_funder_independence_audit_20261004.md"
        ),
        "parameters": {
            "clip_sol": args.clip,
            "min_a": args.min_a,
            "top_share": args.top_share,
            "draws": args.draws,
        },
        "a_windows": sorted(a_windows),
        "b_windows": sorted(b_windows),
        "selected_top_funders": sorted(top),
        "concentration": concentration(rows, b_windows, top),
        "selected_only_cache_replay": selected_only_cache_replay(
            rows, b_windows, top
        ),
        "primary_unseen_creator_first_launch": primary,
        "primary_unseen_creator_first_launch_by_ahead": (
            primary_by_ahead
        ),
        "secondary": {
            "unseen_creators_all_launches": summarize_subset(
                unseen_b, b_windows, top, bottom, args.draws
            ),
            "seen_creators_all_launches": summarize_subset(
                seen_b, b_windows, top, bottom, args.draws
            ),
            "one_per_funder_creator": summarize_subset(
                pair_rows, b_windows, top, bottom, args.draws
            ),
            "one_per_funding_signature": summarize_subset(
                signature_rows, b_windows, top, bottom, args.draws
            ),
        },
        "verdict": (
            "FUNDER-ROTATION-INDEPENDENT"
            if primary_pass(one_ahead)
            else "FUNDER-ROTATION-NOT-VALIDATED"
        ),
        "capital_enabled": False,
        "transaction_build_path": False,
        "transaction_sign_path": False,
        "transaction_submit_path": False,
    }
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument(
        "--birth-audit", default="data/pumpbirthaudit.jsonl"
    )
    parser.add_argument("--funding", default="data/pumpfunding.jsonl")
    parser.add_argument(
        "--json", default="data/pumpfunderindependence.json"
    )
    parser.add_argument("--clip", type=float, default=0.05)
    parser.add_argument("--min-a", type=int, default=3)
    parser.add_argument("--top-share", type=float, default=0.20)
    parser.add_argument("--draws", type=int, default=5000)
    args = parser.parse_args()
    output = run(args)
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
