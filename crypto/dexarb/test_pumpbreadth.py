"""Self-tests: the features must measure concentration and must not read the future."""
import pumpbreadth as B
import pumpcurve as C

LAM = C.LAMPORTS


def ev(user, slot, buy=1, sol=1.0):
    return {"user": user, "slot": slot, "buy": buy, "sol": sol * LAM,
            "tok": 1, "fee": 0, "sig": "s%s%d" % (user, slot),
            "vsol": 30 * LAM, "vtok": 10 ** 15, "ts": slot, "bt": slot}


def test_concentration_is_sol_weighted_not_counted():
    # three buyers, one of them holding 98% of the SOL
    v = {"tape": [ev("dev", 100, sol=98.0), ev("a", 101, sol=1.0), ev("b", 101, sol=1.0)]}
    f = B.breadth_features(v, 2, {})
    assert f["n_buyers_pre"] == 3.0
    assert f["max_buyer_share_pre"] > 0.97
    assert f["dev_share_pre"] > 0.97
    assert f["buyer_hhi_pre"] > 0.95


def test_recurrence_ignores_wallets_first_seen_at_or_after_birth():
    v = {"tape": [ev("dev", 100), ev("old", 100), ev("new", 100)]}
    seen = {"dev": 100, "old": 50, "new": 100}   # only "old" predates birth
    f = B.breadth_features(v, 2, seen)
    assert abs(f["recurring_buyer_share_pre"] - 1 / 3) < 1e-9
    assert abs(f["recurring_sol_share_pre"] - 1 / 3) < 1e-9


def test_a_wallet_first_seen_later_is_never_recurring():
    v = {"tape": [ev("dev", 100), ev("future", 100)]}
    f = B.breadth_features(v, 1, {"dev": 100, "future": 999})
    assert f["recurring_buyer_share_pre"] == 0.0


def test_sells_do_not_enter_the_buy_concentration():
    v = {"tape": [ev("dev", 100, sol=1.0), ev("seller", 101, buy=0, sol=50.0)]}
    f = B.breadth_features(v, 1, {})
    assert f["n_buyers_pre"] == 1.0 and f["dev_share_pre"] == 1.0
