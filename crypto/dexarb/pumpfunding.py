"""Trace the most recent direct SOL funder of verified pump.fun creators.

Input births must come from `pumpbirthaudit.py`; reserve-state inference is not
accepted.  For each creator, this scanner walks finalized transactions strictly
before the decoded CreateEvent transaction and finds the newest parsed System
Program transfer into that creator.

The output is a resumable edge list:

    funder --(SOL, slot, signature)--> creator --(CreateEvent)--> mint

Read-only.  No keypair is loaded and no transaction can be submitted.

    python3 pumpfunding.py --sample 200
    python3 pumpfunding.py --all
"""
import argparse
import concurrent.futures as cf
import json
import os
import random

import pumpbirthaudit as B

VERIFIED_BIRTH = {"create_in_first_trade_tx", "create_precedes_first_trade"}
MIN_FUNDING_LAMPORTS = 5_000_000


def account_key(value):
    return value if isinstance(value, str) else value.get("pubkey")


def parsed_system_transfers(tx):
    """All successfully executed parsed native-SOL transfers in a transaction."""
    if not tx or (tx.get("meta") or {}).get("err"):
        return []
    groups = [(tx.get("transaction") or {}).get("message", {}).get(
        "instructions", []
    )]
    groups.extend(
        group.get("instructions", [])
        for group in (tx.get("meta") or {}).get("innerInstructions") or []
    )
    out = []
    for instructions in groups:
        for ix in instructions:
            if ix.get("program") != "system":
                continue
            parsed = ix.get("parsed") or {}
            if parsed.get("type") not in {"transfer", "transferWithSeed"}:
                continue
            info = parsed.get("info") or {}
            source = info.get("source")
            destination = info.get("destination")
            lamports = info.get("lamports")
            if source and destination and isinstance(lamports, int):
                out.append({
                    "source": source,
                    "destination": destination,
                    "lamports": lamports,
                })
    return out


def signer_set(tx):
    keys = (tx.get("transaction") or {}).get("message", {}).get(
        "accountKeys", []
    )
    return {
        account_key(key) for key in keys
        if isinstance(key, dict) and key.get("signer")
    }


def direct_funders(tx, destination, min_lamports=MIN_FUNDING_LAMPORTS):
    """Qualifying incoming sources, aggregating same-source transfer legs.

    Tiny transfers are trivially sprayable and cannot establish who bankrolled
    a creator. They are counted as diagnostics while the scan continues to an
    older qualifying transfer.
    """
    return direct_funders_from_facts(
        parsed_system_transfers(tx),
        signer_set(tx),
        destination,
        min_lamports,
    )


def direct_funders_from_facts(
    transfers,
    signers,
    destination,
    min_lamports=MIN_FUNDING_LAMPORTS,
):
    """Apply the funding rule to compact, reusable transaction facts."""
    by_source = {}
    for edge in transfers:
        if edge["destination"] != destination or edge["source"] == destination:
            continue
        by_source[edge["source"]] = (
            by_source.get(edge["source"], 0) + edge["lamports"]
        )
    qualifying = [
        {
            "address": source,
            "lamports": lamports,
            "is_signer": source in signers,
        }
        for source, lamports in sorted(
            by_source.items(), key=lambda pair: pair[1], reverse=True
        )
        if lamports >= min_lamports
    ]
    dust = [
        lamports for lamports in by_source.values()
        if lamports < min_lamports
    ]
    return qualifying, signers, len(dust), sum(dust)


def program_ids(tx):
    groups = [(tx.get("transaction") or {}).get("message", {}).get(
        "instructions", []
    )]
    groups.extend(
        group.get("instructions", [])
        for group in (tx.get("meta") or {}).get("innerInstructions") or []
    )
    return sorted({
        ix.get("programId") for instructions in groups for ix in instructions
        if ix.get("programId")
    })


def latest_rows(path, signature_field="first_sig"):
    rows = {}
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            rows[(row["mint"], row[signature_field])] = row
    return rows


def births(path, sample, seed):
    rows = [
        row for row in latest_rows(path).values()
        if row["status"] in VERIFIED_BIRTH and row.get("creator")
    ]
    rows.sort(key=lambda r: (r["create_slot"], r["mint"]))
    if sample is None or sample >= len(rows):
        return rows
    rnd = random.Random(seed)
    return rnd.sample(rows, sample)


def inspect_creator(rpc, birth, history_limit):
    out = {
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
        "min_funding_lamports": MIN_FUNDING_LAMPORTS,
        "dust_sources_skipped": 0,
        "dust_lamports_skipped": 0,
        "transaction_signers": [],
        "program_ids": [],
        "error": None,
    }
    sigs, error = rpc.signatures_before(
        birth["creator"], birth["create_sig"], history_limit
    )
    if error or sigs is None:
        out["error"] = "history: " + str(error)
        return out
    successful = [item for item in sigs if not item.get("err")]
    out["history_signatures"] = len(successful)
    for item in successful:
        tx, error = rpc.transaction(item["signature"], encoding="jsonParsed")
        if error or tx is None:
            out["error"] = "transaction: " + str(error)
            return out
        out["transactions_checked"] += 1
        funders, signers, dust_n, dust_lamports = direct_funders(
            tx, birth["creator"]
        )
        out["dust_sources_skipped"] += dust_n
        out["dust_lamports_skipped"] += dust_lamports
        if not funders:
            continue
        out.update({
            "status": "direct_funder_found",
            "funding_sig": item["signature"],
            "funding_slot": item["slot"],
            "slots_before_create": birth["create_slot"] - item["slot"],
            "funders": funders,
            "funding_lamports": sum(x["lamports"] for x in funders),
            "transaction_signers": sorted(signers),
            "program_ids": program_ids(tx),
        })
        return out
    out["status"] = "no_direct_funder_in_history"
    return out


def summarize(rows):
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    funded = [r for r in rows if r["status"] == "direct_funder_found"]
    signer_funded = [
        r for r in funded if r["funders"] and r["funders"][0]["is_signer"]
    ]
    primary = [r["funders"][0]["address"] for r in funded if r["funders"]]
    clusters = {}
    for address in primary:
        clusters[address] = clusters.get(address, 0) + 1
    repeated = {address: n for address, n in clusters.items() if n >= 2}
    deltas = sorted(r["slots_before_create"] for r in funded)
    amounts = sorted(r["funding_lamports"] / 1e9 for r in funded)

    def quantile(values, p):
        return values[min(len(values) - 1, int(p * len(values)))] if values else None

    return {
        "n": len(rows),
        "min_funding_lamports": MIN_FUNDING_LAMPORTS,
        "status_counts": counts,
        "direct_funder_rate": len(funded) / len(rows) if rows else None,
        "signer_source_rate": (
            len(signer_funded) / len(rows) if rows else None
        ),
        "signer_share_of_found_edges": (
            len(signer_funded) / len(funded) if funded else None
        ),
        "distinct_primary_funders": len(clusters),
        "repeated_primary_funders": len(repeated),
        "launches_from_repeated_primary_funders": sum(repeated.values()),
        "slots_before_create": {
            "p50": quantile(deltas, 0.5),
            "p90": quantile(deltas, 0.9),
            "share_le_2": (
                sum(x <= 2 for x in deltas) / len(deltas) if deltas else None
            ),
        },
        "funding_sol": {
            "p50": quantile(amounts, 0.5),
            "p90": quantile(amounts, 0.9),
        },
        "dust_sources_skipped": sum(
            r.get("dust_sources_skipped", 0) for r in rows
        ),
        "dust_lamports_skipped": sum(
            r.get("dust_lamports_skipped", 0) for r in rows
        ),
        "errors": sum(bool(r.get("error")) for r in rows),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--birth-audit", default="data/pumpbirthaudit.jsonl")
    parser.add_argument("--jsonl", default="data/pumpfunding.jsonl")
    parser.add_argument("--summary", default="data/pumpfunding_summary.json")
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--history-limit", type=int, default=50)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--qps", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20261002)
    args = parser.parse_args()

    chosen = births(
        args.birth_audit, None if args.all else args.sample, args.seed
    )
    done = (
        latest_rows(args.jsonl, "create_sig")
        if os.path.exists(args.jsonl) else {}
    )
    pending = [
        row for row in chosen
        if (
            (row["mint"], row["create_sig"]) not in done
            or done[(row["mint"], row["create_sig"])].get("error")
        )
    ]
    url = os.environ.get(
        "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
    ).strip()
    rpc = B.Rpc(url, min_interval=1.0 / max(args.qps, 0.1))
    with (
        open(args.jsonl, "a") as sink,
        cf.ThreadPoolExecutor(max_workers=args.workers) as pool,
    ):
        futures = {
            pool.submit(inspect_creator, rpc, row, args.history_limit): row
            for row in pending
        }
        for i, future in enumerate(cf.as_completed(futures), 1):
            row = future.result()
            done[(row["mint"], row["create_sig"])] = row
            sink.write(json.dumps(row, sort_keys=True) + "\n")
            sink.flush()
            if i % 10 == 0 or i == len(pending):
                print(
                    f"{i}/{len(pending)} new; {rpc.calls} RPC calls, "
                    f"{rpc.retries} retries",
                    flush=True,
                )

    selected = [done[(r["mint"], r["create_sig"])] for r in chosen]
    output = {
        "rpc_url": (
            "public-mainnet"
            if url == "https://api.mainnet-beta.solana.com" else "configured"
        ),
        "sample_requested": None if args.all else args.sample,
        "history_limit": args.history_limit,
        "seed": args.seed,
        "summary": summarize(selected),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.summary, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
