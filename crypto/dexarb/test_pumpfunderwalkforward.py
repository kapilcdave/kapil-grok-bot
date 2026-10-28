import argparse

import pumpfunderwalkforward as W


def test_fold_schedule_is_expanding_and_disjoint():
    previous = set()
    for train, test in W.FOLDS:
        assert previous <= train
        assert train.isdisjoint(test)
        assert max(train) < min(test)
        previous = train


def test_empty_fold_does_not_crash():
    args = argparse.Namespace(min_a=3, top_share=0.2, draws=10)
    result = W.fold_result([], [], [], {0}, {1}, args)
    assert result["top_b"] is None
    assert result["selected_top_funders"] == 0

