import ast
import json

import snipepaper as P
import snipewallet as W


def create(mint="m", ts=1000, creator="c", sig="csig"):
    return {"t": "create", "mint": mint, "ts": ts, "recv": ts, "v_sol": 30.0,
            "v_tok": 1_073_000_000.0, "creator": creator, "name": "N",
            "symbol": "S", "uri": "ipfs://x", "slot": 1, "sig": sig}


def trade(ts, side="buy", user="u", v_sol=31.0, v_tok=1.04e9, mint="m"):
    return {"t": "trade", "mint": mint, "ts": ts, "side": side, "sol": 0.5,
            "tok": 1000.0, "user": user, "v_sol": v_sol, "v_tok": v_tok}


def transfer_tx(src, dst, lamports, err=None):
    return {"meta": {"err": err, "innerInstructions": []},
            "transaction": {"message": {"instructions": [
                {"program": "system", "parsed": {
                    "type": "transfer",
                    "info": {"source": src, "destination": dst,
                             "lamports": lamports}}}]}}}


class FakeRpc:
    """Address -> signature list, signature -> transaction. Counts calls.
    Anything named in `fail` raises, as a rate-limited endpoint would."""

    def __init__(self, sigs, txs, fail=()):
        self.sigs, self.txs, self.fail = sigs, txs, set(fail)
        self.calls = 0

    def signatures(self, address, limit, before=None):
        self.calls += 1
        if address in self.fail:
            raise W.RpcFailed(address)
        return self.sigs.get(address, [])[:limit]

    def transaction(self, signature):
        self.calls += 1
        if signature in self.fail:
            raise W.RpcFailed(signature)
        return self.txs.get(signature)


# ----------------------------------------------------------------- registry
def test_registry_never_reads_a_prior_launch_past_the_decision_time():
    reg = W.Registry()
    reg.on_create(create(mint="old", ts=100, creator="c"))
    reg.on_trade(trade(110, mint="old", v_sol=40.0, v_tok=0.8e9))
    # Both of these happen AFTER the decision at ts=500 and must not be seen.
    reg.on_trade(trade(600, mint="old", v_sol=90.0, v_tok=0.35e9))
    reg.on_trade(trade(601, mint="old", side="sell", user="c"))
    prior = reg.prior("c", 500)
    assert len(prior) == 1
    assert prior[0]["creator_sold"] is False
    assert abs(prior[0]["peak_mcap_sol"] - W.mcap_sol(40.0, 0.8e9)) < 1e-6
    # And the candidate's own launch is never its own history.
    assert reg.prior("c", 100) == []


def test_incoming_funders_applies_the_dust_floor_and_skips_failed_txs():
    assert W.incoming_funders(transfer_tx("f", "c", 4_999_999), "c") == []
    assert W.incoming_funders(transfer_tx("f", "c", 9_000_000), "c") == \
        [("f", 9_000_000)]
    assert W.incoming_funders(transfer_tx("f", "c", 9_000_000, err="x"),
                              "c") == []
    # Outgoing from the address is not funding it.
    assert W.incoming_funders(transfer_tx("c", "x", 9_000_000), "c") == []


# ------------------------------------------------------------------- finder
def world():
    sigs = {
        "c": [{"signature": "c1", "blockTime": 900, "err": None},
              {"signature": "c0", "blockTime": 400, "err": None}],
        "b_fresh_linked": [{"signature": "b1", "blockTime": 990, "err": None}],
        "b_fresh": [{"signature": "b2", "blockTime": 990, "err": None}],
        "b_old": [{"signature": f"o{i}", "blockTime": 1, "err": None}
                  for i in range(40)],
    }
    txs = {
        "c1": transfer_tx("FUNDER", "c", 2_000_000_000),
        "b1": transfer_tx("FUNDER", "b_fresh_linked", 50_000_000),
        "b2": transfer_tx("stranger", "b_fresh", 50_000_000),
    }
    return FakeRpc(sigs, txs)


def test_finder_reads_creator_funder_fanout_and_buyer_links():
    reg = W.Registry()
    reg.note_funder("FUNDER", "other1")
    reg.note_funder("FUNDER", "other2")
    reg.on_create(create(mint="prev", ts=500, creator="c"))
    finder = W.WalletFinder(world(), budget_s=5.0)
    rows = [trade(1001, user="c"), trade(1002, user="b_fresh_linked"),
            trade(1003, user="b_fresh"), trade(1004, user="b_old"),
            trade(1005, side="sell", user="seller")]
    f = finder.research(create(), rows, reg, decision_ts=1030)
    assert f["complete"] is True
    assert f["creator_prior_launches"] == 1
    assert f["creator_history_n"] == 2 and f["creator_age_s"] == 600
    assert f["funder"] == "FUNDER" and f["funder_fanout"] == 2
    # The creator is not one of its own buyers; sellers are not buyers.
    assert f["buyers_seen"] == 3 and f["buyers_checked"] == 3
    assert f["buyers_fresh"] == 2 and f["buyers_linked"] == 1
    assert "funded 2 other token creators" in f["text"]
    assert "1 were funded by the creator or the creator's funder" in f["text"]


def test_a_failed_funder_lookup_is_unknown_never_absent():
    rpc = world()
    rpc.fail = {"c1"}                   # the creator's funding tx won't load
    f = W.WalletFinder(rpc, budget_s=5.0).research(
        create(), [trade(1002, user="b_fresh")], W.Registry(), 1030)
    assert f["funder"] is None and f["funder_status"] == "unknown"
    assert f["complete"] is False
    assert "unknown" in f["text"] and "No SOL funding" not in f["text"]
    # A buyer whose history fails is counted as unknown, not as "not fresh".
    rpc = world()
    rpc.fail = {"b_fresh"}
    f = W.WalletFinder(rpc, budget_s=5.0).research(
        create(), [trade(1002, user="b_fresh")], W.Registry(), 1030)
    assert f["buyers_checked"] == 0 and f["buyers_unknown"] == 1
    assert "could not be checked" in f["text"]


def test_finder_marks_itself_incomplete_when_the_budget_runs_out():
    finder = W.WalletFinder(world(), budget_s=-1.0)
    f = finder.research(create(), [trade(1002, user="b_fresh")], W.Registry(),
                        decision_ts=1030)
    assert f["complete"] is False and f["cut_short"]


def test_finder_without_rpc_reports_unknown_rather_than_clean():
    f = W.WalletFinder(None).research(create(), [], W.Registry(), 1030)
    assert f["complete"] is False
    assert f["creator_history_n"] is None
    assert "unknown" in f["text"]


# -------------------------------------------------------- paper integration
class FixedFinder:
    def __init__(self, research_s):
        self.research_s = research_s

    def research(self, create, rows, registry, decision_ts):
        return {"text": "t", "buyers_fresh": 1, "research_s": self.research_s}


class SlowDecider:
    """Accepts everything; the wallet arm's reach time is measured around it."""

    def decide(self, payload):
        return {"available": True, "accept": True}


class Args:
    fee_bps, gas_sol, clip_sol, follow = 100.0, 0.0001, 0.25, False
    gate = dict(P.GATE)


def passing_watch():
    watch = P.Watch(create())
    for i in range(20):
        watch.on_trade(trade(1001 + i, user=f"u{i}", v_sol=40.0 + i * 0.01,
                             v_tok=0.8e9))
    return watch


def test_wallet_arm_is_priced_where_its_verdict_exists_not_at_window_close():
    watch = passing_watch()
    ctx = P.WalletCtx(FixedFinder(0.0), W.Registry())
    row = P.build_row(watch, Args(), SlowDecider(), 30.0, 1030.0, ctx)
    assert row["gate_passed"] and row["arms"]["gate_wallet_decide"] is True
    # Pretend research took 5 s and the curve ran up meanwhile.
    row["reach_s"] = 5.0
    watch.on_trade(trade(1033, user="late", v_sol=60.0, v_tok=0.53e9))
    P.finalise(row, watch, Args())
    assert row["entry_reach"]["v_sol_at_entry"] == 60.0
    assert row["entry"]["v_sol_at_entry"] < 41.0
    assert row["reach_drift"] > 0.5            # paid, not skipped
    assert set(row["marks_reach"]) == {str(h) for h in P.HORIZONS}


def test_wallet_cache_makes_a_replay_reproduce_the_live_row(tmp_path):
    cache = tmp_path / "wallet.jsonl"
    live = P.WalletCtx(FixedFinder(1.0), W.Registry(), str(cache))
    a = P.build_row(passing_watch(), Args(), SlowDecider(), 30.0, 1030.0, live)
    live.handle.close()
    # Replay: no finder at all, only the cache. Same verdict, same reach time.
    replay = P.WalletCtx(None, W.Registry(), str(cache))
    b = P.build_row(passing_watch(), Args(), SlowDecider(), 30.0, 1030.0, replay)
    assert b["reach_s"] == a["reach_s"]
    assert b["arms"]["gate_wallet_decide"] == a["arms"]["gate_wallet_decide"]
    # A mint missing from the cache is a void row, never a silent accept.
    missing = P.WalletCtx(None, W.Registry(), str(tmp_path / "empty.jsonl"))
    c = P.build_row(passing_watch(), Args(), SlowDecider(), 30.0, 1030.0, missing)
    assert c["arms"]["gate_wallet_decide"] is False
    assert c["wallet_decider_available"] is False


def test_wallet_module_is_capital_disabled_by_construction():
    tree = ast.parse(open(W.__file__).read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.name.split(".")[0] for a in node.names)
    for forbidden in ("Keypair", "send_transaction", "sendTransaction",
                      "sign", "sign_message", "private_key", "signer",
                      "solana", "solders", "Transaction"):
        assert forbidden not in names, forbidden
    # And the only RPC methods it ever names are reads.
    methods = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and n.value.startswith("get")}
    assert methods <= {"getSignaturesForAddress", "getTransaction"}


def test_scorer_scores_the_wallet_arm_at_the_reach_seat(tmp_path):
    import snipescore as S
    rows = []
    for i in range(12):
        accept = i % 3 == 0
        rows.append({
            "mint": f"m{i}", "created_ts": 1_700_000_000 + i * 600,
            "gate_passed": True, "entry": {"x": 1}, "entry_reach": {"x": 1},
            "decider_available": True, "wallet_decider_available": True,
            "arms": {"gate": True, "gate_decide": False,
                     "gate_wallet_decide": accept},
            "wallet": {"buyers_fresh": i % 3},
            "marks": {"60": {"ret": 0.5}},
            "marks_reach": {"60": {"ret": 0.1 if accept else -0.1}},
        })
    s = S.summarise(rows, "60", "t", arm="gate_wallet_decide",
                    econ="marks_reach")
    assert s["n_accept_decide"] == 4
    assert abs(s["gate"]["mean"] - (4 * 0.1 - 8 * 0.1) / 12) < 1e-12
    # D2 finds that `buyers_fresh` alone reproduces this accept set.
    d2 = S.d2_one_line_rules(rows, "gate_wallet_decide")
    assert d2["reducible"] is True
    assert d2["best"]["rule"].startswith("buyers_fresh")
