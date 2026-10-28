"""Batched full-corpus runner for `pumpbirthaudit`.

Solana's public RPC accepts JSON-RPC arrays. Most candidates resolve from their
first transaction, so batching removes thousands of HTTP round trips while
preserving the exact same classification and output schema.

    python3 pumpbirthaudit_batch.py --all --batch-size 40
"""
import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request

import pumpbirthaudit as B


class BatchRpc:
    def __init__(self, url, pause=0.5):
        self.url = url
        self.pause = pause
        self.http_calls = 0
        self.rpc_calls = 0
        self.retries = 0
        self.next_id = 1

    def call(self, calls, tries=20):
        if not calls:
            return {}
        requests = []
        for method, params in calls:
            requests.append({
                "jsonrpc": "2.0",
                "id": self.next_id,
                "method": method,
                "params": params,
            })
            self.next_id += 1
        pending = {request["id"]: request for request in requests}
        got = {}
        message = "unknown"
        for attempt in range(tries):
            active = list(pending.values())
            payload = json.dumps(active).encode()
            self.http_calls += 1
            self.rpc_calls += len(active)
            req = urllib.request.Request(
                self.url, payload, {"content-type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as response:
                    raw = json.load(response)
                if isinstance(raw, dict):
                    raw = [raw]
                returned = {item.get("id"): item for item in raw}
                for request_id in list(pending):
                    item = returned.get(request_id)
                    if item is None:
                        message = "incomplete batch response"
                        continue
                    if retryable_rpc_error(item.get("error")):
                        message = "retryable RPC throttle"
                        continue
                    got[request_id] = item
                    del pending[request_id]
                if not pending:
                    time.sleep(self.pause)
                    return got
            except (
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
            ) as exc:
                message = type(exc).__name__
            self.retries += 1
            time.sleep(min(15.0, 0.75 * 2 ** attempt) + random.random() * 0.25)
        raise RuntimeError(
            f"batch failed after {tries} attempts with {len(pending)} "
            f"unresolved calls: {message}"
        )


def retryable_rpc_error(error):
    if not error:
        return False
    message = str(error).lower()
    return (
        error.get("code") == 429
        or "too many requests" in message
        or "rate limit" in message
    )


def transaction_call(signature):
    return "getTransaction", [
        signature,
        {
            "encoding": "json",
            "maxSupportedTransactionVersion": 1,
            "commitment": "finalized",
        },
    ]


def signatures_call(candidate, older_limit):
    return "getSignaturesForAddress", [
        candidate["mint"],
        {"before": candidate["first_sig"], "limit": older_limit},
    ]


def empty_result(candidate):
    return {
        **candidate,
        "status": "unresolved",
        "create_sig": None,
        "create_slot": None,
        "creator": None,
        "create_user": None,
        "prior_trade_sig": None,
        "prior_trade_slot": None,
        "older_signatures_checked": 0,
        "error": None,
    }


def response_values(responses, request_ids):
    values = []
    for request_id in request_ids:
        item = responses[request_id]
        values.append((item.get("result"), item.get("error")))
    return values


def first_pass_chunk(rpc, candidates):
    """Resolve atomic create+first-trade transactions; defer the rest."""
    out = [empty_result(candidate) for candidate in candidates]
    start_id = rpc.next_id
    responses = rpc.call([
        transaction_call(candidate["first_sig"]) for candidate in candidates
    ])
    ids = list(range(start_id, start_id + len(candidates)))
    resolved = []
    unresolved = []
    for index, (tx, error) in enumerate(response_values(responses, ids)):
        if error or tx is None:
            out[index]["error"] = "first_tx: " + str(error)
            resolved.append(out[index])
            continue
        creates = [
            event for kind, event in B.pump_events(tx)
            if kind == "create" and event["mint"] == candidates[index]["mint"]
        ]
        if creates:
            out[index].update({
                "status": "create_in_first_trade_tx",
                "create_sig": candidates[index]["first_sig"],
                "create_slot": candidates[index]["first_slot"],
                "creator": creates[0]["creator"],
                "create_user": creates[0]["user"],
            })
            resolved.append(out[index])
        else:
            unresolved.append(candidates[index])
    return resolved, unresolved


def older_pass_chunk(rpc, candidates, older_limit):
    """Classify candidates whose first trade transaction had no CreateEvent."""
    out = [empty_result(candidate) for candidate in candidates]
    start_id = rpc.next_id
    responses = rpc.call([
        signatures_call(candidate, older_limit) for candidate in candidates
    ])
    ids = list(range(start_id, start_id + len(candidates)))
    histories = {}
    active = []
    for index, (sigs, error) in enumerate(response_values(responses, ids)):
        if error or sigs is None:
            out[index]["error"] = "older_sigs: " + str(error)
            continue
        histories[index] = [item for item in sigs if not item.get("err")]
        active.append(index)

    for depth in range(older_limit):
        current = [index for index in active if depth < len(histories[index])]
        if not current:
            break
        start_id = rpc.next_id
        responses = rpc.call([
            transaction_call(histories[index][depth]["signature"])
            for index in current
        ])
        ids = list(range(start_id, start_id + len(current)))
        still_active = []
        for index, (tx, error) in zip(
            current, response_values(responses, ids)
        ):
            if error or tx is None:
                out[index]["error"] = "older_tx: " + str(error)
                continue
            item = histories[index][depth]
            out[index]["older_signatures_checked"] += 1
            events = [
                (kind, event) for kind, event in B.pump_events(tx)
                if event["mint"] == candidates[index]["mint"]
            ]
            if any(kind == "trade" for kind, _event in events):
                out[index].update({
                    "status": "prior_trade_proves_not_birth",
                    "prior_trade_sig": item["signature"],
                    "prior_trade_slot": item["slot"],
                })
                continue
            creates = [event for kind, event in events if kind == "create"]
            if creates:
                out[index].update({
                    "status": "create_precedes_first_trade",
                    "create_sig": item["signature"],
                    "create_slot": item["slot"],
                    "creator": creates[0]["creator"],
                    "create_user": creates[0]["user"],
                })
                continue
            still_active.append(index)
        active = [
            index for index in active
            if index not in current or index in still_active
        ]

    for index in active:
        if out[index]["status"] == "unresolved" and not out[index]["error"]:
            out[index]["status"] = "no_create_or_prior_trade_in_scan"
    return out


def audit_chunk(rpc, candidates, older_limit):
    out = [empty_result(candidate) for candidate in candidates]

    start_id = rpc.next_id
    responses = rpc.call([
        transaction_call(candidate["first_sig"]) for candidate in candidates
    ])
    first_ids = list(range(start_id, start_id + len(candidates)))
    unresolved = []
    for index, (tx, error) in enumerate(response_values(responses, first_ids)):
        if error or tx is None:
            out[index]["error"] = "first_tx: " + str(error)
            continue
        creates = [
            event for kind, event in B.pump_events(tx)
            if kind == "create" and event["mint"] == candidates[index]["mint"]
        ]
        if creates:
            out[index].update({
                "status": "create_in_first_trade_tx",
                "create_sig": candidates[index]["first_sig"],
                "create_slot": candidates[index]["first_slot"],
                "creator": creates[0]["creator"],
                "create_user": creates[0]["user"],
            })
        else:
            unresolved.append(index)

    if not unresolved:
        return out

    start_id = rpc.next_id
    responses = rpc.call([
        signatures_call(candidates[index], older_limit)
        for index in unresolved
    ])
    sig_ids = list(range(start_id, start_id + len(unresolved)))
    histories = {}
    active = []
    for index, (sigs, error) in zip(
        unresolved, response_values(responses, sig_ids)
    ):
        if error or sigs is None:
            out[index]["error"] = "older_sigs: " + str(error)
            continue
        histories[index] = [item for item in sigs if not item.get("err")]
        active.append(index)

    for depth in range(older_limit):
        current = [index for index in active if depth < len(histories[index])]
        if not current:
            break
        start_id = rpc.next_id
        responses = rpc.call([
            transaction_call(histories[index][depth]["signature"])
            for index in current
        ])
        tx_ids = list(range(start_id, start_id + len(current)))
        still_active = []
        for index, (tx, error) in zip(
            current, response_values(responses, tx_ids)
        ):
            if error or tx is None:
                out[index]["error"] = "older_tx: " + str(error)
                continue
            item = histories[index][depth]
            out[index]["older_signatures_checked"] += 1
            events = [
                (kind, event) for kind, event in B.pump_events(tx)
                if event["mint"] == candidates[index]["mint"]
            ]
            if any(kind == "trade" for kind, _event in events):
                out[index].update({
                    "status": "prior_trade_proves_not_birth",
                    "prior_trade_sig": item["signature"],
                    "prior_trade_slot": item["slot"],
                })
                continue
            creates = [event for kind, event in events if kind == "create"]
            if creates:
                out[index].update({
                    "status": "create_precedes_first_trade",
                    "create_sig": item["signature"],
                    "create_slot": item["slot"],
                    "creator": creates[0]["creator"],
                    "create_user": creates[0]["user"],
                })
                continue
            still_active.append(index)
        active = [
            index for index in active
            if index not in current or index in still_active
        ]

    for index in active:
        if out[index]["status"] == "unresolved" and not out[index]["error"]:
            out[index]["status"] = "no_create_or_prior_trade_in_scan"
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--jsonl", default="data/pumpbirthaudit.jsonl")
    parser.add_argument("--summary", default="data/pumpbirthaudit_summary.json")
    parser.add_argument("--sample", type=int, default=160)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--older-limit", type=int, default=20)
    parser.add_argument("--boundary-slots", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=20261002)
    args = parser.parse_args()

    chosen = B.candidates(
        args.tape, None if args.all else args.sample, args.seed,
        args.boundary_slots,
    )
    done = {}
    if os.path.exists(args.jsonl):
        with open(args.jsonl) as handle:
            for line in handle:
                row = json.loads(line)
                done[(row["mint"], row["first_sig"])] = row
    pending = [
        row for row in chosen
        if (
            (row["mint"], row["first_sig"]) not in done
            or done[(row["mint"], row["first_sig"])].get("error")
        )
    ]
    resume_older = [
        row for row in chosen
        if (
            (row["mint"], row["first_sig"]) in done
            and done[(row["mint"], row["first_sig"])].get("status")
            == "needs_older_scan"
        )
    ]
    url = os.environ.get(
        "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
    ).strip()
    rpc = BatchRpc(url, pause=args.pause)
    with open(args.jsonl, "a") as sink:
        needs_older = list(resume_older)
        for start in range(0, len(pending), args.batch_size):
            chunk = pending[start:start + args.batch_size]
            rows, unresolved = first_pass_chunk(rpc, chunk)
            needs_older.extend(unresolved)
            for row in rows:
                done[(row["mint"], row["first_sig"])] = row
                sink.write(json.dumps(row, sort_keys=True) + "\n")
            for candidate in unresolved:
                marker = empty_result(candidate)
                marker["status"] = "needs_older_scan"
                done[(marker["mint"], marker["first_sig"])] = marker
                sink.write(json.dumps(marker, sort_keys=True) + "\n")
            sink.flush()
            print(
                f"first pass {min(start + len(chunk), len(pending))}/"
                f"{len(pending)}; deferred {len(needs_older)}; "
                f"{rpc.http_calls} HTTP batches, {rpc.rpc_calls} RPC calls, "
                f"{rpc.retries} retries",
                flush=True,
            )
        for start in range(0, len(needs_older), args.batch_size):
            chunk = needs_older[start:start + args.batch_size]
            rows = older_pass_chunk(rpc, chunk, args.older_limit)
            for row in rows:
                done[(row["mint"], row["first_sig"])] = row
                sink.write(json.dumps(row, sort_keys=True) + "\n")
            sink.flush()
            print(
                f"older pass {min(start + len(chunk), len(needs_older))}/"
                f"{len(needs_older)}; "
                f"{rpc.http_calls} HTTP batches, {rpc.rpc_calls} RPC calls, "
                f"{rpc.retries} retries",
                flush=True,
            )

    selected = [done[(r["mint"], r["first_sig"])] for r in chosen]
    output = {
        "rpc_url": (
            "public-mainnet"
            if url == "https://api.mainnet-beta.solana.com" else "configured"
        ),
        "sample_requested": None if args.all else args.sample,
        "older_limit": args.older_limit,
        "boundary_slots": args.boundary_slots,
        "batch_size": args.batch_size,
        "seed": args.seed,
        "summary": B.summarize(selected),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.summary, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
