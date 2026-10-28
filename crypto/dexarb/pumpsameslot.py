"""Split birth-transaction wallets from later transactions in the birth slot."""

import argparse
import collections
import json
import random
import sys

import pumpcurve as C
import pumporacle as O


def classify(birth, first):
    if first["sig"] == birth["sig"]:
        return "birth_transaction"
    gap = first["slot"] - birth["slot"]
    if gap == 0:
        return "same_slot_later_transaction"
    if gap == 1:
        return "plus_1_slot"
    if 2 <= gap <= 4:
        return "plus_2_4_slots"
    if 5 <= gap <= 12:
        return "plus_5_12_slots"
    if 13 <= gap <= 50:
        return "plus_13_50_slots"
    return "plus_51_slots_or_later"


def wallet_mint_rows(virgin, fee):
    result = []
    for (window, mint), v in virgin.items():
        birth = v["tape"][0]
        state = {}
        charged = set()
        for order, row in enumerate(v["tape"]):
            user = row["user"]
            if user not in state:
                state[user] = {
                    "first": row,
                    "first_order": order,
                    "cash": 0.0,
                    "bag": 0.0,
                    "bought": 0.0,
                    "spent": 0.0,
                    "trades": 0,
                }
            record = state[user]
            signature_key = (user, row.get("sig"))
            tx_fee = 0.0
            if signature_key not in charged:
                tx_fee = row.get("fee", 0)
                charged.add(signature_key)
            record["trades"] += 1
            if row["buy"]:
                paid = row["sol"] * (1.0 + fee) + tx_fee
                record["cash"] -= paid
                record["spent"] += paid
                record["bag"] += row["tok"]
                record["bought"] += row["tok"]
            else:
                record["cash"] += row["sol"] * (1.0 - fee) - tx_fee
                record["bag"] -= row["tok"]

        for user, record in state.items():
            if not record["first"]["buy"] or record["spent"] <= 0:
                continue
            tolerance = max(1.0, record["bought"] * 1e-9)
            if abs(record["bag"]) > tolerance:
                continue
            pnl_sol = record["cash"] / C.LAMPORTS
            result.append({
                "window": window,
                "mint": mint,
                "user": user,
                "group": classify(birth, record["first"]),
                "pnl_sol": pnl_sol,
                "roi": record["cash"] / record["spent"],
                "spent_sol": record["spent"] / C.LAMPORTS,
                "trades": record["trades"],
                "first_order": record["first_order"],
            })
    return result


def clustered_means(rows, field, draws=2000, seed=20260929):
    by_window = collections.defaultdict(list)
    for row in rows:
        by_window[row["window"]].append(row[field])
    windows = sorted(by_window)
    rnd = random.Random(seed)
    means = []
    for _ in range(draws):
        sample = []
        for _ in windows:
            sample.extend(by_window[rnd.choice(windows)])
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * draws)], means[int(0.975 * draws)]


def clustered_contrast(left, right, field="roi", draws=2000, seed=20260929):
    left_by = collections.defaultdict(list)
    right_by = collections.defaultdict(list)
    for row in left:
        left_by[row["window"]].append(row[field])
    for row in right:
        right_by[row["window"]].append(row[field])
    windows = sorted(set(left_by) & set(right_by))
    rnd = random.Random(seed)
    values = []
    for _ in range(draws):
        sampled_left = []
        sampled_right = []
        for _ in windows:
            window = rnd.choice(windows)
            sampled_left.extend(left_by[window])
            sampled_right.extend(right_by[window])
        values.append(sum(sampled_left) / len(sampled_left)
                      - sum(sampled_right) / len(sampled_right))
    values.sort()
    return values[int(0.025 * draws)], values[int(0.975 * draws)]


def describe(rows):
    if not rows:
        return None
    roi = sorted(row["roi"] for row in rows)
    pnl = [row["pnl_sol"] for row in rows]
    trim = max(1, len(roi) // 100)
    trimmed = roi[trim:-trim] if len(roi) > 2 * trim else roi
    return {
        "n": len(rows),
        "windows": len({row["window"] for row in rows}),
        "mean_roi": sum(roi) / len(roi),
        "median_roi": C.median(roi),
        "trimmed_mean_roi": sum(trimmed) / len(trimmed),
        "roi_ci": clustered_means(rows, "roi"),
        "mean_pnl_sol": sum(pnl) / len(pnl),
        "pnl_ci": clustered_means(rows, "pnl_sol"),
        "total_pnl_sol": sum(pnl),
        "share_positive": sum(value > 0 for value in pnl) / len(pnl),
        "median_spent_sol": C.median(sorted(row["spent_sol"] for row in rows)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--json", default="data/pumpsameslot.json")
    parser.add_argument("--draws", type=int, default=2000)
    args = parser.parse_args()

    raw, metas = O.load(args.tape)
    if not raw:
        sys.exit("no completed windows")
    states = C.discover_initial_states(raw)
    virgin, _, _ = O.build_mints(raw, metas, states)
    rows = wallet_mint_rows(virgin, C.FEE_BPS / 1e4)
    groups = {}
    for group in sorted({row["group"] for row in rows}):
        groups[group] = describe([row for row in rows if row["group"] == group])

    same = [row for row in rows if row["group"] == "same_slot_later_transaction"]
    plus1 = [row for row in rows if row["group"] == "plus_1_slot"]
    contrast = clustered_contrast(same, plus1, draws=args.draws) if same and plus1 else None
    primary = groups.get("same_slot_later_transaction")
    passed = bool(primary and primary["n"] >= 200 and primary["windows"] >= 8
                  and primary["mean_roi"] > 0 and primary["roi_ci"][0] > 0
                  and primary["trimmed_mean_roi"] > 0 and contrast
                  and contrast[0] > 0)
    output = {
        "fee_bps": C.FEE_BPS,
        "n_completed_windows": len(metas),
        "n_virgin_mints": len(virgin),
        "n_fully_exited_buy_first_wallet_mints": len(rows),
        "groups": groups,
        "same_slot_vs_plus_1_roi_contrast_ci": contrast,
        "passed": passed,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
