import pumpcompound as P


def test_verified_virgin_requires_create_event():
    virgin = {
        (0, "good"): {"tape": [{"sig": "a"}]},
        (0, "bad"): {"tape": [{"sig": "b"}]},
    }
    audit = {
        ("good", "a"): {"status": "create_in_first_trade_tx"},
        ("bad", "b"): {"status": "prior_trade_proves_not_birth"},
    }
    assert set(P.verified_virgin(virgin, audit)) == {(0, "good")}


def test_graph_gate_uses_only_test_half_and_current_signer_funder():
    virgin = {
        (0, "train"): {"tape": [{"sig": "a", "slot": 1}]},
        (1, "good"): {"tape": [{"sig": "b", "slot": 2}]},
        (1, "wrong"): {"tape": [{"sig": "c", "slot": 2}]},
    }
    audit = {
        ("train", "a"): {
            "status": "create_in_first_trade_tx",
            "creator": "train-creator",
            "create_sig": "ca",
        },
        ("good", "b"): {
            "status": "create_in_first_trade_tx",
            "creator": "rotated",
            "create_sig": "cb",
        },
        ("wrong", "c"): {
            "status": "create_in_first_trade_tx",
            "creator": "other",
            "create_sig": "cc",
        },
    }
    funding = {
        ("good", "cb"): {
            "status": "direct_funder_found",
            "funders": [{
                "address": "allowed",
                "is_signer": True,
            }],
        },
        ("wrong", "cc"): {
            "status": "direct_funder_found",
            "funders": [{
                "address": "different",
                "is_signer": True,
            }],
        },
    }
    got = P.graph_gated_virgin(
        virgin, audit, funding, "funder", {"allowed"}
    )
    assert set(got) == {(1, "good")}


def test_graph_gate_with_no_authorized_source_has_no_trades():
    virgin = {(0, "a"): {"tape": [{"sig": "a", "slot": 1}]}}
    audit = {
        ("a", "a"): {
            "status": "create_in_first_trade_tx",
            "creator": "creator",
            "create_sig": "ca",
        }
    }
    assert P.graph_gated_virgin(
        virgin, audit, {}, None, set()
    ) == {}
