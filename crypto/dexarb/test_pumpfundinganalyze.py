import pumpfundinganalyze as A


def row(slot, mint, creator="c", funder="f"):
    return {
        "slot": slot,
        "mint": mint,
        "creator": creator,
        "funder": funder,
    }


def test_causal_burst_excludes_future_creations():
    rows = [
        row(100, "a", creator="c1"),
        row(105, "b", creator="c2"),
        row(130, "c", creator="c3"),
    ]
    got = A.add_causal_burst(rows, lookback_slots=20)
    assert [r["burst_count"] for r in got] == [1, 2, 1]


def test_causal_burst_counts_launches_and_reports_distinct_creators():
    rows = [
        row(100, "a", creator="same"),
        row(101, "b", creator="same"),
        row(102, "c", creator="other"),
    ]
    got = A.add_causal_burst(rows)
    assert [r["burst_count"] for r in got] == [1, 2, 3]
    assert [r["burst_distinct_creators"] for r in got] == [1, 1, 2]
