import json

import pumpgraphshadow as S


def record(slot=100):
    return {
        "slot": slot,
        "signature": "sig",
        "processed_create_events": [
            {"mint": "mint", "user": "user", "creator": "creator"}
        ],
        "processed_trade_events": [
            {"mint": "mint", "birth_candidate": True}
        ],
    }


def test_unknown_creator_fails_closed():
    got = S.record_decisions(record(), {}, 1000)
    assert got[0]["decision"] == "reject_unknown_creator"
    assert got[0]["capital_enabled"] is False


def test_causal_allowed_score_is_shadow_only():
    scores = {
        "creator": {
            "creator": "creator",
            "as_of_slot": 99,
            "allow": True,
            "reason": "frozen-v1",
        }
    }
    got = S.record_decisions(record(), scores, 1000)[0]
    assert got["decision"] == "shadow_candidate"
    assert got["transaction_built"] is False
    assert got["transaction_signed"] is False
    assert got["transaction_submitted"] is False


def test_same_slot_score_is_rejected_as_noncausal():
    scores = {
        "creator": {
            "creator": "creator",
            "as_of_slot": 100,
            "allow": True,
        }
    }
    assert (
        S.record_decisions(record(), scores, 1000)[0]["decision"]
        == "reject_noncausal_score"
    )


def test_stale_score_fails_closed():
    scores = {
        "creator": {
            "creator": "creator",
            "as_of_slot": 1,
            "allow": True,
        }
    }
    assert (
        S.record_decisions(record(), scores, 10)[0]["decision"]
        == "reject_stale_score"
    )


def test_creator_shadow_ignores_funder_keyed_scores(tmp_path):
    path = tmp_path / "scores.jsonl"
    path.write_text(json.dumps({
        "identity_type": "funder",
        "funder": "funder",
        "identity": "funder",
        "as_of_slot": 99,
        "allow": True,
    }) + "\n")
    assert S.load_scores(path) == {}
