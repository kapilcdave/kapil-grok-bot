"""Expanding walk-forward audit for the zero-slot funder selector."""
import argparse
import json

import pumpcurve as C
import pumpdeployer as D
import pumpfundinganalyze as F
import pumpfunderindependence as I
import pumpfunderreach as R
import pumporacle as O


FOLDS = (
    (set(range(0, 4)), set(range(4, 8))),
    (set(range(0, 8)), set(range(8, 12))),
    (set(range(0, 12)), set(range(12, 16))),
)


def fold_result(
    zero_rows,
    delayed_rows,
    launches,
    train_windows,
    test_windows,
    args,
):
    state = D.state_by_deployer(zero_rows, train_windows)
    top, bottom, _scores = D.select(
        state, args.min_a, args.top_share
    )
    creators_seen = {
        row["creator"] for row in launches
        if row["window"] in train_windows
    }
    eligible = [
        row for row in delayed_rows
        if row["window"] in test_windows
        and row["creator"] not in creators_seen
    ]
    first = I.first_by(eligible, lambda row: row["creator"])
    result = I.summarize_subset(
        first, test_windows, set(top), set(bottom), args.draws
    )
    result["train_windows"] = sorted(train_windows)
    result["test_windows"] = sorted(test_windows)
    result["selected_top_funders"] = len(top)
    result["selected_bottom_funders"] = len(bottom)
    result["selected_top_funder_ids"] = sorted(top)
    return result


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
    zero_rows = R.funded_rows(
        virgin,
        audit,
        funding,
        fee,
        txc,
        args.clip,
        lambda value: R.entry_after_up_to_same_slot_trades(value, 0),
    )
    delayed_rows = R.funded_rows(
        virgin,
        audit,
        funding,
        fee,
        txc,
        args.clip,
        lambda value: R.entry_after_up_to_same_slot_trades(value, 1),
    )
    launches = I.creator_windows(virgin, audit)
    folds = [
        fold_result(
            zero_rows,
            delayed_rows,
            launches,
            train,
            test,
            args,
        )
        for train, test in FOLDS
    ]
    powered = [
        fold for fold in folds
        if (fold.get("top_b") or {}).get("n", 0) >= 20
    ]
    positive = all(
        fold["top_b"]["mean_roi"] > 0 for fold in powered
    )
    significant = sum(
        fold["top_b"].get("clustered_ci")
        and fold["top_b"]["clustered_ci"][0] > 0
        for fold in powered
    )
    stable = bool(powered and positive and significant >= 2)
    overlaps = []
    for previous, current in zip(folds, folds[1:]):
        left = set(previous["selected_top_funder_ids"])
        right = set(current["selected_top_funder_ids"])
        overlaps.append({
            "intersection": len(left & right),
            "union": len(left | right),
            "jaccard": len(left & right) / len(left | right),
        })
    output = {
        "preregistration": (
            "preregistrations/pumpfun_funder_walkforward_20261004.md"
        ),
        "parameters": {
            "clip_sol": args.clip,
            "min_a": args.min_a,
            "top_share": args.top_share,
            "entry_transactions_ahead": 1,
        },
        "folds": folds,
        "powered_folds": len(powered),
        "positive_powered_folds": sum(
            fold["top_b"]["mean_roi"] > 0 for fold in powered
        ),
        "significant_powered_folds": significant,
        "adjacent_selector_overlap": overlaps,
        "verdict": (
            "FUNDER-WALKFORWARD-STABLE"
            if stable else "FUNDER-WALKFORWARD-NOT-STABLE"
        ),
        "capital_enabled": False,
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
        "--json", default="data/pumpfunderwalkforward.json"
    )
    parser.add_argument("--clip", type=float, default=0.05)
    parser.add_argument("--min-a", type=int, default=3)
    parser.add_argument("--top-share", type=float, default=0.20)
    parser.add_argument("--draws", type=int, default=5000)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
