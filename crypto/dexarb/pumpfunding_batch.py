"""Batched full-corpus runner for `pumpfunding`.

    python3 pumpfunding_batch.py --all --batch-size 25
"""
import argparse
import collections
import json
import os

import pumpbirthaudit_batch as Q
import pumpfunding as F

FACT_CACHE_LIMIT = 100_000
TERMINAL = {"direct_funder_found", "no_direct_funder_in_history"}


def transaction_call(signature):
    return "getTransaction", [
        signature,
        {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 1,
            "commitment": "finalized",
        },
    ]


def signatures_call(birth, history_limit):
    return "getSignaturesForAddress", [
        birth["creator"],
        {"before": birth["create_sig"], "limit": history_limit},
    ]


def empty_result(birth):
    return {
        "mint": birth["mint"],
        "creator": birth["creator"],
        "create_sig": birth["create_sig"],
        "create_slot": birth["create_slot"],
        "window": birth["window"],
        "status": "unresolved",
        "history_signatures": 0,
        "transactions_checked": 0,
        "funding_sig": None,
        "funding_slot": None,
        "slots_before_create": None,
        "funders": [],
        "funding_lamports": 0,
        "min_funding_lamports": F.MIN_FUNDING_LAMPORTS,
        "dust_sources_skipped": 0,
        "dust_lamports_skipped": 0,
        "transaction_signers": [],
        "program_ids": [],
        "error": None,
    }


def needs_history_fetch(row):
    return bool(
        row is None
        or row.get("error")
        or row.get("status") not in TERMINAL | {"history_loaded"}
    )


def scan_chunk(rpc, births, history_limit):
    out = [empty_result(birth) for birth in births]
    start_id = rpc.next_id
    responses = rpc.call([
        signatures_call(birth, history_limit) for birth in births
    ])
    ids = list(range(start_id, start_id + len(births)))
    histories = {}
    active = []
    for index, (sigs, error) in enumerate(Q.response_values(responses, ids)):
        if error or sigs is None:
            out[index]["error"] = "history: " + str(error)
            continue
        histories[index] = [item for item in sigs if not item.get("err")]
        out[index]["history_signatures"] = len(histories[index])
        active.append(index)

    for depth in range(history_limit):
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
            current, Q.response_values(responses, ids)
        ):
            if error or tx is None:
                out[index]["error"] = "transaction: " + str(error)
                continue
            out[index]["transactions_checked"] += 1
            funders, signers, dust_n, dust_lamports = F.direct_funders(
                tx, births[index]["creator"]
            )
            out[index]["dust_sources_skipped"] += dust_n
            out[index]["dust_lamports_skipped"] += dust_lamports
            if not funders:
                still_active.append(index)
                continue
            item = histories[index][depth]
            out[index].update({
                "status": "direct_funder_found",
                "funding_sig": item["signature"],
                "funding_slot": item["slot"],
                "slots_before_create": births[index]["create_slot"] - item["slot"],
                "funders": funders,
                "funding_lamports": sum(x["lamports"] for x in funders),
                "transaction_signers": sorted(signers),
                "program_ids": F.program_ids(tx),
            })
        active = [
            index for index in active
            if index not in current or index in still_active
        ]

    for index in active:
        if out[index]["status"] == "unresolved" and not out[index]["error"]:
            out[index]["status"] = "no_direct_funder_in_history"
    return out


def scan_global(
    rpc,
    pending,
    history_limit,
    batch_size,
    sink,
    done,
    resumed=None,
    transaction_batch_size=None,
):
    """Breadth-first history scan so every RPC batch stays near capacity."""
    transaction_batch_size = transaction_batch_size or batch_size
    states = {}
    for birth, marker in resumed or []:
        key = (birth["mint"], birth["create_sig"])
        row = dict(marker)
        history = row.pop("history", [])
        row["status"] = "unresolved"
        states[key] = {"birth": birth, "history": history, "row": row}
    for start in range(0, len(pending), batch_size):
        chunk = pending[start:start + batch_size]
        start_id = rpc.next_id
        responses = rpc.call([
            signatures_call(birth, history_limit) for birth in chunk
        ])
        ids = list(range(start_id, start_id + len(chunk)))
        for birth, (sigs, error) in zip(
            chunk, Q.response_values(responses, ids)
        ):
            key = (birth["mint"], birth["create_sig"])
            row = empty_result(birth)
            if error or sigs is None:
                row["error"] = "history: " + str(error)
                done[key] = row
                sink.write(json.dumps(row, sort_keys=True) + "\n")
                continue
            history = [item for item in sigs if not item.get("err")]
            row["history_signatures"] = len(history)
            marker = dict(row)
            marker["status"] = "history_loaded"
            marker["history"] = history
            done[key] = marker
            sink.write(json.dumps(marker, sort_keys=True) + "\n")
            states[key] = {"birth": birth, "history": history, "row": row}
        sink.flush()
        print(
            f"funding histories {min(start + len(chunk), len(pending))}/"
            f"{len(pending)}; {rpc.http_calls} HTTP batches, "
            f"{rpc.rpc_calls} RPC calls, {rpc.retries} retries",
            flush=True,
        )

    active = set(states)
    fact_cache = collections.OrderedDict()

    def cache_get(signature):
        value = fact_cache.get(signature)
        if value is not None:
            fact_cache.move_to_end(signature)
        return value

    def cache_put(signature, value):
        fact_cache[signature] = value
        fact_cache.move_to_end(signature)
        if len(fact_cache) > FACT_CACHE_LIMIT:
            fact_cache.popitem(last=False)

    for depth in range(history_limit):
        exhausted = [
            key for key in active if depth >= len(states[key]["history"])
        ]
        for key in exhausted:
            row = states[key]["row"]
            row["status"] = "no_direct_funder_in_history"
            done[key] = row
            sink.write(json.dumps(row, sort_keys=True) + "\n")
            active.remove(key)
        sink.flush()

        current = sorted(active)
        if not current:
            break

        by_signature = collections.defaultdict(list)
        for key in current:
            signature = states[key]["history"][depth]["signature"]
            by_signature[signature].append(key)

        cached = {}
        missing = []
        for signature in by_signature:
            value = cache_get(signature)
            if value is None:
                missing.append(signature)
            else:
                cached[signature] = value

        def apply_result(
            signature,
            facts,
            error,
            by_signature=by_signature,
            depth=depth,
        ):
            for key in by_signature[signature]:
                if key not in active:
                    continue
                state = states[key]
                row = state["row"]
                birth = state["birth"]
                if error or facts is None:
                    row["error"] = "transaction: " + str(error)
                    done[key] = row
                    sink.write(json.dumps(row, sort_keys=True) + "\n")
                    active.remove(key)
                    continue
                row["transactions_checked"] += 1
                funders, signers, dust_n, dust_lamports = (
                    F.direct_funders_from_facts(
                        facts["transfers"],
                        facts["signers"],
                        birth["creator"],
                    )
                )
                row["dust_sources_skipped"] += dust_n
                row["dust_lamports_skipped"] += dust_lamports
                if not funders:
                    continue
                item = state["history"][depth]
                row.update({
                    "status": "direct_funder_found",
                    "funding_sig": item["signature"],
                    "funding_slot": item["slot"],
                    "slots_before_create": birth["create_slot"] - item["slot"],
                    "funders": funders,
                    "funding_lamports": sum(x["lamports"] for x in funders),
                    "transaction_signers": sorted(signers),
                    "program_ids": facts["program_ids"],
                })
                done[key] = row
                sink.write(json.dumps(row, sort_keys=True) + "\n")
                active.remove(key)

        for signature, (facts, error) in cached.items():
            apply_result(signature, facts, error)
        sink.flush()

        for start in range(0, len(missing), transaction_batch_size):
            signatures = missing[start:start + transaction_batch_size]
            start_id = rpc.next_id
            responses = rpc.call([
                transaction_call(signature) for signature in signatures
            ])
            ids = list(range(start_id, start_id + len(signatures)))
            for signature, (tx, error) in zip(
                signatures, Q.response_values(responses, ids)
            ):
                facts = (
                    None
                    if error or tx is None
                    else {
                        "transfers": F.parsed_system_transfers(tx),
                        "signers": F.signer_set(tx),
                        "program_ids": F.program_ids(tx),
                    }
                )
                value = (facts, error)
                cache_put(signature, value)
                apply_result(signature, *value)
            sink.flush()
        print(
            f"funding depth {depth + 1}/{history_limit}; unresolved "
            f"{len(active)}; {rpc.http_calls} HTTP batches, "
            f"{rpc.rpc_calls} RPC calls, {rpc.retries} retries",
            flush=True,
        )

    for key in sorted(active):
        row = states[key]["row"]
        row["status"] = "no_direct_funder_in_history"
        done[key] = row
        sink.write(json.dumps(row, sort_keys=True) + "\n")
    sink.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--birth-audit", default="data/pumpbirthaudit.jsonl")
    parser.add_argument("--jsonl", default="data/pumpfunding.jsonl")
    parser.add_argument("--summary", default="data/pumpfunding_summary.json")
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--history-limit", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--transaction-batch-size", type=int)
    parser.add_argument("--pause", type=float, default=0.75)
    parser.add_argument("--seed", type=int, default=20261002)
    args = parser.parse_args()

    chosen = F.births(
        args.birth_audit, None if args.all else args.sample, args.seed
    )
    done = (
        F.latest_rows(args.jsonl, "create_sig")
        if os.path.exists(args.jsonl) else {}
    )
    pending = [
        row for row in chosen
        if needs_history_fetch(
            done.get((row["mint"], row["create_sig"]))
        )
    ]
    resumed = [
        (
            row,
            done[(row["mint"], row["create_sig"])],
        )
        for row in chosen
        if (
            (row["mint"], row["create_sig"]) in done
            and done[(row["mint"], row["create_sig"])].get("status")
            == "history_loaded"
        )
    ]
    url = os.environ.get(
        "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
    ).strip()
    rpc = Q.BatchRpc(url, pause=args.pause)
    with open(args.jsonl, "a") as sink:
        scan_global(
            rpc,
            pending,
            args.history_limit,
            args.batch_size,
            sink,
            done,
            resumed=resumed,
            transaction_batch_size=args.transaction_batch_size,
        )

    selected = [done[(r["mint"], r["create_sig"])] for r in chosen]
    output = {
        "rpc_url": (
            "public-mainnet"
            if url == "https://api.mainnet-beta.solana.com" else "configured"
        ),
        "sample_requested": None if args.all else args.sample,
        "history_limit": args.history_limit,
        "batch_size": args.batch_size,
        "transaction_batch_size": (
            args.transaction_batch_size or args.batch_size
        ),
        "seed": args.seed,
        "summary": F.summarize(selected),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.summary, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
