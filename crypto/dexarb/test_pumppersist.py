"""Self-tests for the persistence estimator: selection must use A only, and a
planted persistent wallet must be recovered while a one-off is not."""
import pumppersist as P


def row(window, user, roi, group="plus_1_slot", pnl=None):
    return {"window": window, "user": user, "roi": roi, "group": group,
            "pnl_sol": roi if pnl is None else pnl, "mint": "m%d" % window,
            "spent_sol": 1.0, "trades": 2, "first_order": 1}


def test_split_is_by_earliest_birth_slot():
    virgin = {(w, "m"): {"tape": [{"slot": 1000 - 10 * w}]} for w in range(4)}
    a, b = P.split_windows(virgin)
    assert a == {3, 2} and b == {1, 0}


def test_selection_uses_a_half_only():
    rows = []
    # winner in A, winner in B
    rows += [row(0, "good", 0.5) for _ in range(5)]
    rows += [row(1, "good", 0.4)]
    # loser in A, huge winner in B -- must NOT be selected
    rows += [row(0, "lucky_later", -0.9) for _ in range(5)]
    rows += [row(1, "lucky_later", 9.0)]
    state = P.per_wallet(rows, {0})
    chosen, eligible = P.select(state, 5, 0.5)
    assert chosen == {"good"}
    assert eligible == {"good", "lucky_later"}
    assert [r["roi"] for r in P.b_rows(state, chosen)] == [0.4]


def test_eligibility_threshold_excludes_short_histories():
    rows = [row(0, "thin", 1.0), row(1, "thin", 1.0)]
    state = P.per_wallet(rows, {0})
    chosen, eligible = P.select(state, 5, 0.5)
    assert eligible == set() and chosen == set()
