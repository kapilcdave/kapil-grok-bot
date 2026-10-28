"""Summarize whether live funder enrichment meets the entry deadline."""
import argparse
import glob
import json


def quantile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


def summarize(rows, min_births=100, min_completion_share=0.95):
    timed = [
        row for row in rows
        if isinstance(row.get("funding_lookup_seconds"), (int, float))
    ]
    completed = [
        row for row in timed
        if row.get("decision") not in {
            "reject_funding_lookup_error",
            "reject_funding_lookup_deadline",
        }
        and row["funding_lookup_seconds"]
        <= row.get("funding_lookup_deadline_seconds", 2.0)
    ]
    durations = [row["funding_lookup_seconds"] for row in timed]
    completion_share = len(completed) / len(timed) if timed else None
    power = len(timed) >= min_births
    reliability = bool(
        completion_share is not None
        and completion_share >= min_completion_share
    )
    return {
        "n_births": len(timed),
        "completed_under_deadline": len(completed),
        "completion_share": completion_share,
        "lookup_seconds": {
            "p50": quantile(durations, 0.50),
            "p95": quantile(durations, 0.95),
            "max": max(durations) if durations else None,
        },
        "parameters": {
            "min_births": min_births,
            "min_completion_share": min_completion_share,
        },
        "gates": {
            "power": power,
            "reliability": reliability,
        },
        "verdict": (
            "LOOKUP-REACHABLE"
            if power and reliability else "LOOKUP-NOT-REACHABLE"
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="data/pumpfundingshadow_live_deadline_*.jsonl",
    )
    parser.add_argument(
        "--json", default="data/pumpfundinglookup_live.json"
    )
    parser.add_argument("--min-births", type=int, default=100)
    parser.add_argument("--min-completion-share", type=float, default=0.95)
    args = parser.parse_args()

    latest = {}
    paths = sorted(glob.glob(args.input))
    for path in paths:
        with open(path) as handle:
            for line in handle:
                row = json.loads(line)
                key = (row.get("signature"), row.get("mint"))
                latest[key] = row
    output = summarize(
        list(latest.values()),
        min_births=args.min_births,
        min_completion_share=args.min_completion_share,
    )
    output["input_files"] = paths
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
