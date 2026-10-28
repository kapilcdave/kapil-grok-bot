"""Self-tests for the deployer-latency reachability sweep."""
import pumpcurve as C
import pumpdeplat as L
import pumporacle as O


def _curve(slot, vsol, vtok, sig="s", user="u", bt=0.0):
    return {"slot": slot, "vsol": vsol, "vtok": vtok, "sig": sig,
            "user": user, "bt": bt}


def _mint(prices):
    """prices: list of (slot, vsol) rising reserves on a constant product."""
    tape = [_curve(slot, vsol, int(1e15 / vsol * 1e9), sig=f"s{i}")
            for i, (slot, vsol) in enumerate(prices)]
    return {"tape": tape, "end_slot": tape[-1]["slot"] + 10_000,
            "end_ts": None}


def test_later_entry_buys_a_worse_price_on_a_rising_curve():
    v = _mint([(100, 30e9), (101, 60e9), (105, 120e9)])
    fast = L.simulate_at(v, 0.0095, 0.0, 0.05, 0)
    slow = L.simulate_at(v, 0.0095, 0.0, 0.05, 5)
    assert fast is not None and slow is not None
    assert slow < fast


def test_entry_delay_uses_the_last_state_at_or_before_the_deadline():
    v = _mint([(100, 30e9), (101, 60e9), (105, 120e9)])
    index, row = L.A.entry_of(v, 1)
    assert index == 1 and row["slot"] == 101
    index, row = L.A.entry_of(v, 4)
    assert index == 1, "a slot with no trade must not advance the entry"


def test_simulate_at_returns_none_when_the_window_ends_too_early():
    v = _mint([(100, 30e9), (101, 60e9)])
    v["end_slot"] = 102
    assert L.simulate_at(v, 0.0095, 0.0, 0.05, 0) is None


def test_magnitude_scales_with_clip_and_divides_by_windows():
    rows = [{"roi": 0.2}, {"roi": 0.4}]
    small = L.magnitude(rows, 0.05, 8)
    big = L.magnitude(rows, 0.5, 8)
    assert abs(small["sol_per_launch"] - 0.3 * 0.05) < 1e-12
    assert abs(big["sol_per_launch"] / small["sol_per_launch"] - 10) < 1e-9
    assert abs(small["sol_per_window"] - 0.3 * 0.05 * 2 / 8) < 1e-12


def test_magnitude_is_none_without_rows_or_windows():
    assert L.magnitude([], 0.05, 8) is None
    assert L.magnitude([{"roi": 0.2}], 0.05, 0) is None


def test_the_latency_grid_spans_the_retail_floor():
    assert L.RETAIL_FLOOR_SLOTS in L.LATENCY_SLOTS
    assert max(L.LATENCY_SLOTS) * O.SLOT_SEC >= 10, "sweep must reach 10s"
    assert C.LAMPORTS == 1e9
