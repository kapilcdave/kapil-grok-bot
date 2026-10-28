"""Resolve persisted ``needs_older_scan`` birth-audit rows by shard."""
import argparse
import hashlib
import json
import os

import pumpbirthaudit_batch as Q


def latest_markers(path):
    latest = {}
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            latest[(row["mint"], row["first_sig"])] = row
    return [
        row for row in latest.values()
        if row.get("status") == "needs_older_scan"
    ]


def shard_of(row, count):
    raw = f"{row['mint']}:{row['first_sig']}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit", default="data/pumpbirthaudit.jsonl"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--older-limit", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--pause", type=float, default=2.2)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("invalid shard index")

    rows = [
        row for row in latest_markers(args.audit)
        if shard_of(row, args.shard_count) == args.shard_index
    ]
    rows.sort(key=lambda row: (row["first_slot"], row["mint"]))
    url = os.environ.get(
        "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
    ).strip()
    rpc = Q.BatchRpc(url, pause=args.pause)
    with open(args.output, "w") as sink:
        for start in range(0, len(rows), args.batch_size):
            chunk = rows[start:start + args.batch_size]
            resolved = Q.older_pass_chunk(
                rpc, chunk, args.older_limit
            )
            sink.writelines(
                json.dumps(row, sort_keys=True) + "\n"
                for row in resolved
            )
            sink.flush()
            print(
                f"shard {args.shard_index}: "
                f"{min(start + len(chunk), len(rows))}/{len(rows)}; "
                f"{rpc.http_calls} HTTP batches, {rpc.rpc_calls} RPC calls, "
                f"{rpc.retries} retries",
                flush=True,
            )


if __name__ == "__main__":
    main()
