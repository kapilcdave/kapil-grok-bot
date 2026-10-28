"""Fail-closed, capital-disabled creator-graph shadow gate.

Consumes JSONL emitted by memecoin's `websocket_probe`, joins each verified
birth candidate to a precomputed creator score, and writes shadow decisions.
It has no RPC client, keypair, transaction builder, signer or submission path.

Score rows are JSONL:

    {"creator": "...", "as_of_slot": 123, "allow": true,
     "reason": "frozen-rule-id"}

The score must predate the observed birth slot. Unknown, future-dated and stale
scores fail closed.
"""
import argparse
import json
import time


def load_scores(path):
    scores = {}
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("identity_type", "creator") != "creator":
                continue
            scores[row["creator"]] = row
    return scores


def record_decisions(record, scores, max_age_slots):
    slot = record.get("slot")
    signature = record.get("signature")
    if not isinstance(slot, int):
        return []
    creates = {
        event["mint"]: event
        for event in record.get("processed_create_events") or []
        if event.get("mint") and event.get("creator")
    }
    decisions = []
    for trade in record.get("processed_trade_events") or []:
        if not trade.get("birth_candidate"):
            continue
        mint = trade.get("mint")
        create = creates.get(mint)
        base = {
            "observed_slot": slot,
            "signature": signature,
            "mint": mint,
            "creator": create.get("creator") if create else None,
            "event_user": create.get("user") if create else None,
            "capital_enabled": False,
            "transaction_built": False,
            "transaction_signed": False,
            "transaction_submitted": False,
        }
        if create is None:
            decisions.append({
                **base,
                "decision": "reject_missing_create_identity",
            })
            continue
        score = scores.get(create["creator"])
        if score is None:
            decisions.append({
                **base,
                "decision": "reject_unknown_creator",
            })
            continue
        as_of = score.get("as_of_slot")
        if not isinstance(as_of, int) or as_of >= slot:
            decisions.append({
                **base,
                "decision": "reject_noncausal_score",
                "score_as_of_slot": as_of,
            })
            continue
        age = slot - as_of
        if age > max_age_slots:
            decisions.append({
                **base,
                "decision": "reject_stale_score",
                "score_as_of_slot": as_of,
                "score_age_slots": age,
            })
            continue
        decisions.append({
            **base,
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


def run(input_path, scores_path, output_path, max_age_slots, follow=False):
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
            for decision in record_decisions(record, scores, max_age_slots):
                sink.write(json.dumps(decision, sort_keys=True) + "\n")
                sink.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--scores", required=True)
    parser.add_argument("--output", default="data/pumpgraphshadow.jsonl")
    parser.add_argument("--max-age-slots", type=int, default=216_000)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    run(
        args.input, args.scores, args.output,
        args.max_age_slots, follow=args.follow,
    )


if __name__ == "__main__":
    main()
