import pumpfunderreach as R


def tape(slots):
    rows = [
        {
            "slot": slot,
            "sig": signature,
            "vsol": 30_000_000_000 + i * 100_000_000,
            "vtok": 1_000_000_000_000 - i * 1_000_000_000,
            "ts": 1_000 + i,
            "bt": 1_000,
        }
        for i, (slot, signature) in enumerate(slots)
    ]
    return {
        "tape": rows,
        "end_slot": rows[-1]["slot"] + 1_000,
        "end_ts": rows[-1]["ts"] + 1_000,
    }


def test_same_slot_ahead_starts_after_last_birth_signature_event():
    value = tape([
        (100, "birth"),
        (100, "birth"),
        (100, "later-1"),
        (100, "later-1"),
        (100, "later-2"),
        (101, "next-slot"),
    ])
    assert R.entry_after_up_to_same_slot_trades(value, 0)[0] == 1
    assert R.entry_after_up_to_same_slot_trades(value, 1)[0] == 3
    assert R.entry_after_up_to_same_slot_trades(value, 2)[0] == 4
    assert R.entry_after_up_to_same_slot_trades(value, 3)[0] == 4


def test_same_slot_ahead_does_not_require_future_competitor():
    value = tape([
        (100, "birth"),
        (101, "next-slot"),
    ])
    index, row = R.entry_after_up_to_same_slot_trades(value, 2)
    assert index == 0
    assert row["slot"] == 100


def test_seat_gate_requires_all_frozen_conditions():
    seat = {
        "top_b": {
            "n": 50,
            "windows": 6,
            "mean_roi": 0.1,
            "clustered_ci": [0.01, 0.2],
        },
        "top_vs_population_ci": [0.01, 0.2],
        "top_vs_bottom_ci": [0.01, 0.2],
    }
    assert R.seat_pass(seat)
    seat["top_b"]["clustered_ci"][0] = -0.01
    assert not R.seat_pass(seat)
