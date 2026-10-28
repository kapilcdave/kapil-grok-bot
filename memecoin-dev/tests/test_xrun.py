import argparse
import time

import pytest

import xrun
from devbot import feed as feed_mod


def args(**kw):
    base = dict(send=False, limit=0, design_gap_s=0.0, min_followers=0, max_age_s=12.0, llm_timeout=0.2, art_timeout=1.0,
                dev_buy_sol=0.05, sell_at=1.0, hold_s=1.0, max_per_hour=4, budget_pct=10.0,
                max_dev_buy=0.55, floor_pct=20.0, x_post=False,
                wallet="", launcher="pumpportal", j7_key_env="J7_WALLET_KEY", j7_region="nyc",
                trigger="first", hot_n=4, min_age_s=0.0)
    base.update(kw)
    return argparse.Namespace(**base)


def runner(a, creates=None):
    r = object.__new__(xrun.Runner)
    r.a, r.k, r.creates = a, {k: "" for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "OPENROUTER_API_KEY",
                                               "POLLINATIONS_TOKEN", "PINATA_JWT")}, creates or xrun.Creates()
    r.model, r.avoid, r.sent, r.done = "m", [], [], 0
    r.designed_at = r.keyless_at = r.keyed_dead_until = 0.0
    return r


def post(age=1.0, suggestion="", ticker=""):
    p = feed_mod.Post("1", "someone", "Someone", 500_000, "a dog drives a bus", "https://x.com/someone/status/1",
                      time.time() - age, suggestion=suggestion)
    p.seen, p.ticker = p.created + 0.3, ticker
    return p


def test_is_rival_matches_the_obvious_name_ticker_and_long_prefixes_only():
    assert xrun.is_rival("Bus Dog⁠", "BD", "Bus Dog", "")
    assert xrun.is_rival("anything", "BUSDOG", "Bus Dog", "BUSDOG")
    assert xrun.is_rival("Agent Capital Market", "X", "Agent Capital Markets", "")
    assert not xrun.is_rival("Agent", "AGT", "Agent Capital Markets", "ACM")
    assert not xrun.is_rival("Bus", "BU", "", "BU")           # 2-letter tickers and empty names match nothing


def test_creates_counts_rivals_inside_the_window_only():
    c = xrun.Creates()
    c.add(100.0, {"mint": "a", "name": "Bus Dog", "symbol": "BD"})
    c.add(200.0, {"mint": "b", "name": "Bus Dog", "symbol": "BD"})
    c.add(150.0, {"mint": "c", "name": "Other", "symbol": "OT"})
    c.add(150.0, {"name": "no mint"})
    assert [r[3] for r in c.rivals("Bus Dog", "", 90, 160)] == ["a"]


def test_design_falls_back_to_the_j7_name_when_the_model_is_slow(monkeypatch):
    monkeypatch.setattr(xrun.concept_mod, "from_post", lambda *a, **k: time.sleep(1))
    monkeypatch.setattr(xrun.image_mod, "generate", lambda *a, **k: b"img")
    monkeypatch.setattr(xrun.image_mod, "fit", lambda raw: raw)
    d = runner(args()).design(post(suggestion="Bus Dog", ticker="$BUS-DOG"))
    assert (d.name, d.symbol, d.source, d.art) == ("Bus Dog", "BUSDOG", "j7", b"img")
    assert any("concept took over" in e for e in d.errors)


def test_design_without_a_name_or_model_gives_no_concept(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(xrun.concept_mod, "from_post", boom)
    monkeypatch.setattr(xrun.image_mod, "generate", lambda *a, **k: b"img")
    monkeypatch.setattr(xrun.image_mod, "fit", lambda raw: raw)
    d = runner(args()).design(post())
    assert d.name == "" and d.source == "" and any("quota" in e for e in d.errors)


def test_gate_refuses_slow_and_second_place_and_a_dry_run_never_reads_a_balance():
    r = runner(args())
    p = post()
    assert r.gate(p, 13.0, []).startswith("too slow")
    assert "rival" in r.gate(p, 5.0, [(0.0, "Bus Dog", "BD", "m")])
    assert r.gate(p, 5.0, []) == ""     # no rpc on this runner: a dry run must not touch it


def test_gate_send_honours_stop_file_hour_cap_and_session_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(xrun, "STOP", tmp_path / "STOP")
    r = runner(args(send=True))
    r.main = type("K", (), {"pubkey": lambda self: "MAIN"})()
    r.rpc = type("R", (), {"balance_sol": lambda self, pk: 0.50})()
    r.state = {"start_balance": 0.52}                     # floor 0.468; worst loss ~0.0124 leaves 0.4876
    p = post()
    assert r.gate(p, 5.0, []) == ""
    r.state = {"start_balance": 0.56}                     # floor 0.504: already under it
    assert r.gate(p, 5.0, []).startswith("budget")
    r.state = {"start_balance": 0.50}
    r.sent = [time.time()] * 4
    assert "launches this hour" in r.gate(p, 5.0, [])
    (tmp_path / "STOP").write_text("x")
    assert r.gate(p, 5.0, []).startswith("STOP")


def test_no_art_dry_run_skips_the_image_call(monkeypatch):
    monkeypatch.setattr(xrun.concept_mod, "from_post", lambda *a, **k: None)
    def no(*a, **k):
        raise AssertionError("image call in a --no-art run")
    monkeypatch.setattr(xrun.image_mod, "generate", no)
    d = runner(args(no_art=True)).design(post(suggestion="Bus Dog", ticker="BUS"))
    assert d.art == b"stub" and d.t["art"] == xrun.ART_EST_S


def test_send_refuses_no_art():
    with pytest.raises(SystemExit):
        xrun.main(["live", "--send", "--no-art"])


def test_art_falls_back_to_keyless_pollinations_and_crops_the_watermark(monkeypatch):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), "red").save(buf, "PNG")
    def quota(*a, **k):
        raise RuntimeError("429 neurons")
    monkeypatch.setattr(xrun.concept_mod, "from_post", lambda *a, **k: None)
    monkeypatch.setattr(xrun.image_mod, "generate", quota)
    monkeypatch.setattr(xrun.image_mod, "_pollinations", lambda prompt, token: buf.getvalue())
    monkeypatch.setattr(xrun.image_mod, "fit", lambda raw: raw)
    d = runner(args(art_timeout=5.0)).design(post(suggestion="Bus Dog", ticker="BUS"))
    assert Image.open(io.BytesIO(d.art)).size == (90, 90)
    assert any(e.startswith("keyed art out") for e in d.errors)


def test_keyed_art_is_skipped_after_it_fails_and_designs_are_spaced(monkeypatch):
    calls = []
    def quota(*a, **k):
        calls.append("keyed")
        raise RuntimeError("429 neurons")
    monkeypatch.setattr(xrun.concept_mod, "from_post", lambda *a, **k: None)
    monkeypatch.setattr(xrun.image_mod, "generate", quota)
    monkeypatch.setattr(xrun.image_mod, "_pollinations", lambda prompt, token: calls.append("keyless") or b"")
    monkeypatch.setattr(xrun, "uncaption", lambda raw: b"img")
    monkeypatch.setattr(xrun.image_mod, "fit", lambda raw: raw)
    r = runner(args(art_timeout=5.0))
    r.design(post(suggestion="Bus Dog", ticker="BUS"))
    r.design(post(suggestion="Bus Dog", ticker="BUS"))
    assert calls == ["keyed", "keyless", "keyless"]       # the second design goes straight to keyless
    r2 = runner(args(design_gap_s=50.0))
    r2.busy, r2._handle = __import__("threading").Lock(), lambda p: True
    assert r2.handle(post()) is True and r2.handle(post()) is False
