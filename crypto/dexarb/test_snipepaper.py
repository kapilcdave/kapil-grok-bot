import json

import snipepaper as P


def metrics(**over):
    base = {"trades": 20, "uniq_wallets": 10, "buy_ratio": 0.8, "net_sol": 1.0,
            "ssr": 0.1, "mcap_sol": 50.0, "creator_initial_buy_sol": 0.1,
            "creator_sold_in_window": False}
    base.update(over)
    return base


def test_gate_passes_a_clean_candidate():
    ok, why = P.gate_verdict(metrics())
    assert ok and why == []


def test_gate_reports_every_failing_reason_not_the_first():
    # The pass rate caps the sample, so it has to be attributable to all of its
    # causes, not to whichever test happens to run first.
    ok, why = P.gate_verdict(metrics(trades=2, uniq_wallets=1, net_sol=-1.0))
    assert not ok
    assert set(why) >= {"trades", "uniq_wallets", "net_sol"}


def test_undefined_ssr_fails_and_never_passes():
    # No buy volume means SSR is undefined. Letting it read as "no selling"
    # would invert the test and admit exactly the dead tokens it excludes.
    ok, why = P.gate_verdict(metrics(ssr=None))
    assert not ok and "ssr" in why


def test_mcap_band_is_two_sided():
    assert not P.gate_verdict(metrics(mcap_sol=27.9))[0]      # below the band
    assert not P.gate_verdict(metrics(mcap_sol=900.0))[0]     # above it


def create(mint="m", ts=1000, v_sol=30.0, v_tok=1_073_000_000.0, creator="c"):
    return {"t": "create", "mint": mint, "ts": ts, "recv": ts, "v_sol": v_sol,
            "v_tok": v_tok, "creator": creator, "name": "N", "symbol": "S",
            "uri": "ipfs://x", "slot": 1, "sig": "sig"}


def trade(ts, side="buy", sol=1.0, user="u", v_sol=31.0, v_tok=1.04e9, mint="m"):
    return {"t": "trade", "mint": mint, "ts": ts, "side": side, "sol": sol,
            "tok": 1000.0, "user": user, "v_sol": v_sol, "v_tok": v_tok}


def test_metrics_use_only_the_window_so_a_replay_cannot_peek():
    watch = P.Watch(create())
    for i in range(5):
        watch.on_trade(trade(1000 + i, user=f"u{i}"))
    # Trades after the window must not reach the decision.
    for i in range(50):
        watch.on_trade(trade(1100 + i, user=f"late{i}"))
    m = watch.metrics(30.0)
    assert m["trades"] == 5 and m["uniq_wallets"] == 5


def test_creator_sell_inside_the_window_is_detected():
    watch = P.Watch(create(creator="boss"))
    watch.on_trade(trade(1001, side="sell", user="boss"))
    assert watch.metrics(30.0)["creator_sold_in_window"] is True


def test_entry_charges_fee_impact_and_gas_separately():
    entry = P.price_entry(30.0, 1_073_000_000.0, 1.0, 100.0, 0.0001)
    assert abs(entry["fee_sol"] - 0.01) < 1e-12
    assert abs(entry["sol_to_curve"] - 0.99) < 1e-12
    assert abs(entry["cost_sol"] - 1.0001) < 1e-12
    # The avg fill is worse than the pre-trade mid: that is our own impact.
    assert entry["avg_price"] > entry["mid_price_at_entry"]


def test_an_immediate_round_trip_loses_exactly_the_friction():
    """The floor every arm pays before the price does anything at all.

    It is NOT a constant: it is two 100 bps legs plus two gas charges plus our
    own round-trip impact, and the impact term grows with clip/depth. So the
    assertion is on the decomposition, not on a magic number.
    """
    for clip in (0.1, 0.25, 1.0, 5.0):
        entry = P.price_entry(30.0, 1_073_000_000.0, clip, 100.0, 0.0001)
        out = P.price_exit(entry, entry["v_sol_at_entry"],
                           entry["v_tok_at_entry"], 100.0, 0.0001)
        fees_only = -(2 * 100.0 / 1e4) - 2 * 0.0001 / clip
        # Strictly worse than fees alone, because impact is charged twice.
        assert out["ret"] < fees_only
    # And it gets worse with the clip: depth is a cost, not a scale factor.
    # Measured on a fresh 30 SOL curve: 0.1 -> -2.83%, 0.25 -> -3.66%,
    # 1.0 -> -8.08%, 5.0 -> -26.31%. A 5 SOL clip is 17% of the pool, so the
    # impact term, not the fee, is what sizing has to respect.
    rets = []
    for clip in (0.1, 0.25, 1.0, 5.0):
        entry = P.price_entry(30.0, 1_073_000_000.0, clip, 100.0, 0.0001)
        rets.append(P.price_exit(entry, entry["v_sol_at_entry"],
                                 entry["v_tok_at_entry"], 100.0,
                                 0.0001)["ret"])
    assert rets == sorted(rets, reverse=True)


def test_mark_uses_the_standing_state_not_the_next_print():
    watch = P.Watch(create())
    watch.on_trade(trade(1005, v_sol=40.0, v_tok=0.9e9))
    entry_state = {"ts": 1030, "v_sol": 30.0, "v_tok": 1.073e9}
    # No print between 1030 and 1045: the standing state is still the entry's.
    got = P.state_at(watch, 1, 1045, entry_state)
    assert got is entry_state
    # A print before the horizon becomes the standing state.
    watch.on_trade(trade(1040, v_sol=50.0, v_tok=0.8e9))
    got = P.state_at(watch, 1, 1045, entry_state)
    assert got["v_sol"] == 50.0
    # Nothing after the horizon is ever read.
    watch.on_trade(trade(1050, v_sol=99.0, v_tok=0.5e9))
    assert P.state_at(watch, 1, 1045, entry_state)["v_sol"] == 50.0


def test_quiet_token_is_marked_not_dropped():
    # A position with no print after entry is still exitable into the standing
    # curve. Returning None here would delete exactly the stuck rows.
    watch = P.Watch(create())
    for i in range(20):
        watch.on_trade(trade(1001 + i, user=f"u{i}"))
    row = {"entry": P.price_entry(31.0, 1.04e9, 0.25, 100.0, 0.0001),
           "window_s": 30.0, "mint": "m"}

    class Args:
        fee_bps, gas_sol = 100.0, 0.0001
    P.finalise(row, watch, Args())
    assert row["no_print_after_entry"] is True
    assert all(row["marks"][str(h)] is not None for h in P.HORIZONS)
    # Every horizon marks at the same stuck state, i.e. pure friction.
    rets = {row["marks"][str(h)]["ret"] for h in P.HORIZONS}
    assert len(rets) == 1 and -0.05 < rets.pop() < -0.02
    assert row["marks"]["60"]["staleness_s"] == 60.0


def test_oracle_is_an_upper_bound_over_every_mark():
    watch = P.Watch(create())
    for i in range(3):
        watch.on_trade(trade(1001 + i, user=f"u{i}"))
    watch.on_trade(trade(1035, v_sol=60.0, v_tok=0.7e9))   # a spike after entry
    row = {"entry": P.price_entry(31.0, 1.04e9, 0.25, 100.0, 0.0001),
           "window_s": 30.0, "mint": "m"}

    class Args:
        fee_bps, gas_sol = 100.0, 0.0001
    P.finalise(row, watch, Args())
    best = max(m["ret"] for m in row["marks"].values())
    assert row["oracle"]["ret"] >= best - 1e-12


class StubDecider(P.Decider):
    def __init__(self, verdict):
        super().__init__(None, None, 1.0)
        self.verdict = verdict

    def decide(self, payload):
        return self.verdict


def test_an_unavailable_decider_never_accepts():
    watch = P.Watch(create())
    # A passing candidate needs a defined SSR, so it needs at least one sell,
    # and an mcap inside [30, 250] SOL, so the curve has to have been bought up.
    for i in range(20):
        watch.on_trade(trade(1001 + i, user=f"u{i}", sol=2.0,
                             v_sol=35.0, v_tok=0.95e9))
    watch.on_trade(trade(1025, side="sell", sol=0.5, user="u0",
                         v_sol=35.0, v_tok=0.95e9))

    class Args:
        gate = P.GATE
        clip_sol, fee_bps, gas_sol = 0.25, 100.0, 0.0001
    row = P.build_row(watch, Args(), StubDecider(
        {"available": False, "accept": True, "reason": "decider_degenerate"}),
        30.0, 1030)
    assert row["gate_passed"] is True
    # `accept: True` from an unavailable instrument must not reach the arm.
    assert row["arms"]["gate_decide"] is False
    assert row["capital_enabled"] is False


def test_row_is_capital_disabled_by_construction():
    """No transaction path, checked on the CODE and not on the prose.

    Scanning the raw text matches the docstring's own "no signer", so the check
    is run over the parsed module: identifiers, attributes and imports only.
    """
    import ast
    tree = ast.parse(open(P.__file__).read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
            names.update(a.name for a in node.names)
    for forbidden in ("Keypair", "send_transaction", "sendTransaction",
                      "sign", "sign_message", "private_key", "signer",
                      "solana", "solders", "Transaction"):
        assert forbidden not in names, forbidden
