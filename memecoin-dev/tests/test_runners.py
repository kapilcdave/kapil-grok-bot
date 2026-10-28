from runners import enrich, rule, score

HOUR = 3600.0


def sib(mint, created, mc, liq=50_000, name="Kimchi", symbol="KIMCHI"):
    return {"mint": mint, "name": name, "symbol": symbol, "created": created, "mc": mc, "liq": liq}


def test_vamp_flags_kimchi_shape():
    # 2026-10-21: the original launched 23:00, a bigger-than-most vamp 50 min later
    sibs = [sib("orig", 0, 33e6), sib("early_copy", 8 * 60, 2e6), sib("vamp", 50 * 60, 10.7e6),
            sib("dead", 10 * 60, 9e6, liq=1_000)]
    assert enrich.vamp_flags("orig", sibs) == {"n_sib": 2, "mc_rank": 1, "t_rank": 1, "lag_s": 0}
    v = enrich.vamp_flags("vamp", sibs)
    assert (v["mc_rank"], v["t_rank"], v["lag_s"]) == (2, 3, 3000)


def enr(mc_rank=1, t_rank=1, post="", judge=None):
    return {"vamp": {"mc_rank": mc_rank, "t_rank": t_rank},
            "story": {"post_url": post, "news": [{"ts": 0, "title": "PHP 9 released"}]}, "judge": judge or {}}


def test_rule_needs_a_real_story_not_a_ticker_headline():
    admit = {"ts": 10, "mc": 500_000}
    assert rule.why_not(admit, enr()) == "no_story"                       # headline alone is not a story
    assert rule.why_not(admit, enr(judge={"news_event": 0.8})) == ""
    assert rule.why_not(admit, enr(post="https://x.com/a/status/1")) == ""
    assert rule.why_not(admit, enr(mc_rank=2, post="https://x.com/a/status/1")) == "not_original"
    assert rule.why_not({"ts": 10, "mc": 33e6}, enr(post="https://x.com/a/status/1")) == "mc"


def test_jev_rule_accepts_zero_variant_probability():
    j = {"variant": 0.0, "kind": "real_world_news"}
    assert rule.jev_rule({"ts": 10, "mc": 500_000}, enr(judge=j))
    assert not rule.jev_rule({"ts": 10, "mc": 500_000}, enr(mc_rank=2, judge=j))


def snap(ts, px, liq=100_000):
    return {"ts": ts, "px": px, "liq": liq}


def test_trade_entry_exit_cost_and_gone():
    c = {"enrich": {"ts": 0}, "snaps": [snap(60, 1.0), snap(3700, 3.0), snap(4 * HOUR + 60, 2.0)], "gone": []}
    t = score.trade(c, 1, clip=100, fee=0.01)
    leg = 0.01 + 100 / 50_000
    assert abs(t["gross"] - 2.0) < 1e-9
    assert abs(t["net"] - (3.0 * (1 - leg) ** 2 - 1)) < 1e-9
    assert score.trade(c, 4, 100, 0.01)["max"] == 2.0                    # peak 3.0 inside the hold
    c["gone"] = [2 * HOUR]
    assert score.trade(c, 4, 100, 0.01)["net"] == -1.0
    late = {"enrich": {"ts": 0}, "snaps": [snap(400, 1.0)], "gone": []}
    assert score.trade(late, 1, 100, 0.01) is None                         # no entry within 3 min


def test_unknown_liquidity_costs_a_flat_five_percent():
    assert abs(score.leg_cost(0, 100, 0.01) - 0.06) < 1e-12
    assert abs(score.leg_cost(50_000, 100, 0.01) - 0.014) < 1e-12


def test_x_post_id_carries_its_time():
    from runners import features
    p = features.post_time("https://x.com/jimothy_phd/status/2112771115231641860")
    assert p["post_author"] == "jimothy_phd"
    assert 1.79e9 < p["post_ts"] < 1.80e9                                   # October 2026
    assert features.post_time("https://x.com/someone") == {}


def test_text_flags_polymarket_and_charity():
    from runners import features
    f = features.text_flags({"name": "Save the Shelter", "description": "fees donated", "links": ["https://t.me/x"]},
                            "@Polymarket: odds jump")
    assert f["polymarket"] and f["charity"] and f["has_tg"] and not f["has_site"]


def test_avoid_fresh_launch_bought_or_brand_coins():
    def e(created, grad=600, dev=0.1, mainstream=0.1):
        return {"features": {"launch": {"created": created, "grad_lag_s": grad},
                             "dev": {"dev_buy_sol": dev}, "kind": {"mainstream": mainstream}}}
    now = {"ts": 10_000}
    assert rule.avoid(now, e(9_000, grad=0))                 # curve bought out at launch
    assert rule.avoid(now, e(9_000, dev=26.5))               # TRUMP 2026-10-21
    assert rule.avoid(now, e(9_000, mainstream=0.8))
    assert not rule.avoid(now, e(9_000))
    assert not rule.avoid(now, e(1_000, grad=0, dev=87))     # older than 1 h at admission: not judged by launch
    assert rule.avoid(now, {}) is None


def test_coin_missing_from_its_own_search_is_not_a_vamp():
    # PANGEA 2026-10-21: DexScreener search returned no row for the coin itself, so it ranked 0 and was rejected
    admit = {"ts": 10_000, "mint": "me", "mc": 418_000, "pair_created": 6_400}
    e = enr(mc_rank=0, t_rank=0, post="https://x.com/a/status/1")
    e["siblings"] = [sib("copy", 9_000, 40_000)]
    assert rule.why_not(admit, e) == ""
    e["siblings"] = [sib("older_bigger", 5_000, 900_000)]
    assert rule.why_not(admit, e) == "not_original"


def test_storybook_matches_a_distinctive_word_seen_before_the_coin():
    from runners import stories
    b = stories.StoryBook()
    b.add([{"key": "php", "src": "news_technology", "title": "PHP warns admins of two critical flaws",
            "ts": 0, "text": "PHP warns admins of two critical flaws"}], now=100)
    assert [w for w, _ in b.match("PHP-UP-2026", "PHP", before=200)] == ["php"]
    assert b.match("PHP-UP-2026", "PHP", before=50) == []                 # the story came later than the coin
    (w, st), = b.match("Critical Doge", "CRIT", before=200)                # any word matches; Jev reads the headline
    assert w == "critical" and "critical" not in st["subject_terms"] and "php" in st["subject_terms"]
    for i in range(5):                                                      # a word in many headlines is not distinctive
        b.add([{"key": f"x{i}", "src": "news_top", "title": f"flaws story {i}", "ts": 0, "text": f"flaws story {i}"}], now=100)
    assert b.match("Flaws", "FLAWS", before=200) == []


def test_launch_burst_becomes_a_story():
    from runners import stories
    b = stories.StoryBook()
    for i, dev in enumerate(["d1", "d2", "d2"]):
        made = b.note_launch({"mint": f"m{i}", "name": "Microduck", "symbol": "DUCK", "dev": dev}, now=1000 + i)
    assert made and made[0]["src"] == "launch_burst"


def test_news_rule():
    admit = {"ts": 10_000, "mc": 80_000,
             "news": {"created": 9_000, "stories": [{"src": "news_technology", "title": "PHP flaws", "ts": 5_000}]}}
    enr_ = {"news_judge": {"named_after_story": 0.8, "variant": 0.1, "fresh_event": 0.3}, "news_ranks": {"mc_rank": 1},
            "features": {"launch": {"created": 9_000, "grad_lag_s": 600}, "dev": {"dev_buy_sol": 1}, "kind": {"mainstream": 0.2}}}
    assert rule.news_why_not(admit, enr_) == ""
    assert rule.news_why_not({**admit, "mc": 400_000}, enr_) == "mc"
    old_coin = {**admit, "news": {**admit["news"], "created": 1_000}}          # JONATHAN on an NFL headline
    assert rule.news_why_not(old_coin, enr_) == "coin_predates_story"
    enr_["news_judge"]["fresh_event"] = 0.7                                   # Macca on GTA 6: old coin, fresh event
    assert rule.news_why_not(old_coin, enr_) == ""
    enr_["news_ranks"]["mc_rank"] = 2
    assert rule.news_why_not(admit, enr_) == "not_biggest"
    enr_["news_ranks"]["mc_rank"] = 1
    enr_["features"]["dev"]["dev_buy_sol"] = 30
    assert rule.news_why_not(admit, enr_) == "avoid" and rule.news_why_not(admit, enr_, use_avoid=False) == ""


def test_news_rule_v2_ranks_rivals_at_the_decision(monkeypatch):
    # 2026-10-23 03:29Z: two Humor coins admitted together; stale caps ranked both biggest
    from runners import newsfirst
    c = {"mc_jup": 30_000, "created": 200, "rivals": [{"mint": "big", "mc": 9_000, "created": 100},
                                                      {"mint": "unlisted", "mc": 12_000, "created": 300}]}
    assert newsfirst.news_ranks(c, 32_733)["mc_rank"] == 1
    monkeypatch.setattr(newsfirst.sources, "_batch", lambda ms: {"me": {"marketCap": 32_733}, "big": {"marketCap": 63_539}})
    live = newsfirst.live_ranks(c, "me")
    assert (live["mc_rank"], live["n_priced"], live["t_rank"], live["top_rival_mc"]) == (2, 1, 2, 63_539)
    admit = {"ts": 10_000, "mc": 32_733,
             "news": {"created": 9_000, "stories": [{"src": "launch_burst", "title": "3 new coins named 'humor'", "ts": 5_000}]}}
    enr_ = {"news_judge": {"named_after_story": 0.8, "variant": 0.1, "fresh_event": 0.8},
            "news_ranks": {"mc_rank": 1}, "news_ranks_live": live,
            "features": {"launch": {"created": 9_000, "grad_lag_s": 600}, "dev": {"dev_buy_sol": 1}, "kind": {"mainstream": 0.2}}}
    assert rule.news_why_not(admit, enr_, source="burst") == ""
    assert rule.news_why_not(admit, enr_, source="burst", ranks="news_ranks_live") == "not_biggest"


def test_ladder_takes_profit_on_the_way_up_and_trails_the_rest():
    from runners import exits
    px = [1.0, 1.5, 2.1, 3.0, 4.2, 5.0, 4.9, 2.5]           # runs to 5x, then crashes
    s = [{"ts": 60 * i, "px": p, "liq": 1e9} for i, p in enumerate(px)]
    lad = exits.run(s, [], clip=100, fee=0.0)
    assert lad.done and lad.reason == "trail" and lad.took == 2
    # 1/3 at 2.1, 1/3 at 4.2, the last 1/3 trailed out at 2.5 (a 50% stop off 5.0)
    assert abs(lad.net() - ((2.1 + 4.2 + 2.5) / 3 - 1)) < 1e-5


def test_ladder_stops_out_a_dud_and_loses_a_rug():
    from runners import exits
    dud = exits.run([{"ts": 60 * i, "px": p, "liq": 1e9} for i, p in enumerate([1.0, 0.9, 0.55])], [], fee=0.0)
    assert dud.reason == "stop" and abs(dud.net() - (-0.45)) < 1e-5
    rug = exits.run([{"ts": 0, "px": 1.0, "liq": 1e9}, {"ts": 60, "px": 1.2, "liq": 1e9}], [90], fee=0.0)
    assert rug.reason == "gone" and rug.net() == -1.0


def test_pin_ignores_a_thin_pool_flap_and_follows_a_real_migration():
    def sn(i, pair, px, liq):
        return {"ts": 60 * i, "pair": pair, "px": px, "liq": liq}
    # XFILMS 10-23: one minute on an $8k copy pool at 13x, then back
    flap = [sn(0, "dbc", 1.0, 30_000), sn(1, "thin", 13.2, 8_000), sn(2, "thin", 13.9, 8_000), sn(3, "dbc", 1.1, 30_000)]
    assert [s["px"] for s in score.pinned(flap)] == [1.0, 1.1]
    mig = [sn(0, "curve", 1.0, 30_000)] + [sn(i, "amm", 1.2, 40_000) for i in range(1, 5)]
    assert [s["pair"] for s in score.pinned(mig)] == ["curve", "amm", "amm", "amm", "amm"]
