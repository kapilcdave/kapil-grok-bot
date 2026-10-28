"""Merge independently collected funding shards into one complete corpus.

The canonical output is written only when every CreateEvent-verified birth has
one clean terminal funding result.  A partial merge therefore cannot be
mistaken for a completed research corpus.
"""
import argparse
import glob
import json

import pumpfunding as F

TERMINAL = {"direct_funder_found", "no_direct_funder_in_history"}


def row_rank(row):
    if row.get("error"):
        return 0
    if row.get("status") in TERMINAL:
        return 3
    if row.get("status") == "history_loaded":
        return 2
    return 1


def merged_latest(paths):
    rows = {}
    for path in paths:
        with open(path) as handle:
            for line in handle:
                row = json.loads(line)
                key = (row["mint"], row["create_sig"])
                old = rows.get(key)
                if old is None or row_rank(row) >= row_rank(old):
                    rows[key] = row
    return rows


def merge(paths, birth_audit):
    births = F.births(birth_audit, None, 20261002)
    latest = merged_latest(paths)
    expected = [(row["mint"], row["create_sig"]) for row in births]
    missing = [key for key in expected if key not in latest]
    errors = [
        key for key in expected
        if key in latest and latest[key].get("error")
    ]
    pending = [
        key for key in expected
        if key in latest
        and not latest[key].get("error")
        and latest[key].get("status") not in TERMINAL
    ]
    selected = [
        latest[key] for key in expected
        if key in latest
    ]
    return selected, {
        "expected_births": len(expected),
        "rows_present": len(selected),
        "missing": len(missing),
        "errors": len(errors),
        "pending": len(pending),
        "complete": not missing and not errors and not pending,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inputs", default="data/pumpfunding2_shard*.jsonl"
    )
    parser.add_argument(
        "--birth-audit", default="data/pumpbirthaudit.jsonl"
    )
    parser.add_argument("--output", default="data/pumpfunding.jsonl")
    parser.add_argument(
        "--summary", default="data/pumpfunding_summary.json"
    )
    args = parser.parse_args()

    paths = sorted(glob.glob(args.inputs))
    if not paths:
        raise SystemExit("no funding shard files matched")
    rows, coverage = merge(paths, args.birth_audit)
    diagnostic = {"input_files": paths, "coverage": coverage}
    if not coverage["complete"]:
        print(json.dumps(diagnostic, indent=2, sort_keys=True))
        raise SystemExit("funding shards are incomplete; canonical output unchanged")

    with open(args.output, "w") as handle:
        handle.writelines(
            json.dumps(row, sort_keys=True) + "\n" for row in rows
        )
    result = {
        **diagnostic,
        "rpc_url": "mixed-configured-and-public",
        "sample_requested": None,
        "history_limit": 50,
        "batch_size": 1,
        "seed": 20261002,
        "summary": F.summarize(rows),
    }
    with open(args.summary, "w") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
