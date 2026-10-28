import pumpdeployer as D


def row(window, deployer, roi):
    return {"window": window, "deployer": deployer, "roi": roi}


def test_rankdata_averages_ties():
    assert D.rankdata([3, 1, 1, 2]) == [3.0, 0.5, 0.5, 2.0]


def test_spearman_extremes():
    assert abs(D.spearman([1, 2, 3], [10, 20, 30]) - 1.0) < 1e-12
    assert abs(D.spearman([1, 2, 3], [30, 20, 10]) + 1.0) < 1e-12


def test_select_uses_only_a_and_tails():
    state = {
        "a": {"a": [row(0, "a", -2), row(1, "a", -2)], "b": [row(8, "a", 99)]},
        "b": {"a": [row(0, "b", -1), row(1, "b", -1)], "b": []},
        "c": {"a": [row(0, "c", 1), row(1, "c", 1)], "b": []},
        "d": {"a": [row(0, "d", 2), row(1, "d", 2)], "b": [row(8, "d", -99)]},
    }
    top, bottom, scores = D.select(state, min_a=2, top_share=0.25)
    assert top == {"d"}
    assert bottom == {"a"}
    assert scores["a"] == -2
    assert scores["d"] == 2


def test_clustered_contrast_preserves_window_pairing():
    left = [row(0, "a", 3), row(1, "a", 5)]
    right = [row(0, "b", 1), row(1, "b", 3)]
    lo, hi = D.clustered_contrast(left, right, draws=100, seed=1)
    assert lo == 2
    assert hi == 2


def test_selection_null_handles_no_selected_b_rows():
    state = {
        "a": {"a": [row(0, "a", 1)], "b": []},
        "b": {"a": [row(0, "b", 2)], "b": []},
    }
    population = [row(8, "population", 0)]
    got = D.selection_null(
        state,
        {"a": 1.0, "b": 2.0},
        population,
        top_share=0.5,
        draws=10,
        seed=1,
        observed=None,
    )
    assert got["draws"] == 0
    assert got["p95"] is None
