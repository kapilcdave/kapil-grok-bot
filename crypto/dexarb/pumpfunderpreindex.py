"""Local pre-launch funder cache and capital-disabled shadow join.

The cache is populated from transfers observed before a launch. A CreateEvent
lookup is one indexed SQLite query and makes no launch-time RPC request.

This module has no keypair, transaction builder, signer, or submission path.
"""
import argparse
import json
import sqlite3
import time

import pumpfunding as F
import pumpfundingshadow as S


SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_edges (
    signature TEXT NOT NULL,
    slot INTEGER NOT NULL,
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    lamports INTEGER NOT NULL,
    source_is_signer INTEGER NOT NULL,
    observed_unix_nanos INTEGER,
    PRIMARY KEY (signature, source, destination)
);
CREATE INDEX IF NOT EXISTS funding_destination_slot
ON funding_edges (destination, slot DESC);
CREATE INDEX IF NOT EXISTS funding_source_slot
ON funding_edges (source, slot DESC);
CREATE TABLE IF NOT EXISTS seen_signatures (
    source TEXT NOT NULL,
    signature TEXT NOT NULL,
    slot INTEGER NOT NULL,
    PRIMARY KEY (source, signature)
);
CREATE TABLE IF NOT EXISTS recipient_coverage (
    destination TEXT PRIMARY KEY,
    through_slot INTEGER NOT NULL
);
"""


def open_db(path):
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def insert_edge(
    connection,
    *,
    signature,
    slot,
    source,
    destination,
    lamports,
    source_is_signer,
    observed_unix_nanos=None,
):
    if not source_is_signer or lamports < F.MIN_FUNDING_LAMPORTS:
        return False
    if source == destination:
        return False
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO funding_edges
        (signature, slot, source, destination, lamports, source_is_signer,
         observed_unix_nanos)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signature,
            slot,
            source,
            destination,
            lamports,
            int(source_is_signer),
            observed_unix_nanos,
        ),
    )
    return cursor.rowcount > 0


def ingest_transaction(
    connection,
    tx,
    *,
    signature,
    slot,
    selected_funders,
    watched_destinations=None,
    observed_unix_nanos=None,
):
    """Index selected funding edges and later candidate invalidators."""
    watched_destinations = watched_destinations or set()
    signers = F.signer_set(tx)
    aggregates = {}
    for transfer in F.parsed_system_transfers(tx):
        source = transfer["source"]
        destination = transfer["destination"]
        selected_edge = source in selected_funders
        invalidator = destination in watched_destinations
        if not (selected_edge or invalidator) or source not in signers:
            continue
        key = (source, destination)
        aggregates[key] = aggregates.get(key, 0) + transfer["lamports"]
    inserted = 0
    for (source, destination), lamports in aggregates.items():
        inserted += insert_edge(
            connection,
            signature=signature,
            slot=slot,
            source=source,
            destination=destination,
            lamports=lamports,
            source_is_signer=True,
            observed_unix_nanos=observed_unix_nanos,
        )
    return inserted


def build_from_funding(
    connection,
    path,
    selected_funders=None,
    track_invalidators=False,
):
    """Seed/replay the cache from already-verified historical funding rows."""
    source_rows = [json.loads(line) for line in open(path)]
    watched_destinations = set()
    if selected_funders is not None and track_invalidators:
        watched_destinations = {
            row["creator"]
            for row in source_rows
            if row.get("status") == "direct_funder_found"
            and row.get("funders")
            and row["funders"][0].get("address") in selected_funders
        }
    inserted = 0
    eligible = 0
    for row in source_rows:
        if row.get("status") != "direct_funder_found":
            continue
        funders = row.get("funders") or []
        if not funders:
            continue
        primary = funders[0]
        source = primary.get("address")
        destination = row["creator"]
        if (
            selected_funders is not None
            and source not in selected_funders
            and destination not in watched_destinations
        ):
            continue
        eligible += 1
        inserted += insert_edge(
            connection,
            signature=row["funding_sig"],
            slot=row["funding_slot"],
            source=source,
            destination=destination,
            lamports=primary["lamports"],
            source_is_signer=primary.get("is_signer") is True,
        )
    connection.commit()
    return {"eligible_rows": eligible, "inserted_edges": inserted}


def lookup_edge(connection, destination, create_slot):
    """Newest qualifying edge strictly before creation; same-slot fails closed."""
    row = connection.execute(
        """
        SELECT signature, slot, source, lamports, observed_unix_nanos
        FROM funding_edges
        WHERE destination = ? AND slot < ?
        ORDER BY slot DESC, lamports DESC
        LIMIT 1
        """,
        (destination, create_slot),
    ).fetchone()
    if row is None:
        return None
    signature, slot, source, lamports, observed_unix_nanos = row
    return {
        "status": "direct_funder_found",
        "transactions_checked": 0,
        "funding_sig": signature,
        "funding_slot": slot,
        "slots_before_create": create_slot - slot,
        "funders": [{
            "address": source,
            "lamports": lamports,
            "is_signer": True,
        }],
        "observed_unix_nanos": observed_unix_nanos,
        "error": None,
        "lookup_source": "local_preindex",
    }


def coverage_through(connection, destination):
    row = connection.execute(
        """
        SELECT through_slot FROM recipient_coverage
        WHERE destination = ?
        """,
        (destination,),
    ).fetchone()
    return row[0] if row else None


def mark_coverage(connection, destination, through_slot):
    connection.execute(
        """
        INSERT INTO recipient_coverage (destination, through_slot)
        VALUES (?, ?)
        ON CONFLICT(destination) DO UPDATE SET
            through_slot = MAX(through_slot, excluded.through_slot)
        """,
        (destination, through_slot),
    )


class PreindexedResolver:
    def __init__(
        self,
        connection,
        require_invalidator_coverage=False,
    ):
        self.connection = connection
        self.require_invalidator_coverage = require_invalidator_coverage

    def __call__(
        self,
        _rpc,
        birth,
        _history_limit,
        deadline_seconds,
        clock=time.monotonic,
    ):
        started = clock()
        edge = lookup_edge(
            self.connection, birth["creator"], birth["create_slot"]
        )
        if edge is not None and self.require_invalidator_coverage:
            through = coverage_through(
                self.connection, birth["creator"]
            )
            if through is None or through < birth["create_slot"] - 1:
                return {
                    "status": "invalidation_coverage_missing",
                    "transactions_checked": 0,
                    "funders": [],
                    "coverage_through_slot": through,
                    "error": None,
                    "lookup_source": "local_preindex",
                }
        if clock() - started > deadline_seconds:
            return {
                "status": "lookup_deadline_exceeded",
                "transactions_checked": 0,
                "funders": [],
                "error": None,
            }
        if edge is None:
            return {
                "status": "no_preindexed_funder",
                "transactions_checked": 0,
                "funders": [],
                "error": None,
                "lookup_source": "local_preindex",
            }
        return edge


def export_research_scores(reach_path, output_path):
    result = json.load(open(reach_path))
    allowed = (
        result.get("selected_top_funders", [])
        if result.get("verdict") == "ZERO-SLOT-FUNDER-SHADOW-CANDIDATE"
        else []
    )
    rows = [
        {
            "identity_type": "funder",
            "identity": funder,
            "funder": funder,
            "as_of_slot": result["selection_as_of_slot"],
            "allow": True,
            "reason": "zero_slot_funder_reach_shadow_candidate",
            "model": "pumpfun-preindexed-funder-v1",
            "research_only": True,
            "capital_authorized": False,
        }
        for funder in allowed
    ]
    with open(output_path, "w") as handle:
        handle.writelines(
            json.dumps(row, sort_keys=True) + "\n" for row in rows
        )
    return {
        "verdict": result.get("verdict"),
        "allow_rows": len(rows),
        "research_only": True,
        "capital_authorized": False,
    }


def benchmark(
    connection,
    funding_path,
    samples=10_000,
    selected_funders=None,
):
    births = []
    with open(funding_path) as handle:
        for line in handle:
            row = json.loads(line)
            funders = row.get("funders") or []
            if row.get("status") != "direct_funder_found" or not funders:
                continue
            primary = funders[0]
            if not primary.get("is_signer") or not row.get("creator"):
                continue
            if (
                selected_funders is not None
                and primary["address"] not in selected_funders
            ):
                continue
            births.append((
                row["creator"],
                row["create_slot"],
                primary["address"],
                row["funding_sig"],
            ))
    if not births:
        return {"lookups": 0}
    latencies = []
    found = 0
    source_matches = 0
    signature_matches = 0
    for i in range(samples):
        creator, slot, expected_source, expected_signature = (
            births[i % len(births)]
        )
        started = time.perf_counter_ns()
        edge = lookup_edge(connection, creator, slot)
        latencies.append(time.perf_counter_ns() - started)
        found += edge is not None
        if edge is not None:
            source_matches += (
                edge["funders"][0]["address"] == expected_source
            )
            signature_matches += (
                edge["funding_sig"] == expected_signature
            )
    latencies.sort()

    def percentile(p):
        return latencies[min(len(latencies) - 1, int(p * len(latencies)))]

    return {
        "lookups": len(latencies),
        "eligible_historical_births": len(births),
        "found": found,
        "completion_rate": found / len(latencies),
        "source_match_rate": source_matches / len(latencies),
        "signature_match_rate": signature_matches / len(latencies),
        "latency_us": {
            "p50": percentile(0.50) / 1_000,
            "p95": percentile(0.95) / 1_000,
            "p99": percentile(0.99) / 1_000,
            "max": max(latencies) / 1_000,
        },
    }


def shadow(
    connection,
    input_path,
    scores_path,
    output_path,
    max_age_slots,
    deadline_seconds,
    require_invalidator_coverage=False,
):
    scores = S.load_scores(scores_path)
    resolver = PreindexedResolver(
        connection,
        require_invalidator_coverage=require_invalidator_coverage,
    )
    count = 0
    with open(input_path) as source, open(output_path, "w") as sink:
        for line in source:
            record = json.loads(line)
            decisions = S.record_decisions(
                record,
                scores,
                rpc=None,
                history_limit=0,
                max_age_slots=max_age_slots,
                lookup_deadline_seconds=deadline_seconds,
                resolver=resolver,
            )
            for decision in decisions:
                decision["lookup_source"] = "local_preindex"
                sink.write(json.dumps(decision, sort_keys=True) + "\n")
                count += 1
    return {"decisions": count, "capital_enabled": False}


def selected_from_scores(path):
    return set(S.load_scores(path))


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--db", required=True)
    build.add_argument("--funding", default="data/pumpfunding.jsonl")
    build.add_argument("--scores")
    build.add_argument("--track-invalidators", action="store_true")

    scores = subparsers.add_parser("scores")
    scores.add_argument(
        "--reach", default="data/pumpfunderreach.json"
    )
    scores.add_argument(
        "--output", default="data/pumpfunder_research_scores.jsonl"
    )

    bench = subparsers.add_parser("benchmark")
    bench.add_argument("--db", required=True)
    bench.add_argument("--funding", default="data/pumpfunding.jsonl")
    bench.add_argument("--scores")
    bench.add_argument("--samples", type=int, default=10_000)
    bench.add_argument(
        "--json", default="data/pumpfunderpreindex_benchmark.json"
    )

    gate = subparsers.add_parser("shadow")
    gate.add_argument("--db", required=True)
    gate.add_argument("--input", required=True)
    gate.add_argument(
        "--scores", default="data/pumpfunder_research_scores.jsonl"
    )
    gate.add_argument(
        "--output", default="data/pumpfunderpreindex_shadow.jsonl"
    )
    gate.add_argument("--max-age-slots", type=int, default=20_000_000)
    gate.add_argument("--deadline-seconds", type=float, default=0.002)
    gate.add_argument(
        "--require-invalidator-coverage",
        action="store_true",
    )

    args = parser.parse_args()
    if args.command == "scores":
        output = export_research_scores(args.reach, args.output)
    else:
        connection = open_db(args.db)
        try:
            if args.command == "build":
                selected = (
                    selected_from_scores(args.scores)
                    if args.scores else None
                )
                output = build_from_funding(
                    connection,
                    args.funding,
                    selected,
                    track_invalidators=args.track_invalidators,
                )
            elif args.command == "benchmark":
                selected = (
                    selected_from_scores(args.scores)
                    if args.scores else None
                )
                output = benchmark(
                    connection,
                    args.funding,
                    args.samples,
                    selected_funders=selected,
                )
                with open(args.json, "w") as handle:
                    json.dump(output, handle, indent=2, sort_keys=True)
                    handle.write("\n")
            else:
                output = shadow(
                    connection,
                    args.input,
                    args.scores,
                    args.output,
                    args.max_age_slots,
                    args.deadline_seconds,
                    args.require_invalidator_coverage,
                )
        finally:
            connection.close()
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
