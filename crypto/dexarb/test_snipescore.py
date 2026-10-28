import json

import snipescore as S


def row(mint, ts, ret, accept, available=True, thin=False, nonstd=False):
    return {"mint": mint, "created_ts": ts, "gate_passed": True,
            "entry": {"tokens": 1.0}, "decider_available": available,
            "decider_accept": accept, "text_thin": thin,
            "nonstandard_curve": nonstd, "desc_chars": 0 if thin else 50,
            "metrics": {"mcap_sol": 50.0, "net_sol": 1.0, "trades": 20,
                        "uniq_wallets": 10},
            "arms": {"gate": True, "gate_decide": bool(available and accept)},
            "marks": {h: {"ret": ret} for h in S.HORIZONS},
            "oracle": {"ret": ret + 0.1}}


def test_roll_preserves_turnover_exactly():
    # This is the whole point of a roll: the null pays the same fees on the same
    # number of trades, so the comparison is selection and not turnover.
    accepts = [True, False, True, True, False, False, True]
    for k in range(1, 20):
        rolled = S.roll(accepts, k)
        assert len(rolled) == len(accepts)
        assert sum(rolled) == sum(accepts)


def test_roll_of_zero_is_the_identity():
    accepts = [True, False, True]
    assert S.roll(accepts, 0) == accepts
    assert S.roll(accepts, len(accepts)) == accepts


def test_declined_rows_count_as_zero_not_as_absent():
    rows = [row("a", 0, -0.5, True), row("b", 1, -0.5, False)]
    accepts = [bool(r["arms"]["gate_decide"]) for r in rows]
    got = S.arm_returns(rows, accepts, "60")
    assert got == [-0.5, 0.0]


def test_a_skip_everything_arm_beats_a_losing_arm_on_g3():
    """The artefact G3 is demoted for, asserted so it cannot come back quietly.

    Every row loses, the decider declines every row, and so the unconfounded
    reading is "this arm did nothing" while G3 reads as a large win.
    """
    rows = [row(str(i), i * 3600, -0.4, False) for i in range(8)]
    out = S.summarise(rows, "60", "t")
    assert out["n_accept_decide"] == 0
    assert out["gate"]["mean"] < -0.3
    assert out["paired_decide_minus_gate"]["mean"] > 0.3     # the artefact
    # And at matched turnover there is nothing: a roll of an all-reject vector
    # is still all-reject, so selection is exactly zero.
    for null in out["roll_nulls"]:
        assert abs(null["vs_real"]["mean"]) < 1e-12


def test_d2_refuses_a_degenerate_accept_set():
    rows = [row(str(i), i, -0.1, False) for i in range(6)]
    got = S.d2_one_line_rules(rows)
    assert got["reducible"] is None and got["best"] is None
    assert "degenerate" in got["note"]


def test_d2_finds_a_one_line_rule_when_one_exists():
    # The decider "accepts" exactly the rows with text. A single feature
    # reproduces that perfectly, so the readout is the rule, not the model.
    rows = [row(str(i), i, -0.1, i % 2 == 0, thin=(i % 2 == 1))
            for i in range(10)]
    got = S.d2_one_line_rules(rows)
    assert got["reducible"] is True
    assert got["best"]["agreement"] == 1.0


def test_single_block_yields_no_ci_rather_than_a_fake_one():
    # Every row in one UTC hour shares one regime. A CI over it is a fiction.
    rows = [row(str(i), 100 + i, -0.2, True) for i in range(20)]
    out = S.summarise(rows, "60", "t")
    assert out["gate"]["lo"] is None
    assert "no CI" in out["gate"]["note"]


def test_blocks_are_utc_hours():
    a = S.hour_block({"created_ts": 1791237379})
    b = S.hour_block({"created_ts": 1791237379 + 3600})
    assert a != b and a.count("-") == 2 and "T" in a


def test_a_real_selection_effect_survives_the_roll():
    # The decider picks the winners. Rolling breaks the pairing, so real-minus-
    # roll must be positive while turnover is identical.
    rows = []
    for i in range(24):
        good = i % 2 == 0
        rows.append(row(str(i), i * 3600, 0.5 if good else -0.5, good))
    out = S.summarise(rows, "60", "t")
    assert out["n_accept_decide"] == 12
    for null in out["roll_nulls"]:
        assert null["vs_real"]["mean"] > 0.1


def test_later_finalised_row_supersedes_the_gate_reject_row(tmp_path):
    path = tmp_path / "ledger.jsonl"
    early = {"mint": "m", "created_ts": 1, "gate_passed": True}
    late = dict(early, marks={"60": {"ret": -0.2}}, entry={"tokens": 1.0})
    path.write_text(json.dumps(early) + "\n" + json.dumps(late) + "\n")
    got = S.load(str(path))
    assert len(got) == 1 and "marks" in got[0]
