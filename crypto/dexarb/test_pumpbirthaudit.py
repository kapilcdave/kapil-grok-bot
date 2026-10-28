import struct

import pumpbirthaudit as A
import pumptape as T


def borsh_string(value):
    raw = value.encode()
    return struct.pack("<I", len(raw)) + raw


def test_parse_create_event_stable_prefix():
    keys = [bytes([i]) * 32 for i in range(1, 5)]
    body = (
        A.CREATE_EVENT_DISC
        + borsh_string("Coin")
        + borsh_string("C")
        + borsh_string("https://example.test")
        + b"".join(keys)
        + struct.pack("<q", 1_800_000_000)
    )
    got = A.parse_create_event(body)
    assert got["name"] == "Coin"
    assert got["symbol"] == "C"
    assert got["mint"] == T.b58encode(keys[0])
    assert got["bonding_curve"] == T.b58encode(keys[1])
    assert got["user"] == T.b58encode(keys[2])
    assert got["creator"] == T.b58encode(keys[3])
    assert got["timestamp"] == 1_800_000_000


def test_parse_create_event_rejects_short_data():
    assert A.parse_create_event(A.CREATE_EVENT_DISC + b"\x01") is None


def test_summary_distinguishes_boundary_and_creator():
    rows = [
        {
            "status": "prior_trade_proves_not_birth",
            "offset_slots": 0,
            "creator": None,
            "state_user": "x",
            "error": None,
        },
        {
            "status": "create_precedes_first_trade",
            "offset_slots": 50,
            "creator": "c",
            "state_user": "u",
            "error": None,
        },
        {
            "status": "create_in_first_trade_tx",
            "offset_slots": 60,
            "creator": "same",
            "state_user": "same",
            "error": None,
        },
    ]
    got = A.summarize(rows)
    assert got["false_birth_rate_resolved"] == 1 / 3
    assert got["boundary_lt_20_slots"]["false_rate_resolved"] == 1
    assert got["interior_ge_20_slots"]["false_rate_resolved"] == 0
    assert got["creator_equals_first_trade_user_share"] == 0.5
