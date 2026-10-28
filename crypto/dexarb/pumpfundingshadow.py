"""Read-only live shadow gate for signer-funder scores.

Unlike ``pumpgraphshadow.py``, this path does not pretend that a historical
creator->funder edge remains valid for a new launch. For every authoritative
CreateEvent/birth pair it resolves that creator's newest direct pre-creation
SOL funding transaction, requires the primary source to be a signer, and only
then joins a frozen funder score.

The resolver is intentionally measured against a wall-clock deadline. Public
RPC history lookup may be too slow for the preregistered entry floor; a late
positive lookup is recorded as a rejection rather than counted as reachable.

There is no keypair, transaction builder, signer or submission path.
"""
import argparse
import json
import os
import time

import pumpbirthaudit as B
import pumpfunding as F


def live_signatures_before(
    rpc,
    birth,
    history_limit,
    timeout=None,
):
    """Get causally older signatures without requiring a finalized birth.

    ``before=create_sig`` can fail at launch time because the just-observed
    signature is not finalized yet. Query confirmed creator history instead.
    If the create signature is present, list order gives an exact boundary. If
    it is absent, fail closed on same-slot ordering and retain only lower slots.
    """
    call_kwargs = (
        {"tries": 1, "timeout": timeout}
        if timeout is not None else {}
    )
    signatures, error = rpc.call(
        "getSignaturesForAddress",
        [
            birth["creator"],
            {"limit": history_limit + 1, "commitment": "confirmed"},
        ],
        **call_kwargs,
    )
    if error or signatures is None:
        return None, "history: " + str(error)
    successful = [item for item in signatures if not item.get("err")]
    create_index = next(
        (
            index for index, item in enumerate(successful)
            if item.get("signature") == birth["create_sig"]
        ),
        None,
    )
    if create_index is not None:
        return successful[create_index + 1:create_index + 1 + history_limit], None
    return [
        item for item in successful
        if item.get("slot", birth["create_slot"]) < birth["create_slot"]
    ][:history_limit], None


def inspect_creator_live(
    rpc,
    birth,
    history_limit,
    deadline_seconds,
    clock=time.monotonic,
):
    """Resolve the newest confirmed direct SOL funding edge before creation."""
    out = {
        "status": "unresolved",
        "transactions_checked": 0,
        "funding_sig": None,
        "funding_slot": None,
        "slots_before_create": None,
        "funders": [],
        "min_funding_lamports": F.MIN_FUNDING_LAMPORTS,
        "dust_sources_skipped": 0,
        "dust_lamports_skipped": 0,
        "error": None,
        "commitment": "confirmed",
    }
    started = clock()

    def remaining():
        return deadline_seconds - (clock() - started)

    left = remaining()
    if left <= 0:
        out["status"] = "lookup_deadline_exceeded"
        return out
    signatures, error = live_signatures_before(
        rpc, birth, history_limit, timeout=max(0.05, left)
    )
    if error:
        out["error"] = error
        return out
    for item in signatures:
        left = remaining()
        if left <= 0:
            out["status"] = "lookup_deadline_exceeded"
            return out
        tx, error = rpc.call(
            "getTransaction",
            [
                item["signature"],
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 1,
                    "commitment": "confirmed",
                },
            ],
            tries=1,
            timeout=max(0.05, left),
        )
        if error or tx is None:
            out["error"] = "transaction: " + str(error)
            return out
        out["transactions_checked"] += 1
        funders, _signers, dust_n, dust_lamports = F.direct_funders(
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
        })
        return out
    out["status"] = "no_direct_funder_in_history"
    return out


def load_scores(path):
    scores = {}
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("identity_type") != "funder":
                continue
            funder = row.get("funder")
            if funder:
                scores[funder] = row
    return scores


def candidate_pairs(record):
    slot = record.get("slot")
    if not isinstance(slot, int):
        return []
    creates = {
        event["mint"]: event
        for event in record.get("processed_create_events") or []
        if event.get("mint") and event.get("creator")
    }
    return [
        (trade, creates.get(trade.get("mint")))
        for trade in record.get("processed_trade_events") or []
        if trade.get("birth_candidate")
    ]


def safety_base(record, trade, create):
    return {
        "observed_slot": record.get("slot"),
        "signature": record.get("signature"),
        "mint": trade.get("mint"),
        "creator": create.get("creator") if create else None,
        "event_user": create.get("user") if create else None,
        "capital_enabled": False,
        "transaction_built": False,
        "transaction_signed": False,
        "transaction_submitted": False,
    }


def record_decisions(
    record,
    scores,
    rpc,
    history_limit,
    max_age_slots,
    lookup_deadline_seconds,
    resolver=inspect_creator_live,
    clock=time.monotonic,
):
    decisions = []
    for trade, create in candidate_pairs(record):
        base = safety_base(record, trade, create)
        if create is None:
            decisions.append({
                **base,
                "decision": "reject_missing_create_identity",
            })
            continue
        if not isinstance(record.get("signature"), str):
            decisions.append({
                **base,
                "decision": "reject_missing_creation_signature",
            })
            continue

        birth = {
            "mint": trade["mint"],
            "creator": create["creator"],
            "create_sig": record.get("signature"),
            "create_slot": record["slot"],
            "window": "live",
        }
        started = clock()
        edge = resolver(
            rpc,
            birth,
            history_limit,
            lookup_deadline_seconds,
            clock,
        )
        elapsed = clock() - started
        enriched = {
            **base,
            "funding_lookup_seconds": elapsed,
            "funding_lookup_deadline_seconds": lookup_deadline_seconds,
            "funding_status": edge.get("status"),
            "funding_transactions_checked": edge.get(
                "transactions_checked", 0
            ),
        }
        if edge.get("error"):
            decisions.append({
                **enriched,
                "decision": "reject_funding_lookup_error",
                "funding_error": edge["error"],
            })
            continue
        if edge.get("status") == "lookup_deadline_exceeded":
            decisions.append({
                **enriched,
                "decision": "reject_funding_lookup_deadline",
            })
            continue
        if edge.get("status") != "direct_funder_found":
            decisions.append({
                **enriched,
                "decision": "reject_no_direct_funder",
            })
            continue
        funders = edge.get("funders") or []
        if not funders or not funders[0].get("is_signer"):
            decisions.append({
                **enriched,
                "decision": "reject_non_signer_funding_source",
                "funder": funders[0].get("address") if funders else None,
            })
            continue

        primary = funders[0]
        funder = primary["address"]
        funded = {
            **enriched,
            "funder": funder,
            "funding_sig": edge.get("funding_sig"),
            "funding_slot": edge.get("funding_slot"),
            "funding_sol": primary.get("lamports", 0) / 1e9,
            "funding_slots_before_create": edge.get("slots_before_create"),
        }
        if elapsed > lookup_deadline_seconds:
            decisions.append({
                **funded,
                "decision": "reject_funding_lookup_deadline",
            })
            continue
        score = scores.get(funder)
        if score is None:
            decisions.append({
                **funded,
                "decision": "reject_unknown_funder",
            })
            continue
        as_of = score.get("as_of_slot")
        if not isinstance(as_of, int) or as_of >= record["slot"]:
            decisions.append({
                **funded,
                "decision": "reject_noncausal_score",
                "score_as_of_slot": as_of,
            })
            continue
        age = record["slot"] - as_of
        if age > max_age_slots:
            decisions.append({
                **funded,
                "decision": "reject_stale_score",
                "score_as_of_slot": as_of,
                "score_age_slots": age,
            })
            continue
        decisions.append({
            **funded,
            "decision": (
                "shadow_candidate" if score.get("allow")
                else "reject_graph_score"
            ),
            "score_as_of_slot": as_of,
            "score_age_slots": age,
            "score_reason": score.get("reason"),
            "score_model": score.get("model"),
        })
    return decisions


def run(
    input_path,
    scores_path,
    output_path,
    rpc,
    history_limit,
    max_age_slots,
    lookup_deadline_seconds,
    follow=False,
):
    scores = load_scores(scores_path)
    with open(input_path) as source, open(output_path, "a") as sink:
        while True:
            line = source.readline()
            if not line:
                if not follow:
                    break
                time.sleep(0.1)
                continue
            record = json.loads(line)
            for decision in record_decisions(
                record,
                scores,
                rpc,
                history_limit,
                max_age_slots,
                lookup_deadline_seconds,
            ):
                sink.write(json.dumps(decision, sort_keys=True) + "\n")
                sink.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--scores", required=True)
    parser.add_argument(
        "--output", default="data/pumpfundingshadow.jsonl"
    )
    parser.add_argument("--history-limit", type=int, default=50)
    parser.add_argument("--max-age-slots", type=int, default=216_000)
    parser.add_argument("--lookup-deadline-seconds", type=float, default=2.0)
    parser.add_argument("--qps", type=float, default=8.0)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()

    url = os.environ.get(
        "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
    ).strip()
    rpc = B.Rpc(url, min_interval=1.0 / max(args.qps, 0.1))
    run(
        args.input,
        args.scores,
        args.output,
        rpc,
        args.history_limit,
        args.max_age_slots,
        args.lookup_deadline_seconds,
        follow=args.follow,
    )


if __name__ == "__main__":
    main()
