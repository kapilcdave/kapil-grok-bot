import pumpfunderindependence as I


def row(slot, creator, funder="f", signature="s", roi=0.1):
    return {
        "slot": slot,
        "mint": str(slot),
        "creator": creator,
        "funder": funder,
        "funding_sig": signature,
        "funding_slot": slot - 10,
        "window": 8,
        "roi": roi,
    }


def test_first_by_keeps_earliest_launch():
    rows = [row(2, "c"), row(1, "c"), row(3, "d")]
    got = I.first_by(rows, lambda value: value["creator"])
    assert [(value["creator"], value["slot"]) for value in got] == [
        ("c", 1),
        ("d", 3),
    ]


def test_equal_funder_mean_does_not_launch_weight():
    rows = [
        row(1, "a", funder="many", roi=1.0),
        row(2, "b", funder="many", roi=1.0),
        row(3, "c", funder="one", roi=-1.0),
    ]
    assert I.equal_funder_mean(rows) == 0.0


def test_primary_gate_requires_funder_cluster_ci():
    result = {
        "top_b": {
            "n": 30,
            "windows": 6,
            "mean_roi": 0.1,
            "clustered_ci": [0.01, 0.2],
        },
        "top_vs_population_ci": [0.01, 0.2],
        "funder_clustered_ci": [-0.01, 0.2],
    }
    assert not I.primary_pass(result)


def test_selected_only_cache_detects_stale_false_positive():
    rows = [
        row(100, "c", funder="selected", signature="a"),
        row(200, "c", funder="other", signature="b"),
    ]
    got = I.selected_only_cache_replay(rows, {8}, {"selected"})
    assert got["true_positive"] == 1
    assert got["false_positive"] == 1
    assert got["requires_later_funding_invalidation"] is True
