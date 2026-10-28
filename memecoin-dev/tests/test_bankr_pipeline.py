import time

import pytest

import bankr_pipeline as p
from devbot import concept


def tok(name, sym, addr, vol=5000.0, chg=10.0):
    return {"chain": "solana", "address": addr, "name": name, "symbol": sym, "liq": 20000, "vol_h1": vol,
            "vol_h24": vol * 5, "chg_h1": chg, "chg_h24": 0, "age_h": 3, "desc": "", "url": ""}


def test_narratives_group_distinct_names_and_skip_cold_tokens():
    rows = [tok("Shark Cat", "SCAT", "a"), tok("Broom Cat", "BCAT", "b"), tok("Cold Cat", "CCAT", "c", vol=10),
            tok("Solo Frog", "FROG", "d")]
    got = p.narratives(rows)
    assert [n["term"] for n in got] == ["cat"]
    assert {t["address"] for t in got[0]["tokens"]} == {"a", "b"}


def test_same_name_twice_is_not_a_narrative():
    assert p.narratives([tok("koinu", "KOINU", "a"), tok("koinu", "KOINU", "b")]) == []


def test_pick_skips_recent_terms_and_tokens():
    nars = [{"term": "cat", "tokens": [tok("x", "X", "a")], "score": 9},
            {"term": "dog", "tokens": [tok("y", "Y", "b")], "score": 8},
            {"term": "frog", "tokens": [tok("z", "Z", "c")], "score": 7}]
    s = {"terms": {"cat": time.time()}, "addrs": {"b": time.time()}}
    assert p.pick(s, nars)["term"] == "frog"
    assert p.pick({"terms": {"cat": time.time() - 90000}, "addrs": {}}, nars)["term"] == "cat"


def test_design_any_moves_past_a_bad_ticker_reply(monkeypatch):
    """A reply whose tickers all overflow SYMBOL_RE must cost one narrative, not the cycle."""
    nars = [{"term": "hugo", "tokens": []}, {"term": "oil", "tokens": []}]
    seen = []

    def fake_design(n):
        seen.append(n["term"])
        if n["term"] == "hugo":
            raise concept.ConceptError("Hugo Whisper / 'Hugowhisper': bad ticker")
        return "concept"

    monkeypatch.setattr(p, "design", fake_design)
    n, c = p.design_any(nars)
    assert (n["term"], c) == ("oil", "concept")
    assert seen == ["hugo"] * p.DESIGN_TRIES + ["oil"]   # re-asked, then moved on


def test_design_any_raises_when_every_narrative_fails(monkeypatch):
    monkeypatch.setattr(p, "design", lambda n: (_ for _ in ()).throw(concept.ConceptError("bad ticker")))
    with pytest.raises(RuntimeError, match="no usable concept"):
        p.design_any([{"term": "hugo", "tokens": []}])


def test_eligible_keeps_order_and_drops_recent(monkeypatch):
    nars = [{"term": "cat", "tokens": [tok("x", "X", "a")], "score": 9},
            {"term": "dog", "tokens": [tok("y", "Y", "b")], "score": 8},
            {"term": "frog", "tokens": [tok("z", "Z", "c")], "score": 7}]
    s = {"terms": {"cat": time.time()}, "addrs": {"b": time.time()}}
    assert [n["term"] for n in p.eligible(s, nars)] == ["frog"]
    assert [n["term"] for n in p.eligible({"terms": {}, "addrs": {}}, nars)] == ["cat", "dog", "frog"]


def test_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "STOP", tmp_path / "STOP")
    monkeypatch.setenv("BANKR_MAX_PER_DAY", "2")
    monkeypatch.setenv("BANKR_GAP_MIN", "30")
    now = time.time()
    assert p.gates({"launches": [], "posts": []}) == ""
    assert "BANKR_GAP_MIN" in p.gates({"launches": [now - 60], "posts": []})
    assert "BANKR_MAX_PER_DAY" in p.gates({"launches": [now - 4000, now - 8000], "posts": []})
    (tmp_path / "STOP").write_text("")
    assert "STOP" in p.gates({"launches": [], "posts": []})


def test_extra_stop_does_not_leak_into_the_shared_stopword_set():
    """bankr's filler must not re-stop devbot's own scanner: `STOP |= ...` used to do exactly that."""
    from devbot import trends
    assert "ai" not in p.EXTRA_STOP & trends.STOP        # it is bankr's, not devbot's
    assert "ai" in trends.terms("Bob AI", "BOBAI")       # devbot still sees it
    assert "ai" not in trends.terms("Bob AI", "BOBAI", p.EXTRA_STOP)
