"""Read-only pre-launch watcher for frozen high-score funders.

It polls each selected funder's recent confirmed signatures, fetches only new
transactions, and writes qualifying outgoing signer transfers into the local
SQLite pre-index. It never performs a creator-history lookup at launch time.

No keypair, transaction builder, signer, or submission path exists here.
"""
import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request

import pumpbirthaudit as B
import pumpfunderpreindex as P


class BatchRpc(B.Rpc):
    """Small JSON-RPC batch client for the pre-launch polling workload."""

    def batch_call(self, calls, tries=2, timeout=20):
        if not calls:
            return []
        message = None
        for attempt in range(tries):
            with self.lock:
                wait = self.min_interval - (time.monotonic() - self.last)
                if wait > 0:
                    time.sleep(wait)
                self.last = time.monotonic()
                self.calls += 1
            payload = json.dumps([
                {
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": method,
                    "params": params,
                }
                for index, (method, params) in enumerate(calls)
            ]).encode()
            request = urllib.request.Request(
                self.url, payload, {"content-type": "application/json"}
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=timeout
                ) as response:
                    values = json.load(response)
                if not isinstance(values, list):
                    message = "batch response was not a list"
                else:
                    by_id = {
                        value.get("id"): value for value in values
                        if isinstance(value, dict)
                    }
                    return [
                        (
                            by_id.get(index, {}).get("result"),
                            by_id.get(index, {}).get("error"),
                        )
                        for index in range(len(calls))
                    ]
            except (
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
            ) as error:
                message = type(error).__name__
            self.retries += 1
            if attempt + 1 < tries:
                time.sleep(0.35 * 2 ** attempt + random.random() * 0.2)
        return [(None, message) for _call in calls]


def recent_signatures(rpc, funder, limit):
    return rpc.call(
        "getSignaturesForAddress",
        [
            funder,
            {"limit": limit, "commitment": "confirmed"},
        ],
        tries=2,
        timeout=10,
    )


def confirmed_transaction(rpc, signature):
    return rpc.call(
        "getTransaction",
        [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 1,
                "commitment": "confirmed",
            },
        ],
        tries=2,
        timeout=10,
    )


def signature_seen(connection, funder, signature):
    return connection.execute(
        """
        SELECT 1 FROM seen_signatures
        WHERE source = ? AND signature = ?
        """,
        (funder, signature),
    ).fetchone() is not None


def mark_seen(connection, funder, signature, slot):
    connection.execute(
        """
        INSERT OR IGNORE INTO seen_signatures (source, signature, slot)
        VALUES (?, ?, ?)
        """,
        (funder, signature, slot),
    )


def poll_funder(connection, rpc, funder, limit):
    signatures, error = recent_signatures(rpc, funder, limit)
    if error or signatures is None:
        return {
            "funder": funder,
            "signatures": 0,
            "transactions": 0,
            "edges": 0,
            "error": error,
        }
    unseen = [
        row for row in signatures
        if not row.get("err")
        and not signature_seen(connection, funder, row["signature"])
    ]
    transactions = 0
    edges = 0
    errors = []
    for item in reversed(unseen):
        signature = item["signature"]
        tx, tx_error = confirmed_transaction(rpc, signature)
        if tx_error or tx is None:
            errors.append({"signature": signature, "error": tx_error})
            continue
        transactions += 1
        edges += P.ingest_transaction(
            connection,
            tx,
            signature=signature,
            slot=item["slot"],
            selected_funders={funder},
            observed_unix_nanos=time.time_ns(),
        )
        mark_seen(connection, funder, signature, item["slot"])
    connection.commit()
    return {
        "funder": funder,
        "signatures": len(signatures),
        "unseen": len(unseen),
        "transactions": transactions,
        "edges": edges,
        "errors": errors,
    }


def run_cycle(connection, rpc, funders, limit):
    if hasattr(rpc, "batch_call"):
        return run_cycle_batched(connection, rpc, funders, limit)
    rows = [
        poll_funder(connection, rpc, funder, limit)
        for funder in sorted(funders)
    ]
    return {
        "timestamp_unix_nanos": time.time_ns(),
        "funders": len(funders),
        "transactions": sum(row["transactions"] for row in rows),
        "edges": sum(row["edges"] for row in rows),
        "errors": sum(bool(row.get("error") or row.get("errors")) for row in rows),
        "rows": rows,
        "capital_enabled": False,
    }


def chunks(values, size):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def run_cycle_batched(connection, rpc, funders, limit, batch_size=25):
    ordered = sorted(funders)
    signature_calls = [
        (
            "getSignaturesForAddress",
            [
                funder,
                {"limit": limit, "commitment": "confirmed"},
            ],
        )
        for funder in ordered
    ]
    signature_results = rpc.batch_call(signature_calls)
    rows = {
        funder: {
            "funder": funder,
            "signatures": 0,
            "unseen": 0,
            "transactions": 0,
            "edges": 0,
            "errors": [],
        }
        for funder in ordered
    }
    pending = []
    for funder, (signatures, error) in zip(
        ordered, signature_results
    ):
        if error or signatures is None:
            rows[funder]["error"] = error
            continue
        rows[funder]["signatures"] = len(signatures)
        unseen = [
            item for item in signatures
            if not item.get("err")
            and not signature_seen(
                connection, funder, item["signature"]
            )
        ]
        rows[funder]["unseen"] = len(unseen)
        pending.extend(
            (funder, item) for item in reversed(unseen)
        )

    for group in chunks(pending, batch_size):
        calls = [
            (
                "getTransaction",
                [
                    item["signature"],
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 1,
                        "commitment": "confirmed",
                    },
                ],
            )
            for _funder, item in group
        ]
        for (funder, item), (tx, error) in zip(
            group, rpc.batch_call(calls)
        ):
            if error or tx is None:
                rows[funder]["errors"].append({
                    "signature": item["signature"],
                    "error": error,
                })
                continue
            rows[funder]["transactions"] += 1
            rows[funder]["edges"] += P.ingest_transaction(
                connection,
                tx,
                signature=item["signature"],
                slot=item["slot"],
                selected_funders={funder},
                observed_unix_nanos=time.time_ns(),
            )
            mark_seen(
                connection, funder, item["signature"], item["slot"]
            )
        connection.commit()

    output_rows = [rows[funder] for funder in ordered]
    return {
        "timestamp_unix_nanos": time.time_ns(),
        "funders": len(funders),
        "transactions": sum(
            row["transactions"] for row in output_rows
        ),
        "edges": sum(row["edges"] for row in output_rows),
        "errors": sum(
            bool(row.get("error") or row.get("errors"))
            for row in output_rows
        ),
        "rows": output_rows,
        "rpc_mode": "batch",
        "capital_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--scores", default="data/pumpfunder_research_scores.jsonl"
    )
    parser.add_argument(
        "--journal", default="data/pumpfunderwatch.jsonl"
    )
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--qps", type=float, default=8.0)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    funders = P.selected_from_scores(args.scores)
    if not funders:
        raise SystemExit("no research funders in score file")
    url = os.environ.get(
        "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
    ).strip()
    rpc = BatchRpc(url, min_interval=1.0 / max(args.qps, 0.1))
    connection = P.open_db(args.db)
    try:
        with open(args.journal, "a") as journal:
            while True:
                output = run_cycle(
                    connection, rpc, funders, args.limit
                )
                journal.write(json.dumps(output, sort_keys=True) + "\n")
                journal.flush()
                print(json.dumps(output, sort_keys=True), flush=True)
                if args.once:
                    break
                time.sleep(args.interval_seconds)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
