import json

import pumpfundingshadow as S


def record(slot=100):
    return {
        "slot": slot,
        "signature": "create-sig",
        "processed_create_events": [
            {"mint": "mint", "user": "user", "creator": "rotated-creator"}
        ],
        "processed_trade_events": [
            {"mint": "mint", "birth_candidate": True}
        ],
    }


def edge(
    *,
    status="direct_funder_found",
    funder="shared-funder",
    signer=True,
    error=None,
):
    return {
        "status": status,
        "transactions_checked": 2,
        "funders": [{
            "address": funder,
            "lamports": 1_000_000_000,
            "is_signer": signer,
        }],
        "funding_sig": "funding-sig",
        "funding_slot": 98,
        "slots_before_create": 2,
        "error": error,
    }


def resolver(returned):
    def resolve(_rpc, birth, history_limit, _deadline_seconds, _clock):
        assert birth["creator"] == "rotated-creator"
        assert birth["create_sig"] == "create-sig"
        assert history_limit == 50
        return returned

    return resolve


def clocks(start, end):
    values = iter((start, end))
    return lambda: next(values)


def score(allow=True, as_of=90):
    return {
        "identity_type": "funder",
        "funder": "shared-funder",
        "as_of_slot": as_of,
        "allow": allow,
        "model": "funder-v1",
    }


def decide(returned, scores=None, clock=None, deadline=2.0):
    return S.record_decisions(
        record(),
        scores or {},
        rpc=object(),
        history_limit=50,
        max_age_slots=1000,
        lookup_deadline_seconds=deadline,
        resolver=resolver(returned),
        clock=clock or clocks(1.0, 1.1),
    )[0]


def test_rotated_creator_joins_current_signer_funder_score():
    got = decide(edge(), {"shared-funder": score()})
    assert got["creator"] == "rotated-creator"
    assert got["funder"] == "shared-funder"
    assert got["decision"] == "shadow_candidate"
    assert got["capital_enabled"] is False
    assert got["transaction_built"] is False
    assert got["transaction_signed"] is False
    assert got["transaction_submitted"] is False


def test_late_positive_lookup_is_rejected():
    got = decide(
        edge(),
        {"shared-funder": score()},
        clock=clocks(1.0, 3.1),
        deadline=2.0,
    )
    assert got["decision"] == "reject_funding_lookup_deadline"


def test_non_signer_source_is_not_common_control_evidence():
    got = decide(edge(signer=False), {"shared-funder": score()})
    assert got["decision"] == "reject_non_signer_funding_source"


def test_unknown_funder_fails_closed():
    got = decide(edge())
    assert got["decision"] == "reject_unknown_funder"


def test_missing_creation_signature_fails_before_lookup():
    value = record()
    value["signature"] = None
    got = S.record_decisions(
        value,
        {},
        rpc=object(),
        history_limit=50,
        max_age_slots=1000,
        lookup_deadline_seconds=2.0,
        resolver=lambda *_args: (_ for _ in ()).throw(
            AssertionError("resolver should not run")
        ),
    )[0]
    assert got["decision"] == "reject_missing_creation_signature"


def test_loader_ignores_creator_scores(tmp_path):
    path = tmp_path / "scores.jsonl"
    rows = [
        {
            "identity_type": "creator",
            "creator": "creator",
            "as_of_slot": 90,
            "allow": True,
        },
        score(),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert list(S.load_scores(path)) == ["shared-funder"]


class HistoryRpc:
    def __init__(self, signatures):
        self.signatures = signatures

    def call(self, method, params, **_kwargs):
        assert method == "getSignaturesForAddress"
        assert params[0] == "rotated-creator"
        assert params[1]["commitment"] == "confirmed"
        return self.signatures, None


def test_live_history_uses_signature_boundary_when_birth_is_confirmed():
    signatures = [
        {"signature": "newer", "slot": 101},
        {"signature": "create-sig", "slot": 100},
        {"signature": "same-slot-older", "slot": 100},
        {"signature": "older", "slot": 99},
    ]
    got, error = S.live_signatures_before(
        HistoryRpc(signatures),
        {
            "creator": "rotated-creator",
            "create_sig": "create-sig",
            "create_slot": 100,
        },
        50,
    )
    assert error is None
    assert [row["signature"] for row in got] == [
        "same-slot-older", "older"
    ]


def test_live_history_drops_ambiguous_same_slot_when_birth_not_listed():
    signatures = [
        {"signature": "ambiguous", "slot": 100},
        {"signature": "older", "slot": 99},
    ]
    got, error = S.live_signatures_before(
        HistoryRpc(signatures),
        {
            "creator": "rotated-creator",
            "create_sig": "create-sig",
            "create_slot": 100,
        },
        50,
    )
    assert error is None
    assert [row["signature"] for row in got] == ["older"]
