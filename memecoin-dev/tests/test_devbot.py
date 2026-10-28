import time

from devbot import http
from devbot import trends as tr
from devbot.concept import distinct_from
from devbot.ledger import Ledger, check_launch

NOW = time.time()


def test_variant_must_differ_from_every_example():
    ex = [("Elon Dog", "EDOG"), ("Police Inu", "PINU")]
    assert distinct_from("Elon Cat", "ECAT", ex)
    assert not distinct_from("elon dog", "ECAT", ex)
    assert not distinct_from("Elon Cat", "edog", ex)
    assert not distinct_from("Elon Cat", "EDOG2", ex)       # ticker contains an example ticker
    assert not distinct_from("Real EDOG Cat", "ECAT", ex)
    assert not distinct_from("Cop Cat", "PINUX", ex)
    assert not distinct_from("", "ECAT", ex)
    # the wave's own word is not distinctive: a cat coin in a cat wave may say CAT
    cats = [("144p cat", "CAT"), ("cat wif sword", "SWORDCAT")]
    assert distinct_from("Catblor", "CATB", cats, frozenset({"cat"}))
    assert not distinct_from("Swordcat Jr", "SWCJ", cats, frozenset({"cat"}))   # distinctive ticker reused
    assert not distinct_from("Mega Cat", "CAT", cats, frozenset({"cat"}))       # exact ticker still blocked


def test_terms_split_camelcase_and_drop_filler():
    t = tr.terms("PoliceDoge", "PDOGE")
    assert {"police", "doge", "pdoge"} <= t
    assert not tr.terms("The First Community Coin", "COIN") & {"the", "first", "community", "coin"}
    assert "cat" in tr.terms("Cats", "CATS")                  # plural folded
    assert "ai" in tr.terms("Bob AI", "BOBAI")                # short allowlisted term kept
    assert "маня" in tr.terms("Маня", "Manya")                # non-Latin names count


def seed(db_path, rows, *, first_poll_h_ago):
    t = tr.Trends(db_path)
    for mint, name, symbol, creator, age_min, peak in rows:
        t.db.execute("INSERT INTO coins (mint, name, symbol, description, creator, created, peak_usd, replies,"
                     " complete, seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (mint, name, symbol, "", creator, NOW - age_min * 60, peak, 0, 0, NOW))
    t.db.execute("INSERT INTO meta VALUES ('first_poll', ?)", (str(NOW - first_poll_h_ago * 3600),))
    t.db.commit()
    return t


def test_narratives_score_merge_and_guard(tmp_path):
    rows = [
        # elon/musk wave: 5 coins, 4 creators, mostly in the last hour, two pumped hard
        ("e1", "Elon Musk Cat", "EMC", "c1", 10, 64000),
        ("e2", "Musk Elon Rocket", "MER", "c2", 20, 32000),
        ("e3", "Elon Musk Frog", "EMF", "c3", 30, 4000),
        ("e4", "Baby Elon Musk", "BEM", "c4", 40, 5000),
        ("e5", "Elon Musk Hat", "EMH", "c1", 200, 4000),
        # one creator spamming "zork": must not count as a narrative
        ("z1", "Zork One", "ZRK1", "spam", 5, 4000), ("z2", "Zork Two", "ZRK2", "spam", 6, 4000),
        ("z3", "Zork Three", "ZRK3", "spam", 7, 4000), ("z4", "Zork Four", "ZRK4", "spam", 8, 4000),
        # a quiet old theme: present but no velocity, no traction
        ("o1", "Owl Wise", "OWLW", "c5", 300, 4000), ("o2", "Owl Night", "OWLN", "c6", 320, 4000),
        ("o3", "Owl Moon", "OWLM", "c7", 340, 4000),
    ]
    t = seed(tmp_path / "t.sqlite", rows, first_poll_h_ago=8)
    ns = t.narratives(now=NOW)
    top = ns[0]
    assert {"elon", "musk"} <= set(top.terms)                 # co-occurring terms merged
    assert top.creators == 4 and top.coins_6h == 5 and top.winners == 2 and top.launches_1h == 4
    assert top.lift > 1                                       # 4 in the last hour vs ~0.2/h before
    assert not any("zork" in n.terms for n in ns)             # single-creator spam excluded
    owl = next(n for n in ns if "owl" in n.terms)
    assert owl.score < top.score
    assert not any({"elon", "musk"} & set(n.terms) for n in t.narratives(now=NOW, exclude_terms={"elon"}))


def test_narratives_warmup_has_no_fake_velocity(tmp_path):
    rows = [("a1", "Frog King", "FKG", "c1", 5, 4000), ("a2", "Frog Queen", "FQN", "c2", 6, 4000),
            ("a3", "Frog Jester", "FJS", "c3", 7, 4000)]
    t = seed(tmp_path / "w.sqlite", rows, first_poll_h_ago=0.5)
    (n,) = [n for n in t.narratives(now=NOW) if "frog" in n.terms]
    assert n.base_per_h == 0 and n.launches_1h == 3 and n.lift is None   # no fake velocity in warm-up
    assert "warming up" in n.evidence()


def ok(**over):
    args = dict(stopped=False, live=True, launches_today=0, max_launches=3, spent_today=0.0,
                max_sol_per_day=0.05, cost=0.01, balance=1.0, baseline=1.0, floor_pct=50)
    args.update(over)
    return check_launch(**args)


def test_launch_guard():
    assert ok().ok
    assert not ok(live=False).ok
    assert not ok(stopped=True).ok
    assert not ok(launches_today=3).ok
    assert not ok(spent_today=0.045).ok
    assert ok(spent_today=0.04).ok
    assert not ok(balance=0.505).ok                 # would dip under 50% of baseline
    assert not ok(baseline=None).ok
    assert ok(max_launches=0, launches_today=500).ok    # 0 = no count cap...
    assert not ok(max_launches=0, spent_today=0.045).ok  # ...but the SOL cap still binds


def test_sol_cap_none_must_be_explicit(monkeypatch, tmp_path):
    import math

    import pytest

    from devbot import config

    for k, v in {"TELEGRAM_ALLOWED_USERS": "1", "DEVBOT_TELEGRAM_TOKEN": "t", "TELEGRAM_HOME_CHAT": "1",
                 "DEV_KEYPAIR_PATH": "k", "CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "c",
                 "PINATA_JWT": "j", "LIVE": "1", "DEVBOT_STATE": str(tmp_path)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MAX_SOL_PER_DAY", "")
    with pytest.raises(config.ConfigError):        # empty never means unlimited
        config.load()
    monkeypatch.setenv("MAX_SOL_PER_DAY", "none")
    assert config.load().max_sol_per_day == math.inf
    assert ok(max_sol_per_day=math.inf, spent_today=50.0).ok   # no daily cap...
    assert not ok(max_sol_per_day=math.inf, balance=0.505).ok  # ...the floor still stops it


def test_baseline_only_ratchets_up(tmp_path):
    led = Ledger(tmp_path / "b.sqlite")
    assert led.raise_baseline(0.05) and led.baseline == 0.05   # first record
    assert not led.raise_baseline(0.03) and led.baseline == 0.05  # a loss never lowers the floor
    assert led.raise_baseline(0.12) and led.baseline == 0.12   # a claim raises it


def test_dead_coin_rule_and_pinata_cleanup(tmp_path, monkeypatch):
    import json

    from devbot import pump

    now = 1_800_000_000
    ms = lambda s: (now - s) * 1000  # noqa: E731
    amder = {"complete": False, "last_trade_timestamp": ms(1800), "created_timestamp": ms(1900),
             "real_sol_reserves": 28}
    assert not pump.is_dead(amder, now)                                       # idle 30 min: not yet
    assert pump.is_dead(dict(amder, last_trade_timestamp=ms(4000)), now)      # idle >1 h, curve empty
    assert pump.is_dead({"complete": False, "last_trade_timestamp": None, "created_timestamp": ms(4000),
                         "real_sol_reserves": 0}, now)                         # never traded
    assert not pump.is_dead(dict(amder, last_trade_timestamp=ms(4000), real_sol_reserves=2 * 10**9), now)  # holders
    assert not pump.is_dead(dict(amder, last_trade_timestamp=ms(9999), complete=True), now)  # graduated

    assert pump.cid_of("https://ipfs.io/ipfs/bafkabc") == "bafkabc"

    calls = []

    def fake(method, url, **_):
        calls.append((method, url))
        return json.dumps({"data": {"files": [{"id": "f1", "cid": "bafkabc"}]}}).encode() if method == "GET" else b"{}"
    monkeypatch.setattr(pump.http, "request", fake)
    assert pump.unpin("jwt", "bafkabc") == 1
    assert calls == [("GET", f"{pump.PINATA_API}?cid=bafkabc"), ("DELETE", f"{pump.PINATA_API}/f1")]

    led = Ledger(tmp_path / "p.sqlite")
    led.record_launch(mint="M", name="N", symbol="S", narrative="n", source_mint="s", source_name="x",
                      source_symbol="X", uri="https://ipfs.io/ipfs/meta", signature="sig", confirmed=True,
                      dev_buy_sol=0)
    led.record_pins("M", ["img", "meta"])
    led.db.execute("UPDATE launches SET ts = ts - 7200")
    assert [r[0] for r in led.uncleaned(3600)] == ["M"] and set(led.pins("M")) == {"img", "meta"}
    led.mark_cleaned("M", 2)
    assert led.uncleaned(3600) == []


def test_ledger_persists_switches_and_spend(tmp_path):
    db = tmp_path / "s.sqlite"
    led = Ledger(db)
    led.put("stopped", "1")
    led.record_spend(0.01, "launch X")
    led.record_launch(mint="M", name="Elon Cat", symbol="ECAT", narrative="n", source_mint="s",
                      source_name="Elon Dog", source_symbol="EDOG", uri="u", signature="sig",
                      confirmed=True, dev_buy_sol=0)
    again = Ledger(db)  # a restart must not re-arm or reset today's count
    assert again.stopped
    assert abs(again.spent_today() - 0.01) < 1e-12
    assert again.launches_today() == 1
    assert "ECAT" in again.used_names()


def test_close_account_ix_pays_owner_and_needs_owner_signature():
    from solders.keypair import Keypair

    from devbot.pump import close_account_ix

    owner = Keypair().pubkey()
    acct = str(Keypair().pubkey())
    ix = close_account_ix(acct, owner, "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    assert bytes(ix.data) == b"\x09"
    assert [(str(a.pubkey), a.is_signer, a.is_writable) for a in ix.accounts] == [
        (acct, False, True), (str(owner), False, True), (str(owner), True, False)]


def test_workers_ai_reply_parsing(monkeypatch):
    import json

    import pytest

    from devbot import concept

    dog = tr.Coin("m", "Elon Dog", "EDOG", "c", NOW, 50000, 0, False)
    ns = [tr.Narrative(["elon", "dog"], 5.0, 3, 0.5, 4.0, 5, 3, 1, [dog], lift=2.7)]
    good = {"narrative_index": 0, "name": "Elon Cat", "symbol": "ECAT", "description": "d",
            "image_prompt": "p", "why": "w"}
    copy = dict(good, name="Elon Dog Two", symbol="EDOG2")
    monkeypatch.setattr(concept, "_pick", lambda weights, rng: "as written")  # casing tested separately
    wrapped = {"candidates": [copy, good]}  # first candidate copies; the second must be taken
    for envelope in ({"result": {"response": wrapped}}, {"result": {"response": json.dumps(wrapped)}},
                     {"result": {"response": good}}):  # a bare single candidate is accepted too
        monkeypatch.setattr(concept.http, "post_json", lambda *a, e=envelope, **k: json.dumps(e).encode())
        c = concept.generate("acct", "tok", "model", ns, "counterpart", avoid=[])
        assert (c.name, c.symbol, c.angle) == ("Elon Cat", "ECAT", "counterpart")

    monkeypatch.setattr(concept.http, "post_json",
                        lambda *a, **k: json.dumps({"result": {"response": copy}}).encode())
    with pytest.raises(concept.ConceptError, match="copies"):
        concept.generate("acct", "tok", "model", ns, "counterpart", avoid=[])

    monkeypatch.setattr(concept.http, "post_json",
                        lambda *a, **k: json.dumps({"result": {"response": {"name": "x"}}}).encode())
    with pytest.raises(concept.ConceptError):
        concept.generate("acct", "tok", "model", ns, "counterpart", avoid=[])

    # Workers AI out of neurons: straight to OpenRouter (no second Workers AI call), or fail without a key
    calls = []

    def quota_then_openrouter(url, *a, **k):
        calls.append(url)
        if "cloudflare" in url:
            raise http.HttpError(429, '{"errors":[{"message":"daily free allocation of 10,000 neurons"}]}')
        if len(calls) == 2:                                     # first free model rate-limited upstream
            raise http.HttpError(429, "temporarily rate-limited upstream")
        return json.dumps({"choices": [{"message": {"content": json.dumps(wrapped)}}]}).encode()

    monkeypatch.setattr(http, "post_json", quota_then_openrouter)
    c = concept.generate("acct", "tok", "model", ns, "counterpart", avoid=[], openrouter_key="or",
                         openrouter_models=["a:free", "b:free"])
    assert c.symbol == "ECAT" and len(calls) == 3 and all("openrouter.ai" in u for u in calls[1:])
    with pytest.raises(concept.ServiceError, match="429"):
        concept.generate("acct", "tok", "model", ns, "counterpart", avoid=[])


def test_image_falls_back_to_pollinations(monkeypatch):
    import pytest

    from devbot import image

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    seen = []

    def fake(method, url, *, headers=None, **k):
        seen.append((url, headers))
        if "cloudflare" in url:
            raise http.HttpError(429, "out of neurons")
        assert "nologo=true" in url and "no%20text" in url
        return png

    monkeypatch.setattr(http, "request", fake)
    assert image.generate("acct", "tok", "a rhino", fallback=True, pollinations_token="pt") == png
    assert seen[-1][0].startswith(image.POLLINATIONS_KEYED) and seen[-1][1] == {"Authorization": "Bearer pt"}
    assert image.generate("acct", "tok", "a rhino", fallback=True) == png
    assert seen[-1][0].startswith(image.POLLINATIONS) and seen[-1][1] is None
    with pytest.raises(http.HttpError):                       # fallback off: the Workers AI error stands
        image.generate("acct", "tok", "a rhino")
    monkeypatch.setattr(http, "request", lambda *a, **k: (_ for _ in ()).throw(http.HttpError(429, "x"))
                        if "cloudflare" in a[1] else b"<html>rate limited</html>")
    with pytest.raises(RuntimeError, match="no image"):
        image.generate("acct", "tok", "a rhino", fallback=True)
    assert image.kind(png) == ("png", "image/png") and image.kind(b"\xff\xd8\xff") == ("jpg", "image/jpeg")


def test_rpc_falls_through_only_on_quota_errors(monkeypatch):
    import json

    import pytest

    from devbot import pump

    def fake(replies):
        calls = []

        def post(url, payload, **_):
            calls.append(url)
            r = replies[url]
            if isinstance(r, int):
                raise http.HttpError(r, "")
            return json.dumps(r).encode()
        return calls, post

    calls, post = fake({"pub": 413, "qn": {"result": 7}})
    monkeypatch.setattr(pump.http, "post_json", post)
    assert pump.Rpc(["pub", "qn"]).call("getBalance", []) == 7 and calls == ["pub", "qn"]

    calls, post = fake({"pub": {"error": {"code": -32003, "message": "daily limit"}}, "qn": {"result": 1}})
    monkeypatch.setattr(pump.http, "post_json", post)
    assert pump.Rpc(["pub", "qn"]).call("x", []) == 1

    calls, post = fake({"pub": {"error": {"code": -32602, "message": "bad params"}}, "qn": {"result": 1}})
    monkeypatch.setattr(pump.http, "post_json", post)
    with pytest.raises(RuntimeError, match="bad params"):
        pump.Rpc(["pub", "qn"]).call("x", [])  # a real error must not be retried elsewhere
    assert calls == ["pub"]

    calls, post = fake({"pub": 429, "qn": 429})
    monkeypatch.setattr(pump.http, "post_json", post)
    with pytest.raises(RuntimeError, match="rate-limited"):
        pump.Rpc(["pub", "qn"]).call("x", [])


def test_metadata_carries_the_coin_website():
    from devbot import pump

    m = pump.metadata(name="N", symbol="S", description="d", image_uri="i",
                      website="https://memephil.example.workers.dev/Mint111")
    assert m["website"] == "https://memephil.example.workers.dev/Mint111"
    assert m["twitter"] == "" and m["telegram"] == ""        # never borrowed socials
    assert pump.metadata(name="N", symbol="S", description="d", image_uri="i")["website"] == ""


def test_creator_vault_and_unconfirmed_claim(monkeypatch):
    import pytest
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey

    from devbot import pump

    # the dev wallet's vault, checked against the account its first launch's fees landed in
    dev = Pubkey.from_string("DZo2AkoC418KFiqFDwqwwsiWicVX6wDJHFHgRvh3j1RS")
    assert str(pump.creator_vault(dev)) == "JDhX41ewbsJxNkJoQSyyW5xiPjQopj563t6B1UFtk3Mr"

    class Dropped:  # every send "succeeds" but never lands
        sent = 0

        def send(self, tx):
            self.sent += 1
            return f"sig{self.sent}"

        def confirm(self, sig, timeout_s=75):
            return False

    monkeypatch.setattr(pump, "_build", lambda payload, signers: object())
    rpc = Dropped()
    with pytest.raises(RuntimeError, match="not confirmed after 3 tries"):
        pump.claim_creator_fees(rpc, Keypair(), 0.00005)
    assert rpc.sent == 3                                     # rebuilt and resent, never reported as +0


def test_casing_and_story_inputs(tmp_path, monkeypatch):
    from devbot import concept

    assert concept.apply_casing("Leslie The Rhino", "leslie", "all lowercase", "ALL CAPS") == ("leslie the rhino", "LESLIE")
    assert concept.apply_casing("rose cat", "ROSECAT", "ALL CAPS", "Title") == ("ROSE CAT", "Rosecat")
    assert concept.apply_casing("EVILcoin", "Evil", "as written", "as written") == ("EVILcoin", "Evil")
    assert concept.SYMBOL_RE.match("144pCat") and not concept.SYMBOL_RE.match("$CAT")
    # a reply from a model that ignores JSON mode still parses
    raw = json_reply('Sure! {"narrative_index":0,"name":"a","symbol":"AB","description":"d","image_prompt":"p","why":"w"}')
    assert concept._parse(raw)["candidates"][0]["symbol"] == "AB"

    assert tr.status_url("https://x.com/useponzify/status/2109824583071060079?s=46") == \
        "https://x.com/useponzify/status/2109824583071060079"
    assert tr.status_url("https://x.com/TrySkillsApp") == "" and tr.status_url("") == ""
    t = tr.Trends(tmp_path / "s.sqlite")
    calls = []
    monkeypatch.setattr(tr, "fetch_post", lambda url: calls.append(url) or "@a: the dog on the news")
    ex = [tr.Coin(f"m{i}", "Dog", "DOG", "c", NOW, 4000, 0, False, "", "https://x.com/a/status/9") for i in range(3)]
    n = tr.Narrative(["dog"], 1.0, 1, 0.0, 0.0, 3, 2, 0, ex)
    assert t.post_texts(n) == ["@a: the dog on the news"] and t.post_texts(n) == ["@a: the dog on the news"]
    assert calls == ["https://x.com/a/status/9"]               # fetched once, then cached


def json_reply(response):
    import json

    return json.dumps({"result": {"response": response}}).encode()


def test_x_post_text_and_signature():
    from devbot import social

    mint = "Hf2UTFJzmXNYPEsENKanyW1rKAwjVrQwf5hRYPKpNQVR"
    text = social.launch_text("Leslie the Rhino", "LESLIE", "x" * 400, mint, "Dev buy 0 SOL, dev sells posted publicly.")
    assert len(text) <= 280 and text.startswith("Leslie the Rhino $LESLIE") and f"CA: {mint}" in text
    assert "http" not in text                                   # a link in the post costs ~13x more on X
    bare = social.launch_text("Leslie the Rhino", "LESLIE", "x" * 400, "", "Dev buy 0 SOL, dev sells posted publicly.")
    assert len(bare) <= 280 and "CA:" not in bare and bare.endswith("\n\nDev buy 0 SOL, dev sells posted publicly.")
    blocked = http.HttpError(403, '{"detail":"Crypto addresses are prohibited for the first 7 days after authentication."}')
    assert social.address_blocked(blocked) and not social.address_blocked(http.HttpError(403, "other"))
    keys = social.XKeys("ck", "cs", "at", "as")
    h1 = social.oauth_header(keys, "POST", social.API, nonce="n", timestamp="1")
    assert h1 == social.oauth_header(keys, "POST", social.API, nonce="n", timestamp="1")
    assert h1 != social.oauth_header(social.XKeys("ck", "other", "at", "as"), "POST", social.API, nonce="n", timestamp="1")
    for field in ("oauth_consumer_key=\"ck\"", "oauth_token=\"at\"", "oauth_signature=", "HMAC-SHA1"):
        assert field in h1


def test_command_menu_is_valid_for_telegram():
    import re

    from devbot.bot import COMMANDS, HELP

    for cmd, desc in COMMANDS:                    # setMyCommands rejects the whole list on one bad entry
        assert re.fullmatch(r"[a-z0-9_]{1,32}", cmd) and 1 <= len(desc) <= 256
    assert len(COMMANDS) <= 100 and "/propose - " in HELP


def test_multipart_shape():
    body, ctype = http.multipart({"network": "public"}, {"file": ("a.jpg", b"\xff\xd8", "image/jpeg")})
    boundary = ctype.split("boundary=")[1]
    assert body.startswith(f"--{boundary}\r\n".encode())
    assert b'name="network"\r\n\r\npublic\r\n' in body
    assert b'filename="a.jpg"\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8\r\n' in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_heat_counts_outside_buyers_only():
    coin = {"creator": "dev", "created_timestamp": 1_000_000}          # launched at t=1000 s
    at = lambda s: f"1970-01-01T00:{s // 60:02d}:{s % 60:02d}.000Z"
    trades = [{"type": "buy", "userAddress": "dev", "amountSol": "1", "timestamp": at(1010)},     # creator
              {"type": "buy", "userAddress": "bundle", "amountSol": "5", "timestamp": at(1000)},  # launch second
              {"type": "buy", "userAddress": "a", "amountSol": "0.5", "timestamp": at(1005)},
              {"type": "buy", "userAddress": "a", "amountSol": "0.5", "timestamp": at(1006)},     # same wallet twice
              {"type": "buy", "userAddress": "b", "amountSol": "1", "timestamp": at(1030)},
              {"type": "sell", "userAddress": "a", "amountSol": "0.9", "timestamp": at(1040)}]
    h = tr.heat(coin, trades)
    assert (h.buyers, h.buy_sol, h.sells) == (2, 2.0, 1)


def test_launch_tool_boilerplate_is_not_the_story():
    from devbot.concept import clean_description

    assert clean_description("Deployed using https://j7tracker.io") == ""
    assert clean_description("the zoo rhino that escaped. Launched with rapidlaunch.io") == "the zoo rhino that escaped"
    assert clean_description("cat that made the news") == "cat that made the news"


def test_non_latin_variants_are_distinct():
    assert distinct_from("ワイちゃん", "WAICH", [("ワイ", "Me")])
    assert not distinct_from("ワイ", "WAI", [("ワイ", "Me")])          # the same name is still a copy


def test_dropped_create_is_resent_once_it_lands_no_more(monkeypatch):
    import pytest
    from solders.keypair import Keypair

    from devbot import pump

    class Net:
        def __init__(self, lands_on):
            self.sent, self.lands_on = [], lands_on

        def send(self, tx):
            self.sent.append(f"sig{len(self.sent) + 1}")
            return self.sent[-1]

        def confirm(self, sig, timeout_s=75):
            if self.lands_on and sig == f"sig{self.lands_on}":
                return True
            if self.lands_on and int(sig[3:]) > self.lands_on:
                raise RuntimeError(f"{sig} failed: mint already in use")
            return False

        def call(self, method, params):
            return {"value": [{"confirmationStatus": "confirmed"} if s == f"sig{self.lands_on}" else None
                              for s in params[0]]}

    monkeypatch.setattr(pump, "_build", lambda payload, signers: object())
    kw = dict(name="n", symbol="S", uri="u", dev_buy_sol=0, slippage_pct=10, priority_fee_sol=0, wait_s=0)
    net = Net(lands_on=2)                                   # first send dropped, the resend lands
    out = pump.create(net, Keypair(), Keypair(), **kw)
    assert out.confirmed and out.signature == "sig2" and net.sent == ["sig1", "sig2"]
    net = Net(lands_on=0)                                   # never lands: 3 tries, reported unconfirmed
    assert not pump.create(net, Keypair(), Keypair(), **kw).confirmed and len(net.sent) == 3

    class Broke(Net):                                       # a genuine failure is raised, not retried
        def confirm(self, sig, timeout_s=75):
            raise RuntimeError(f"{sig} failed: insufficient funds")
    with pytest.raises(RuntimeError, match="insufficient"):
        pump.create(Broke(0), Keypair(), Keypair(), **kw)


def test_mayhem_mode_is_a_create_flag_and_its_agent_is_not_a_buyer(monkeypatch, tmp_path):
    import pytest
    from solders.keypair import Keypair

    from devbot import audience, config, pump

    sent = []
    monkeypatch.setattr(pump, "_build", lambda payload, signers: sent.append(payload) or object())

    class Net:
        def send(self, tx):
            return "sig1"

        def confirm(self, sig, timeout_s=75):
            return True

    kw = dict(name="n", symbol="S", uri="u", dev_buy_sol=0, slippage_pct=10, priority_fee_sol=0)
    pump.create(Net(), Keypair(), Keypair(), **kw)
    pump.create(Net(), Keypair(), Keypair(), mayhem=True, **kw)
    assert "isMayhemMode" not in sent[0] and sent[1]["isMayhemMode"] == "true"

    for k, v in {"TELEGRAM_ALLOWED_USERS": "1", "DEVBOT_TELEGRAM_TOKEN": "t", "TELEGRAM_HOME_CHAT": "1",
                 "DEV_KEYPAIR_PATH": "k", "CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "c",
                 "DEVBOT_STATE": str(tmp_path), "MAYHEM": "1"}.items():
        monkeypatch.setenv(k, v)
    assert config.load().mayhem
    monkeypatch.setenv("LAUNCHER", "j7")
    monkeypatch.setenv("J7_WALLET_KEY", "x")
    with pytest.raises(config.ConfigError, match="MAYHEM"):   # j7tracker signs its own create
        config.load()

    from datetime import datetime, timezone
    t0 = 1_790_000_000.0
    iso = lambda s: datetime.fromtimestamp(t0 + s, timezone.utc).isoformat()
    trades = [{"type": "buy", "userAddress": pump.MAYHEM_AGENT, "timestamp": iso(5 + i), "amountSol": 1}
              for i in range(200)] + [{"type": "buy", "userAddress": "H", "timestamp": iso(120), "amountSol": 0.1}]
    monkeypatch.setattr(audience, "all_trades", lambda mint: trades)
    a = audience.audience(None, "M", "C", t0, {"H": "human"})
    assert a.counts == {"human": 1} and a.late_buys == 1 and a.agent_trades == 200


def test_x_post_carries_the_art_and_falls_back_to_text(monkeypatch):
    import json

    from devbot import social

    keys = social.XKeys("ck", "cs", "at", "as")
    sent = []

    def fake(method, url, *, body=None, headers=None, **k):
        sent.append((url, body))
        if url == social.MEDIA:
            assert b'name="media_category"\r\n\r\ntweet_image' in body and headers["Authorization"].startswith("OAuth ")
            return json.dumps({"data": {"id": "77"}}).encode()
        return json.dumps({"data": {"id": "1"}}).encode()

    monkeypatch.setattr(http, "request", fake)
    mid = social.upload_image(keys, b"\xff\xd8\xff", "jpg", "image/jpeg")
    assert social.post(keys, "hi", mid) == "1" and json.loads(sent[-1][1])["media"] == {"media_ids": ["77"]}
    social.post(keys, "hi")
    assert "media" not in json.loads(sent[-1][1])


def test_audience_tells_humans_from_bots_and_sybils(monkeypatch):
    from devbot import audience

    launch = 1_000_000.0
    history = {"bot": [{"blockTime": launch + i * 10} for i in range(100)],        # 100 txs in 17 min
               "sybil": [{"blockTime": launch + 30}],                              # born for this coin
               "human": [{"blockTime": launch + 60}, {"blockTime": launch - 5 * 86400}]}

    class Rpc:
        def call(self, method, params):
            return history[params[0]]

    assert [audience.classify(Rpc(), w, launch) for w in ("bot", "sybil", "human")] == ["bot", "fresh", "human"]
    at = lambda s: f"1970-01-12T13:{s // 60:02d}:{s % 60:02d}.000Z"   # launch = 13:46:40 on day 12
    trades = {"trades": [{"type": "buy", "userAddress": "sniper", "amountSol": "1", "timestamp": at(2800)},
                         {"type": "buy", "userAddress": "bot", "amountSol": "1", "timestamp": at(2810)},
                         {"type": "buy", "userAddress": "human", "amountSol": "0.3", "timestamp": at(2950)},
                         {"type": "buy", "userAddress": "dev", "amountSol": "5", "timestamp": at(2800)}]}
    monkeypatch.setattr(audience.http, "get_json", lambda *a, **k: {**trades, "pagination": {"hasMore": False}})
    monkeypatch.setattr(audience.time, "sleep", lambda s: None)
    a = audience.audience(Rpc(), "mint", "dev", launch, {})
    assert dict(a.counts) == {"launch-block": 1, "bot": 1, "human": 1} and a.first_human_s == 150 and a.human_sol == 0.3


TRENDS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>giant pumpkin</title><ht:approx_traffic>5,000+</ht:approx_traffic>
  <ht:news_item><ht:news_item_title>Minnesota man grows 2,700 lb pumpkin</ht:news_item_title></ht:news_item></item>
<item><title>singer x</title><ht:approx_traffic>50000+</ht:approx_traffic>
  <ht:news_item><ht:news_item_title>Singer X dies at 81</ht:news_item_title></ht:news_item></item>
<item><title>barcelona</title><ht:approx_traffic>20000+</ht:approx_traffic></item>
</channel></rss>"""


def test_trend_feed_parses_and_drops_tragedies(monkeypatch):
    from devbot import content

    topics = content.parse_trends(TRENDS_XML)
    assert [(t.title, t.traffic) for t in topics] == [("giant pumpkin", 5000), ("singer x", 50000), ("barcelona", 20000)]
    assert topics[0].context == ["Minnesota man grows 2,700 lb pumpkin"] and topics[1].grim
    monkeypatch.setattr(http, "request", lambda *a, **k: TRENDS_XML)
    assert [t.title for t in content.trending()] == ["barcelona", "giant pumpkin"]   # biggest first, no deaths
    dog = tr.Coin("m1", "Elon Dog", "EDOG", "c", NOW, 9000, 0, False)
    sad = tr.Coin("m2", "RIP Doge", "RIP", "c", NOW, 9000, 0, False)
    ns = [tr.Narrative(["dog"], 1, 1, 0.0, 0.0, 3, 2, 0, [dog]), tr.Narrative(["doge"], 1, 1, 0.0, 0.0, 3, 2, 0, [sad])]
    got = content.from_narratives(ns, {"dog": ["@x: elon walked a dog"], "doge": ["@y: kabosu died"]})
    assert [(t.title, t.mint, t.context) for t in got] == [("dog", "m1", ["coin: Elon Dog", "@x: elon walked a dog"])]


def test_draft_is_checked_before_it_reaches_telegram():
    import pytest

    from devbot import content
    from devbot.concept import ConceptError

    topics = content.parse_trends(TRENDS_XML) + [content.Topic("pump.fun", "dog", [], 3, "m1")]
    good = {"topic_index": 0, "jokes": ["ai grew a bigger pumpkin. in a spreadsheet.", "x" * 300,
                                        "buy $GOURD now", "CA: 675N4pG9xkzJ7d8V8Fq8rjzJ6CgYgUQh3nqnJp5PpumP",
                                        "see pump.fun", '"agi will carve it"',
                                        "humans invented weekends to recover from the thing they invented on weekdays"],
            "image": "edit", "image_instruction": "a robot pumpkin"}
    d = content.check(good, topics)
    assert d.jokes == ["ai grew a bigger pumpkin. in a spreadsheet.", "agi will carve it"]
    assert d.image_mode == "new"                       # a google topic has no picture to edit
    assert content.check({**good, "topic_index": 3}, topics).image_mode == "edit"
    assert content.check({**good, "image": "new", "image_instruction": ""}, topics).image_mode == "none"
    for bad in ({"topic_index": -1}, {"topic_index": 9}, {"jokes": ["buy $X", "https://x.com"]}):
        with pytest.raises(ConceptError):
            content.check({**good, **bad}, topics)


def test_oauth_signs_query_params_and_post_can_reply(monkeypatch):
    import json

    from devbot import social

    # X's documented signing example (developer.x.com, "Creating a signature")
    k = social.XKeys("xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
                     "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE")
    h = social.oauth_header(k, "POST", "https://api.twitter.com/1.1/statuses/update.json",
                            nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958",
                            query={"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"})
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h and "status" not in h
    sent = []
    monkeypatch.setattr(http, "request", lambda m, url, *, body=None, headers=None, **kw:
                        sent.append((url, body)) or json.dumps({"data": {"id": "2"}}).encode())
    social.post(k, "launch", reply_to="99")
    assert json.loads(sent[-1][1])["reply"] == {"in_reply_to_tweet_id": "99"}
    social.update_bio(k, "an AI & a dev")
    assert sent[-1][0] == social.PROFILE + "?description=an%20AI%20%26%20a%20dev"


def _bare_bot(tmp_path, **cfg):
    from types import SimpleNamespace

    from devbot.bot import Bot

    b = Bot.__new__(Bot)
    b.cfg = SimpleNamespace(**{"x_drafts_per_day": 4, "content_quiet_utc": (6, 12), "x_keys": None,
                               "live": False, "pinata_jwt": "", "launcher": "pumpportal", "mayhem": False,
                               "rotate_wallets": False, "autosell_pct": 0, **cfg})
    b.ledger = Ledger(tmp_path / "l.sqlite")
    b.drafts, b.proposals, b.content_error = {}, {}, ""
    b.pad = None
    b.wallets = None
    b.feed_auto_cap = 0.0
    b.feed_coins, b.recent_replies = True, []
    return b


def test_content_schedule_skips_quiet_hours(tmp_path):
    b = _bare_bot(tmp_path)
    for h in range(0, 24 * 7):
        now = 1_760_000_000 - 1_760_000_000 % 86400 + h * 3600
        t = b.schedule_content(now)
        assert not 6 <= time.gmtime(t).tm_hour < 12
        assert 0 < t - now <= 18 * 3600 / 4 * 1.25 + 6 * 3600 + 1800
        assert float(b.ledger.get("next_content_ts")) == t
    b.cfg.content_quiet_utc = (22, 4)   # a window across midnight
    for h in range(48):
        assert not (lambda hr: hr >= 22 or hr < 4)(time.gmtime(b.schedule_content(1_760_000_000 + h * 3600)).tm_hour)


def test_x_draft_is_picked_in_telegram_then_posted(tmp_path, monkeypatch):
    from devbot import bot as bot_mod, content, image, pump, social

    b = _bare_bot(tmp_path, x_keys="K", x_max_posts_per_day=2, cf_account_id="a", cf_api_token="t", llm_model="m",
                  openrouter_key="", openrouter_models=(), image_fallback=True, pollinations_token="")

    class Trends:
        def narratives(self, limit):
            return [tr.Narrative(["dog"], 1, 1, 0.0, 0.0, 3, 2, 0, [tr.Coin("m1", "Dog", "DOG", "c", NOW, 1, 0, False)])]

        def post_texts(self, n):
            return []

    b.trends = Trends()
    monkeypatch.setattr(content, "trending", lambda: [t for t in content.parse_trends(TRENDS_XML) if not t.grim])
    seen = {}

    def fake_draft(acct, tok, model, topics, recent, **k):
        seen["topics"], seen["recent"] = [t.title for t in topics], recent
        return content.Draft(next((t for t in topics if t.title == "dog"), topics[0]), ["joke one", "joke two"],
                             "edit", "robot eyes")

    monkeypatch.setattr(content, "draft", fake_draft)
    monkeypatch.setattr(pump, "coin_info", lambda m: {"image_uri": "https://ipfs.io/ipfs/x"})
    monkeypatch.setattr(image, "variant", lambda *a, **k: b"\xff\xd8robot")
    sent, said, posts = [], [], []
    b.photo = lambda art, text, markup=None: sent.append((art, text, markup))
    b.say = lambda text, markup=None: said.append((text, markup))
    b.draft_post()
    assert seen["topics"] == ["giant pumpkin", "barcelona", "dog", content.EVERGREEN.title]
    art, text, markup = sent[-1]
    assert art == b"\xff\xd8robot" and "1. joke one" in text and "2. joke two" in text
    did = next(iter(b.drafts))
    assert [x["callback_data"] for x in markup["inline_keyboard"][0]] == [f"dp:{did}.0", f"dp:{did}.1"]
    assert markup["inline_keyboard"][2] == [{"text": "Skip", "callback_data": f"ds:{did}"}]

    monkeypatch.setattr(social, "upload_image", lambda keys, data, ext, mime: "MEDIA")
    monkeypatch.setattr(social, "post", lambda keys, text, media_id="", reply_to="", quote_id="":
                        posts.append((text, media_id)) or "99")
    b.draft_click("dp", f"{did}.1")
    assert posts == [("joke two", "MEDIA")] and said[-1][0] == "Posted: https://x.com/i/status/99"
    assert said[-1][1]["inline_keyboard"][0][0]["callback_data"] == "dx:99"
    assert json_loads(b.ledger.get("posted_texts")) == ["joke two"] and not b.drafts
    b.draft_click("dt", f"{did}.0")                                       # already used: expired
    assert "expired" in said[-1][0] and len(posts) == 1
    # the next draft avoids the topic it just used and sees what was posted
    b.draft_post()
    assert "dog" not in seen["topics"] and seen["recent"] == ["joke two"]
    did = next(iter(b.drafts))
    b.draft_click("dt", f"{did}.0")
    assert posts[-1] == ("joke one", "") and b.ledger.count_today("x_post") == 2
    b.draft_post()
    did = next(iter(b.drafts))
    b.draft_click("dp", f"{did}.0")                                       # daily cap: kept for later
    assert "cap reached" in said[-1][0] and did in b.drafts and len(posts) == 2
    b.draft_click("ds", did)
    assert not b.drafts
    deleted = []
    monkeypatch.setattr(social, "delete", lambda keys, pid: deleted.append(pid))
    b.draft_click("dx", "99")
    assert deleted == ["99"]


def json_loads(s):
    import json

    return json.loads(s)


def test_launch_records_and_reports(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from devbot import bot as bot_mod, concept, pump

    b = _bare_bot(tmp_path, live=True, pinata_jwt="jwt", website_base="", disclosure="", dev_buy_sol=0.0,
                  slippage_pct=10, priority_fee_sol=0.0)
    src = tr.Coin("s", "Phasor", "PHASOR", "c", time.time() - 120, 9000, 0, False)
    c = concept.Concept(tr.Narrative(["phasor"], 9, 1, 0.0, 1.0, 1, 1, 0, [src], live="LIVE"), "sidekick",
                        "Baby Phasor", "BABYPHAS", "d", "p", "w")
    p = bot_mod.Proposal(c, b"\xff\xd8art", image_uri="ipfs://img")
    said, markups = [], []
    b.say = lambda text, markup=None: said.append(text) or markups.append(markup)
    b.wallet, b.dev, b.rpc = "W", None, None
    b.verdict = lambda: SimpleNamespace(ok=True)
    b.cost = lambda: 0.01
    b.announce = lambda c, mint, art, quote_id="": "777"
    monkeypatch.setattr(pump, "pin_metadata", lambda jwt, **kw: "ipfs://meta")
    monkeypatch.setattr(pump, "cid_of", lambda u: u)
    monkeypatch.setattr(pump, "create", lambda *a, **k: SimpleNamespace(mint="MINT", signature="SIG", confirmed=True))
    b.launch(p)
    assert said[-1].startswith("LIVE: Baby Phasor ($BABYPHAS)") and "x.com/i/status/777" in said[-1]
    assert "$PHASOR was 2.0 min old" in said[-1] and b.ledger.launches(1)[0][0] == "MINT"


def _half_signed(msg, signed_by, blank_for):
    from solders.message import to_bytes_versioned
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction
    keys = list(msg.account_keys)
    sigs = [Signature.default()] * msg.header.num_required_signatures
    sigs[keys.index(signed_by.pubkey())] = signed_by.sign_message(to_bytes_versioned(msg))
    return VersionedTransaction.populate(msg, sigs)


def test_partial_sign_keeps_the_launchpads_signature_and_fills_ours():
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction
    from devbot import launchpads

    dev, mint = Keypair(), Keypair()
    ix = Instruction(Pubkey.default(), b"", [AccountMeta(dev.pubkey(), True, True), AccountMeta(mint.pubkey(), True, True)])
    msg = MessageV0.try_compile(dev.pubkey(), [ix], [], Hash.default())
    theirs = _half_signed(msg, mint, dev)  # the launchpad signed the mint; ours is still blank
    signed = launchpads.sign_partial(bytes(theirs), dev)
    keys = list(signed.message.account_keys)
    from solders.message import to_bytes_versioned
    body = to_bytes_versioned(signed.message)
    for kp in (dev, mint):
        assert signed.signatures[keys.index(kp.pubkey())].verify(kp.pubkey(), body)
    import pytest
    with pytest.raises(RuntimeError):
        launchpads.sign_partial(bytes(theirs), Keypair())


def test_jupiter_launch_uploads_then_submits_and_confirms_by_our_signature(monkeypatch):
    import base64, json
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction
    from devbot import launchpads

    dev, mint = Keypair(), Keypair()
    ix = Instruction(Pubkey.default(), b"", [AccountMeta(dev.pubkey(), True, True), AccountMeta(mint.pubkey(), True, True)])
    unsigned = _half_signed(MessageV0.try_compile(dev.pubkey(), [ix], [], Hash.default()), mint, dev)
    calls = []

    def post_json(url, payload, headers=None, timeout=60):
        calls.append(("POST", url, payload, headers))
        return json.dumps({"transaction": base64.b64encode(bytes(unsigned)).decode(), "mint": str(mint.pubkey()),
                           "imagePresignedUrl": "https://up/img", "metadataPresignedUrl": "https://up/meta",
                           "imageUrl": "https://static/img"}).encode()

    def request(method, url, body=None, headers=None, timeout=30):
        calls.append((method, url, body, headers))
        return b"{}"

    monkeypatch.setattr(launchpads.http, "post_json", post_json)
    monkeypatch.setattr(launchpads.http, "request", request)
    monkeypatch.setattr(launchpads.image_mod, "kind", lambda b: ("jpg", "image/jpeg"))

    class FakeRpc:
        def confirm(self, sig, timeout_s=75):
            calls.append(("CONFIRM", sig))
            return True

    pad = launchpads.build("jupiter", jupiter_api_key="k")
    out = pad.launch(FakeRpc(), dev, name="N", symbol="S", description="d", image=b"img")
    assert out.mint == str(mint.pubkey()) and out.confirmed
    assert [c[0] for c in calls] == ["POST", "PUT", "PUT", "POST", "CONFIRM"]
    assert calls[0][2]["creator"] == str(dev.pubkey()) and calls[0][3] == {"x-api-key": "k"}
    assert json.loads(calls[2][2])["image"] == "https://static/img"
    assert calls[-1][1] == out.signature
    assert launchpads.build("pump") is None
    import pytest
    with pytest.raises(ValueError):
        launchpads.build("jupiter")
    with pytest.raises(ValueError):
        launchpads.build("heaven")


def test_base58_matches_solana_addresses():
    from solders.keypair import Keypair
    from devbot import launchpads
    assert launchpads.b58encode(bytes(32)) == "1" * 32 and launchpads.b58decode("1" * 32) == bytes(32)
    kp = Keypair()
    assert launchpads.b58encode(bytes(kp.pubkey())) == str(kp.pubkey())
    assert launchpads.b58decode(str(kp.pubkey())) == bytes(kp.pubkey())


def _bags_world(monkeypatch, *, needs_creation=True, bundles=False, positions=(), deployer_lamports=0):
    """A fake Bags + RPC: records every call in order and returns documented response shapes."""
    import json
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from devbot import launchpads

    dev, mint = Keypair(), Keypair()

    def tx_b58(signed_by_mint=True):
        ix = Instruction(Pubkey.default(), b"", [AccountMeta(dev.pubkey(), True, True), AccountMeta(mint.pubkey(), True, True)])
        t = _half_signed(MessageV0.try_compile(dev.pubkey(), [ix], [], Hash.default()), mint, dev)
        return launchpads.b58encode(bytes(t))

    calls = []

    def ok(response):
        return json.dumps({"success": True, "response": response}).encode()

    def post_json(url, payload, headers=None, timeout=60):
        calls.append(("POST", url.replace(launchpads.BAGS, ""), payload))
        if url == launchpads.JITO:
            return json.dumps({"result": ["TIPACCT"] if payload["method"] == "getTipAccounts" else "bundleid"}).encode()
        path = url.replace(launchpads.BAGS, "")
        if path == "/fee-share/config":
            item = {"blockhash": {"blockhash": "x", "lastValidBlockHeight": 1}, "transaction": tx_b58()}
            tipped = "tipWallet" in payload
            return ok({"needsCreation": needs_creation, "feeShareAuthority": "a", "meteoraConfigKey": "CFG",
                       "transactions": [] if bundles else [item], "bundles": [[item, item]] if bundles and tipped else
                       ([[item]] if bundles else [])})
        if path == "/token-launch/create-launch-transaction":
            return ok(tx_b58())
        if path == "/token-launch/claim-txs/v3":
            return ok([{"tx": tx_b58(), "blockhash": {}}])
        if path == "/fee-share/deployer/claim-txs":
            return ok({"transactions": [{"transaction": tx_b58(), "blockhash": {}}]})
        raise AssertionError(path)

    def request(method, url, body=None, headers=None, timeout=30):
        path = url.replace(launchpads.BAGS, "")
        calls.append((method, path, body))
        if path.startswith("/token-launch/create-token-info"):
            return ok({"tokenMint": str(mint.pubkey()), "tokenMetadata": "ipfs://meta", "tokenLaunch": {}})
        if path.startswith("/token-launch/claimable-positions"):
            return ok([{"baseMint": m, "totalClaimableLamportsUserShare": l} for m, l in positions])
        if path.startswith("/fee-share/deployer/claimable-positions"):
            return ok({"totalClaimableLamports": str(deployer_lamports)})
        raise AssertionError(path)

    monkeypatch.setattr(launchpads.http, "post_json", post_json)
    monkeypatch.setattr(launchpads.http, "request", request)
    monkeypatch.setattr(launchpads.image_mod, "kind", lambda b: ("jpg", "image/jpeg"))

    class FakeRpc:
        def send(self, tx):
            calls.append(("SEND", str(tx.signatures[0])))
            return str(tx.signatures[0])

        def confirm(self, sig, timeout_s=75):
            return True

    return dev, mint, calls, FakeRpc()


def test_bags_launch_makes_config_then_launches_with_the_dev_buy(monkeypatch):
    from devbot import launchpads
    dev, mint, calls, rpc = _bags_world(monkeypatch)
    out = launchpads.build("bags", bags_api_key="k").launch(rpc, dev, name="N", symbol="s", description="d",
                                                            image=b"img", dev_buy_sol=0.05)
    assert out.mint == str(mint.pubkey()) and out.confirmed
    steps = [c[0] if c[0] == "SEND" else c[1] for c in calls]
    assert steps == ["/token-launch/create-token-info", "/fee-share/config", "SEND",
                     "/token-launch/create-launch-transaction", "SEND"]
    cfg = calls[1][2]
    assert cfg["claimersArray"] == [str(dev.pubkey())] and cfg["basisPointsArray"] == [10_000]
    assert cfg["baseMint"] == str(mint.pubkey()) and "tipWallet" not in cfg
    launch = calls[3][2]
    assert launch == {"ipfs": "ipfs://meta", "tokenMint": str(mint.pubkey()), "wallet": str(dev.pubkey()),
                      "initialBuyLamports": 50_000_000, "configKey": "CFG"}


def test_bags_skips_config_that_exists_and_tips_bundles(monkeypatch):
    from devbot import launchpads
    dev, mint, calls, rpc = _bags_world(monkeypatch, needs_creation=False)
    launchpads.build("bags", bags_api_key="k").launch(rpc, dev, name="N", symbol="S", description="d", image=b"i")
    assert [c[0] for c in calls].count("SEND") == 1  # only the launch tx

    dev, mint, calls, rpc = _bags_world(monkeypatch, bundles=True)
    launchpads.build("bags", bags_api_key="k").launch(rpc, dev, name="N", symbol="S", description="d", image=b"i")
    configs = [c[2] for c in calls if c[1] == "/fee-share/config"]
    assert "tipWallet" not in configs[0] and configs[1]["tipWallet"] == "TIPACCT" and configs[1]["tipLamports"] == 100_000
    sent = [c for c in calls if c[1] == launchpads.JITO]
    assert [c[2]["method"] for c in sent] == ["getTipAccounts", "sendBundle"]
    assert len(sent[1][2]["params"][0]) == 2  # both bundle txs, base58


def test_bags_bps_must_sum_and_errors_surface(monkeypatch):
    import pytest
    from devbot import launchpads
    dev, mint, calls, rpc = _bags_world(monkeypatch)
    pad = launchpads.Bags(api_key="k", fee_claimers=(("A", 5000), ("B", 4000)))
    with pytest.raises(ValueError):
        pad.launch(rpc, dev, name="N", symbol="S", description="d", image=b"i")
    monkeypatch.setattr(launchpads.http, "request",
                        lambda *a, **k: b'{"success": false, "error": "bad key"}')
    with pytest.raises(RuntimeError, match="bad key"):
        launchpads.build("bags", bags_api_key="k").unclaimed_sol(dev)


def test_bags_claims_creator_and_deployer_fees(monkeypatch):
    from devbot import launchpads
    dev, mint, calls, rpc = _bags_world(monkeypatch, positions=[("M1", 2_000_000_000), ("M2", 0)],
                                        deployer_lamports=500_000_000)
    pad = launchpads.build("bags", bags_api_key="k")
    assert pad.unclaimed_sol(dev) == 2.5
    calls.clear()
    pad.claim(rpc, dev)
    assert [c[0] if c[0] == "SEND" else c[1] for c in calls] == ["/token-launch/claimable-positions?wallet=" + str(dev.pubkey()),
                                     "/token-launch/claim-txs/v3", "SEND", "/fee-share/deployer/claim-txs", "SEND"]
    assert calls[1][2] == {"feeClaimer": str(dev.pubkey()), "tokenMint": "M1"}  # M2 has nothing: skipped


def test_jupiter_refuses_a_dev_buy_it_cannot_make():
    import pytest
    from devbot import launchpads
    pad = launchpads.build("jupiter", jupiter_api_key="k")
    with pytest.raises(ValueError, match="dev buy"):
        pad.launch(None, None, name="N", symbol="S", description="", image=b"", dev_buy_sol=0.1)
    assert pad.unclaimed_sol(None) == 0.0


def test_live_variant_is_a_riff_not_a_lookalike(monkeypatch):
    import random

    from devbot import concept

    src = tr.Coin("m", "Phasor", "PHASOR", "c", NOW, 9000, 0, False, image="https://ipfs.io/ipfs/bafy")
    live = tr.Narrative(["phasor"], 9, 1, 0.0, 1.0, 1, 1, 0, [src], live="LIVE: Phasor")
    for name, sym in (("Phas0r", "PHAS0R"), ("Phasor 2", "PHASOR2"), ("Phasorr", "PSR"), ("Evil Phasor", "PHASR")):
        assert concept.lookalike(name, sym, [("Phasor", "PHASOR")]), (name, sym)
    for name, sym in (("Baby Phasor", "BABYPHASOR"), ("Mrs Phasor", "MRSPHASOR"), ("Phasor CEO", "PHASORCEO")):
        assert not concept.lookalike(name, sym, [("Phasor", "PHASOR")]), (name, sym)

    def cands(*cs):
        return {"candidates": [{"narrative_index": 0, "description": "d", "image_prompt": "p", "why": "w",
                                "name": n, "symbol": s, **({"twist": t} if t else {})} for n, s, t in cs]}

    reply = cands(("Phas0r", "PHAS0R", "a hat"), ("Baby Phasor", "BABYPHAS", ""), ("Baby Phasor", "BABYPHAS", "as a baby"))
    monkeypatch.setattr(concept, "_ask", lambda *a, **k: reply)
    c = concept.generate("a", "t", "m", [live], "sidekick", [], rng=random.Random(0))
    assert concept._norm(c.name) == "babyphasor" and c.twist == "as a baby"   # lookalike and twistless skipped
    # a story narrative keeps the old rule: no twist, distinctive tickers blocked
    story = tr.Narrative(["phasor"], 9, 1, 0.0, 1.0, 1, 1, 0, [src])
    monkeypatch.setattr(concept, "_ask", lambda *a, **k: cands(("Laser Cat", "LZCAT", "x")))
    assert concept.generate("a", "t", "m", [story], "sidekick", [], rng=random.Random(0)).twist == ""


def _png(w, h):
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(out, "WEBP")
    return out.getvalue()


def test_variant_edits_the_source_picture_and_falls_back(monkeypatch):
    import base64
    import io
    import json

    from PIL import Image

    from devbot import image

    calls = []

    def fake(method, url, *, body=None, headers=None, **k):
        calls.append(url)
        if "gateway.pinata.cloud" in url:
            return _png(1000, 800)                                  # a big webp, as coins often have
        if image.EDIT_MODEL in url:
            assert b'name="input_image_0"; filename="source.png"' in body and b"tiny crown" in body
            src = body.split(b"\r\n\r\n", 4)[-1]
            assert Image.open(io.BytesIO(src[:src.rindex(b"\r\n--")])).size == (512, 410)
            return json.dumps({"result": {"image": base64.b64encode(b"\x89PNGedited").decode()}}).encode()
        raise AssertionError(url)

    monkeypatch.setattr(http, "request", fake)
    out = image.variant("acct", "tok", "https://ipfs.io/ipfs/bafy", "a tiny crown")
    assert out == b"\x89PNGedited" and calls[0] == "https://gateway.pinata.cloud/ipfs/bafy"

    def cf_down(method, url, *, body=None, headers=None, **k):
        if image.EDIT_MODEL in url:
            raise http.HttpError(429, "out of neurons")
        if url == image.POLLINATIONS_EDITS:
            assert b'name="image"; filename="source.png"' in body and b"klein" in body and headers["Authorization"] == "Bearer sk"
            return json.dumps({"data": [{"b64_json": base64.b64encode(b"\xff\xd8pollinations").decode()}]}).encode()
        return _png(400, 400)

    monkeypatch.setattr(http, "request", cf_down)
    assert image.variant("acct", "tok", "https://ipfs.io/ipfs/bafy", "a crown", pollinations_token="sk") == b"\xff\xd8pollinations"
    import pytest
    with pytest.raises(RuntimeError, match="Workers AI"):
        image.variant("acct", "tok", "https://ipfs.io/ipfs/bafy", "a crown")   # keyless Pollinations can't edit


def test_proposal_art_says_what_it_is(tmp_path, monkeypatch):
    from devbot import concept, image

    b = _bare_bot(tmp_path, cf_account_id="a", cf_api_token="t", image_fallback=True, pollinations_token="")
    src = tr.Coin("m", "Phasor", "PHASOR", "c", NOW, 9000, 0, False, image="https://x/img.png")
    live = tr.Narrative(["phasor"], 9, 1, 0.0, 1.0, 1, 1, 0, [src], live="LIVE")
    c = concept.Concept(live, "sidekick", "Baby Phasor", "BABYPHAS", "d", "p", "w", twist="as a baby")
    monkeypatch.setattr(image, "variant", lambda *a, **k: b"edited")
    monkeypatch.setattr(image, "generate", lambda *a, **k: b"fresh")
    assert b.art(c) == (b"edited", "$PHASOR's picture, edited: as a baby")
    monkeypatch.setattr(image, "variant", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Workers AI: 429")))
    art, note = b.art(c)
    assert art == b"fresh" and note.startswith("fresh art (editing $PHASOR's picture failed: Workers AI: 429")


def _legacy_tx_b64(payer, *cosigners):
    """What dbc.mjs returns: an unsigned LEGACY transaction (base64), fee payer first. Takes pubkeys or keypairs."""
    import base64
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.transaction import Transaction
    pk = lambda k: k.pubkey() if hasattr(k, "pubkey") else Pubkey.from_string(k)  # noqa: E731
    ix = Instruction(Pubkey.default(), b"", [AccountMeta(pk(k), True, True) for k in (payer, *cosigners)])
    return base64.b64encode(bytes(Transaction.new_unsigned(Message.new_with_blockhash([ix], pk(payer), Hash.default())))).decode()


def test_sign_partial_signs_every_requested_slot_of_a_legacy_transaction():
    import base64
    import pytest
    from solders.keypair import Keypair
    from solders.message import to_bytes_versioned
    from devbot import launchpads
    dev, mint = Keypair(), Keypair()
    signed = launchpads.sign_partial(base64.b64decode(_legacy_tx_b64(dev, mint)), dev, mint)
    body = to_bytes_versioned(signed.message)
    keys = list(signed.message.account_keys)
    for kp in (dev, mint):
        assert signed.signatures[keys.index(kp.pubkey())].verify(kp.pubkey(), body)
    with pytest.raises(RuntimeError, match="not a required signer"):
        launchpads.sign_partial(base64.b64decode(_legacy_tx_b64(dev)), Keypair())


def _meteora(monkeypatch, tmp_path, fees=None, mints=("M1", "M2")):
    import json
    from solders.keypair import Keypair
    from devbot import launchpads
    dev = Keypair()
    calls = []

    class Done:
        returncode, stderr = 0, ""

        def __init__(self, out):
            self.stdout = json.dumps(out)

    def run(cmd, input=None, **kw):
        req, name = json.loads(input), cmd[2]
        calls.append((name, req))
        if name in ("config", "pool", "claim"):
            extra = [req["baseMint"]] if name == "pool" else []
            return Done({"tx": _legacy_tx_b64(dev, *extra), "pool": "POOL"})
        if name == "fees":
            return Done({"pools": fees or []})
        raise AssertionError(name)

    monkeypatch.setattr(launchpads.subprocess, "run", run)
    monkeypatch.setattr(launchpads.pump, "pin_image", lambda jwt, sym, img: "https://ipfs.io/ipfs/IMG")
    monkeypatch.setattr(launchpads.pump, "pin_metadata", lambda jwt, **kw: "https://ipfs.io/ipfs/META")
    sent = []

    class FakeRpc:
        def blockhash(self):
            return "11111111111111111111111111111111"

        def send(self, tx):
            sent.append(tx)
            return str(tx.signatures[0])

        def confirm(self, sig, timeout_s=75):
            return True

    pad = launchpads.build("meteora", dbc_config="CFG", pinata_jwt="j", rpc_url="http://rpc", mints=lambda: list(mints))
    return pad, dev, calls, sent, FakeRpc()


def test_meteora_launch_builds_a_pool_with_the_dev_buy_and_signs_dev_and_mint(monkeypatch, tmp_path):
    from solders.message import to_bytes_versioned
    pad, dev, calls, sent, rpc = _meteora(monkeypatch, tmp_path)
    out = pad.launch(rpc, dev, name="N", symbol="S", description="d", image=b"i", dev_buy_sol=0.05)
    name, req = calls[0]
    assert name == "pool" and req["config"] == "CFG" and req["buyLamports"] == 50_000_000
    assert req["uri"] == "https://ipfs.io/ipfs/META" and req["payer"] == str(dev.pubkey()) and req["rpc"] == "http://rpc"
    assert out.mint == req["baseMint"] and out.confirmed
    tx = sent[0]
    body, keys = to_bytes_versioned(tx.message), [str(k) for k in tx.message.account_keys]
    assert keys[0] == str(dev.pubkey()) and req["baseMint"] in keys[:2]
    assert all(sig.verify(k, body) for sig, k in zip(tx.signatures, tx.message.account_keys))  # both slots signed


def test_meteora_fees_and_claim_skip_empty_and_errored_pools(monkeypatch, tmp_path):
    fees = [{"mint": "M1", "pool": "P1", "partnerQuote": "2000000000", "creatorQuote": "0"},
            {"mint": "M2", "pool": "P2", "error": "account not found"},
            {"mint": "M3", "pool": "P3", "partnerQuote": "0", "creatorQuote": "0"}]
    pad, dev, calls, sent, rpc = _meteora(monkeypatch, tmp_path, fees=fees)
    assert pad.unclaimed_sol(dev) == 2.0
    calls.clear()
    pad.claim(rpc, dev)
    assert [c[0] for c in calls] == ["fees", "claim"] and calls[1][1]["baseMint"] == "M1"
    assert len(sent) == 1
    pad2, dev2, calls2, _, _ = _meteora(monkeypatch, tmp_path, mints=())
    assert pad2.unclaimed_sol(dev2) == 0.0 and calls2 == []  # no launches: no helper call


def test_meteora_needs_a_config_and_a_pinata_key():
    import pytest
    from devbot import launchpads
    with pytest.raises(ValueError, match="DBC_CONFIG"):
        launchpads.build("meteora", pinata_jwt="j")
    with pytest.raises(ValueError, match="DBC_CONFIG"):
        launchpads.build("meteora", dbc_config="C")


def test_ledger_remembers_which_mints_a_launchpad_made(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    led.record_pad_mint("meteora", "A")
    led.record_pad_mint("meteora", "A")
    led.record_pad_mint("bags", "B")
    assert led.pad_mints("meteora") == ["A"] and led.pad_mints("bags") == ["B"] and led.pad_mints("x") == []


def test_dbc_helper_builds_a_valid_config_offline():
    """The real Node helper + SDK: the curve we ship must pass the SDK's own validators."""
    import base64, json, shutil, subprocess
    import pytest
    from solders.keypair import Keypair
    from devbot import launchpads
    if not shutil.which("node") or not (launchpads.METEORA_DIR / "node_modules").exists():
        pytest.skip("node or meteora/node_modules missing (cd meteora && npm ci --ignore-scripts)")
    dev, cfg = Keypair(), Keypair()
    pad = launchpads.MeteoraDbc(config="", pinata_jwt="", rpc_url="http://127.0.0.1:1")
    built = pad._node("config", {"blockhash": "11111111111111111111111111111111", "payer": str(dev.pubkey()),
                                 "config": str(cfg.pubkey()), "curve": pad.curve()})
    signed = launchpads.sign_partial(base64.b64decode(built["tx"]), dev, cfg)
    assert str(signed.message.account_keys[0]) == str(dev.pubkey())
    with pytest.raises(RuntimeError, match="meteora config"):
        pad._node("config", {"blockhash": "11111111111111111111111111111111", "payer": str(dev.pubkey()),
                             "config": str(cfg.pubkey()), "curve": {**pad.curve(), "migrationMarketCap": 1}})


def _v0_tx_b64(payer, *cosigners):
    """What launchlab.mjs returns: an unsigned v0 transaction (base64). Takes pubkeys or keypairs."""
    import base64
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction
    pk = lambda k: k.pubkey() if hasattr(k, "pubkey") else Pubkey.from_string(k)  # noqa: E731
    ix = Instruction(Pubkey.default(), b"", [AccountMeta(pk(k), True, True) for k in (payer, *cosigners)])
    msg = MessageV0.try_compile(pk(payer), [ix], [], Hash.default())
    return base64.b64encode(bytes(VersionedTransaction.populate(
        msg, [Signature.default()] * msg.header.num_required_signatures))).decode()


def _launchlab(monkeypatch, unclaimed=None, **kw):
    import json
    from solders.keypair import Keypair
    from devbot import launchpads
    dev = Keypair()
    calls, sent = [], []

    class Done:
        returncode, stderr = 0, ""

        def __init__(self, out):
            self.stdout = json.dumps(out)

    def run(cmd, input=None, **kwargs):
        req, name = json.loads(input), cmd[2]
        calls.append((name, req))
        if name == "launch":
            return Done({"txs": [_v0_tx_b64(dev, req["mint"])], "platformId": req["platformId"]})
        if name == "unclaimed":
            return Done(unclaimed or {"lamports": "0", "platformLamports": "0", "own": False})
        if name == "platform-create":
            return Done({"txs": [_v0_tx_b64(dev)], "platformId": "PLAT"})
        return Done({"txs": [_v0_tx_b64(dev)]})

    class FakeRpc:
        def send(self, tx):
            sent.append(tx)
            return str(tx.signatures[0])

        def confirm(self, sig, timeout_s=75):
            return True

    monkeypatch.setattr(launchpads.subprocess, "run", run)
    monkeypatch.setattr(launchpads.pump, "pin_image", lambda jwt, sym, img: "https://ipfs.io/ipfs/IMG")
    monkeypatch.setattr(launchpads.pump, "pin_metadata", lambda jwt, **k: "https://ipfs.io/ipfs/META")
    pad = launchpads.build(kw.pop("name", "launchlab"), pinata_jwt="j", rpc_url="http://rpc", **kw)
    return pad, dev, calls, sent, FakeRpc()


def test_launchlab_launch_signs_dev_and_mint_and_prices_the_priority_fee(monkeypatch):
    from solders.message import to_bytes_versioned
    pad, dev, calls, sent, rpc = _launchlab(monkeypatch, launchlab_platform="PLATFORM1")
    out = pad.launch(rpc, dev, name="N", symbol="S", description="d", image=b"i", dev_buy_sol=0.05)
    name, req = calls[0]
    assert name == "launch" and req["platformId"] == "PLATFORM1" and req["buyLamports"] == 50_000_000
    assert req["microLamports"] == 125_000 and req["uri"] == "https://ipfs.io/ipfs/META" and req["rpc"] == "http://rpc"
    assert out.mint == req["mint"] and out.confirmed
    tx = sent[0]
    body = to_bytes_versioned(tx.message)
    assert all(sig.verify(k, body) for sig, k in zip(tx.signatures, tx.message.account_keys))


def test_launchlab_platform_choice_and_missing_pinata():
    import pytest
    from devbot import launchpads
    assert launchpads.build("letsbonk", pinata_jwt="j", rpc_url="r").platform_id == launchpads.LETSBONK_PLATFORM
    assert launchpads.build("launchlab", pinata_jwt="j", rpc_url="r").platform_id == ""  # Raydium's own
    with pytest.raises(ValueError, match="PINATA_JWT"):
        launchpads.build("launchlab")


def test_launchlab_claims_creator_and_own_platform_fees_only_when_there_are_some(monkeypatch):
    import pytest
    pad, dev, calls, sent, rpc = _launchlab(
        monkeypatch, unclaimed={"lamports": "2000000000", "platformLamports": "500000000", "own": True},
        launchlab_platform="PLAT")
    assert pad.unclaimed_sol(dev) == 2.5
    calls.clear()
    pad.claim(rpc, dev)
    assert [c[0] for c in calls] == ["unclaimed", "claim-creator", "claim-platform"] and len(sent) == 2
    assert calls[2][1]["platformId"] == "PLAT"

    pad, dev, calls, sent, rpc = _launchlab(monkeypatch, unclaimed={"lamports": "7", "platformLamports": "0", "own": False})
    pad.claim(rpc, dev)
    assert [c[0] for c in calls] == ["unclaimed", "claim-creator"]  # not our platform: never asks to claim its fees

    pad, dev, calls, sent, rpc = _launchlab(monkeypatch)
    with pytest.raises(RuntimeError, match="nothing was claimable"):
        pad.claim(rpc, dev)
    assert not sent  # an empty vault would make the on-chain claim fail; we never build it


def test_launchlab_platform_goes_to_the_platform_wallet_not_the_creator(monkeypatch):
    pad, dev, calls, sent, rpc = _launchlab(monkeypatch)
    assert pad.make_platform(rpc, dev, name="n", web="w", img="i", fee_rate=5000, creator_fee_rate=5000) == "PLAT"
    req = calls[0][1]
    assert req["lockScales"] == [1_000_000, 0, 0]  # any creator share is rejected by the program for new platforms
    assert req["feeRate"] == 5000 and req["creatorFeeRate"] == 5000 and len(sent) == 1


def test_launchlab_helper_builds_a_real_launch_transaction():
    """The real Node helper + Raydium SDK against mainnet RPC (read-only). Opt in: DEVBOT_NET_TESTS=1."""
    import base64, os, shutil
    import pytest
    from solders.keypair import Keypair
    from devbot import launchpads
    if os.environ.get("DEVBOT_NET_TESTS") != "1":
        pytest.skip("set DEVBOT_NET_TESTS=1 to run tests that read mainnet")
    if not shutil.which("node") or not (launchpads.RAYDIUM_DIR / "node_modules").exists():
        pytest.skip("node or raydium/node_modules missing (cd raydium && npm ci --ignore-scripts)")
    dev, mint = Keypair(), Keypair()
    pad = launchpads.build("launchlab", pinata_jwt="j", rpc_url="https://api.mainnet-beta.solana.com")
    built = pad._node("launch", {"payer": str(dev.pubkey()), "mint": str(mint.pubkey()), "name": "T", "symbol": "T",
                                 "uri": "https://x/m.json", "platformId": "", "buyLamports": 10_000_000})
    tx = launchpads.sign_partial(base64.b64decode(built["txs"][0]), dev, mint)
    assert str(tx.message.account_keys[0]) == str(dev.pubkey()) and built["platformId"]


def test_coin_from_your_own_idea(tmp_path, monkeypatch):
    import random

    import pytest

    from devbot import concept, image, scanner

    calls = []

    def ask(acct, tok, model, prompt, system=""):
        calls.append(("cf", system))
        raise concept.ServiceError("Workers AI: 429 out of neurons")

    reply = {"candidates": [{"narrative_index": 0, "name": "Bead Off", "symbol": "WSAM", "description": "d",
                             "image_prompt": "p", "why": "w"},
                            {"narrative_index": 0, "name": "Bead Off", "symbol": "BEAD", "description": "water just leaves",
                             "image_prompt": "a jacket water bounces off", "why": "w", "story": "the reel"}]}
    monkeypatch.setattr(concept, "_ask", ask)
    monkeypatch.setattr(concept, "_ask_openrouter", lambda key, models, prompt, system="": calls.append(("or", system)) or reply)
    c = concept.from_idea("a", "t", "m", "arcteryx jacket in a reel: water spills and just beads off", ["WSAM"],
                          rng=random.Random(1), openrouter_key="k")
    assert concept._norm(c.symbol) == "bead" and c.angle == "idea" and c.story == "the reel"
    assert c.narrative.live.startswith("your idea: arcteryx jacket") and not c.narrative.examples
    assert [k for k, _ in calls] == ["cf", "or"] and all(sysp == concept.IDEA_SYSTEM for _, sysp in calls)
    assert "never be named after the brand" in concept.IDEA_SYSTEM
    with pytest.raises(concept.ConceptError):
        concept.from_idea("a", "t", "m", " ", [])

    b = _bare_bot(tmp_path, cf_account_id="a", cf_api_token="t", llm_model="m", openrouter_key="k",
                  openrouter_models=(), image_fallback=True, pollinations_token="")
    monkeypatch.setattr(concept, "from_idea", lambda *a, **k: c)
    monkeypatch.setattr(scanner, "symbol_taken", lambda s, n: False)
    monkeypatch.setattr(image, "generate", lambda *a, **k: b"\xff\xd8art")
    p = b.idea("the arcteryx reel")
    assert p.concept is c and p.image == b"\xff\xd8art" and p.art_note == "fresh art" and p.image_uri == ""
    shown = []
    b.photo = lambda art, caption, markup=None: shown.append((caption, markup))
    b.offer(p)
    caption, markup = shown[-1]
    assert "Bead Off" in caption and "your idea: arcteryx" in caption
    assert [x["text"] for x in markup["inline_keyboard"][0]] == ["Launch", "Skip"]


def test_drafts_offer_post_buttons_and_no_quotes(tmp_path, monkeypatch):
    from devbot import content, social

    topics = [content.Topic("google", "monday", ["everyone hates mondays"], 5000)]
    good = {"topic_index": 0, "image": "none", "image_instruction": "",
            "jokes": ['monday -> "an agent did your monday before you woke up"', "my agent says i need to set boundaries"]}
    d = content.check(good, topics)
    assert d.jokes == ["an agent did your monday before you woke up"]   # arrow kept the variant; old shape dropped
    assert content.clean("we're so *aligned* man holy shit") == "we're so aligned man holy shit"
    assert "quote" not in content.SYSTEM.lower()

    b = _bare_bot(tmp_path, x_keys=social.XKeys("ck", "cs", "at", "as"), x_max_posts_per_day=20)
    monkeypatch.setattr(content, "draft", lambda *a, **k: d)
    b.topics = lambda: topics
    b.cfg.openrouter_key, b.cfg.openrouter_models, b.cfg.llm_model = "", (), "m"
    b.cfg.cf_account_id = b.cfg.cf_api_token = "x"
    said = []
    b.say = lambda text, markup=None: said.append((text, markup))
    b.draft_post()
    text, markup = said[-1]
    assert [x["text"] for x in markup["inline_keyboard"][0]] == ["Post 1"]
    assert markup["inline_keyboard"][0][0]["callback_data"].startswith("dp:")
    assert text.startswith("X draft (google: monday)")


def test_j7tracker_deploys_our_mint_with_a_checked_session(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import pytest
    from solders.keypair import Keypair

    from devbot import bot as bot_mod, concept, j7, pump

    mint = Keypair()
    sent = []

    def fake(method, url, *, body=None, headers=None, **k):
        sent.append((method, url, body, headers))
        if url.endswith("/api/session-check"):
            if headers["Authorization"] != "Bearer good":
                raise http.HttpError(401, '{"reason":"no_session"}')
            return json.dumps({"username": "memephil", "expiresAt": 1_900_000_000_000}).encode()
        return json.dumps({"type": "token_create_success", "mint_address": str(mint.pubkey()), "signature": "SIG"}).encode()

    monkeypatch.setattr(http, "request", fake)
    monkeypatch.setattr(j7, "_landed", lambda rpc, sigs: "SIG")
    monkeypatch.setattr(j7.time, "sleep", lambda s: None)
    out = j7.deploy(None, j7.BASES["nyc"], "good", "ENCKEY", mint, name="Bead Off", symbol="BEAD", description="d",
                    image=b"\xff\xd8art", website="https://w", twitter="https://x.com/i/status/1")
    assert out == pump.Launched(str(mint.pubkey()), "SIG", True)
    method, url, body, headers = sent[-1]
    b = json.loads(body)
    assert url == "https://tx-nyc.j7tracker.io/deploy/submit" and headers["Authorization"] == "Bearer good"
    assert b["mode"] == "pump" and b["external"] is True and b["api_key"] == "ENCKEY" and b["private_desc"] is True
    assert b["vanity_mint_keypair"] == str(mint) and b["image_url"].startswith("data:image/jpeg;base64,")
    assert not {"bundle", "bundle_wallets", "sniper_wallets", "auto_sell"} & set(b)      # plain deploys only

    monkeypatch.setattr(http, "request", lambda *a, **k: json.dumps(
        {"type": "token_create_success", "mint_address": "OTHER", "signature": "S"}).encode())
    with pytest.raises(j7.J7Error, match="not our mint"):
        j7.deploy(None, j7.BASES["nyc"], "good", "K", mint, name="n", symbol="S", description="d", image=b"\xff\xd8")
    monkeypatch.setattr(http, "request", lambda *a, **k: (_ for _ in ()).throw(http.HttpError(401, "{}")))
    with pytest.raises(j7.J7Error, match="/j7"):
        j7.deploy(None, j7.BASES["nyc"], "old", "K", mint, name="n", symbol="S", description="d", image=b"\xff\xd8")

    # /j7 <access token>: the message is deleted, a bad token is refused, a good one is kept until it expires
    import base64
    jwt = lambda exp: "eyJhbGciOiJFUzI1NiJ9." + base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=") + ".sig"
    good = jwt(time.time() + 3600)

    def fake2(method, url, *, body=None, headers=None, **k):
        if url.endswith("/api/session-check") and headers["Authorization"] != f"Bearer {good}":
            raise http.HttpError(401, '{"reason":"no_session"}')
        return fake(method, url, body=body, headers={**headers, "Authorization": "Bearer good"}, **k)

    monkeypatch.setattr(http, "request", fake2)
    bt = _bare_bot(tmp_path, launcher="j7", j7_region="nyc", j7_wallet_key="ENCKEY", website_base="", disclosure="",
                   dev_buy_sol=0.0, slippage_pct=10, priority_fee_sol=0.0)
    calls, said = [], []
    bt.tg = lambda method, payload, timeout=40: calls.append((method, payload)) or {}
    bt.say = lambda text, markup=None: said.append(text)
    bt.set_j7_session({"chat": {"id": 5}, "message_id": 9}, "dAcTnotajwt")
    assert calls[-1] == ("deleteMessage", {"chat_id": 5, "message_id": 9}) and "starts eyJ" in said[-1]
    bt.set_j7_session({"chat": {"id": 5}, "message_id": 10}, jwt(time.time() + 3600) + "x")   # j7tracker refuses it
    assert "Not saved" in said[-1] and bt.ledger.get("j7_access") is None
    bt.set_j7_session({"chat": {"id": 5}, "message_id": 11}, good)
    assert bt.j7_session() == good and "saved for memephil, good until" in said[-1]
    bt.ledger.put("j7_access", json.dumps({"token": good, "expires": time.time() + 30}))
    with pytest.raises(RuntimeError, match="expired"):
        bt.j7_session()
    bt.ledger.put("j7_access", json.dumps({"token": good, "expires": time.time() + 3600}))

    # a launch with LAUNCHER=j7 sends our pre-made mint through j7tracker, not PumpPortal
    src = tr.Coin("s", "Phasor", "PHASOR", "c", time.time() - 60, 9000, 0, False)
    c = concept.Concept(tr.Narrative(["phasor"], 9, 1, 0.0, 1.0, 1, 1, 0, [src], live="LIVE"), "idea",
                        "Bead Off", "BEAD", "d", "p", "w")
    p = bot_mod.Proposal(c, b"\xff\xd8art", mint=mint, image_uri="https://ipfs.io/ipfs/IMGCID")
    bt.wallet, bt.dev, bt.rpc = "W", None, None
    bt.verdict = lambda: SimpleNamespace(ok=True)
    bt.cost = lambda: 0.01
    bt.announce = lambda c, m, art, quote_id="": "777"
    monkeypatch.setattr(pump, "create", lambda *a, **k: pytest.fail("PumpPortal used with LAUNCHER=j7"))
    monkeypatch.setattr(pump, "pin_metadata", lambda *a, **k: pytest.fail("j7tracker hosts the metadata"))
    bt.launch(p)
    b = json.loads(sent[-1][2])
    assert b["twitter"] == "https://x.com/i/status/777" and b["vanity_mint_keypair"] == str(mint)
    assert said[-1].startswith("LIVE: Bead Off ($BEAD)") and bt.ledger.pins(str(mint.pubkey())) == ["IMGCID"]


def _tweet(**kw):
    t = {"id": "1", "type": "TWEET", "isReply": False, "isRetweet": False, "createdAt": int(time.time() * 1000) - 5000,
         "text": "my cat just learned to open the fridge", "tweetUrl": "https://x.com/big/status/1",
         "author": {"handle": "big", "name": "Big", "followersCount": 2_000_000}, "media": {"images": [], "videos": []}}
    t.update(kw)
    return t


def test_feed_keeps_fresh_original_posts_from_big_accounts():
    from devbot import feed

    def why(**kw):
        t = _tweet(**kw)
        return feed.worth_a_coin(t, feed.to_post(t), 200_000, 60)

    assert why() == ""
    assert why(type="REPLY", isReply=True) == "not an original post"
    assert why(type="RETWEET", isRetweet=True) == "not an original post"
    assert why(author={"handle": "small", "followersCount": 5000}).startswith("@small has 5,000")
    assert why(createdAt=int(time.time() * 1000) - 120_000).endswith("s old")
    assert why(createdAt="2020-01-01T00:00:00.000Z").endswith("s old")          # ISO timestamps parse too
    assert why(contractAddress="So1...") == "already shills a coin"
    assert why(text="Trump announces new tariffs on cheese") == "grim or political"
    assert why(text="the funeral was beautiful") == "grim or political"
    assert why(text="https://t.co/x") == "nothing to make a coin of"


def test_feed_events_queue_the_newest_post_within_the_hourly_cap():
    from devbot import feed

    designed = []
    f = feed.Feed("tok", lambda p: designed.append(p) or p.id != "6", min_followers=200_000, max_age_s=60,
                  max_per_hour=2)
    f.on_tweet(_tweet(id="1"))
    f.on_tweet(_tweet(id="1", text="an update"))                     # same id: ignored
    f.on_tweet(_tweet(id="2", type="REPLY", isReply=True))           # filtered
    f.on_suggestion({"tweet_id": "1", "prediction": "Fridge Cat\u2060"})
    f.on_copies({"tweet_id": "1", "results": [{}] * 5})
    f.on_copies({"tweet_id": "1", "results": [{}] * 3})
    p = f.posts["1"]
    assert p.text == "my cat just learned to open the fridge" and p.suggestion == "Fridge Cat" and p.copies == 5
    assert f.todo.qsize() == 1
    for i in range(3, 7):
        f.on_tweet(_tweet(id=str(i), text=f"post {i}"))
    import threading
    t = threading.Thread(target=f._work, daemon=True)
    t.start()
    time.sleep(1.5)
    for i in (7, 8):
        f.on_tweet(_tweet(id=str(i), text=f"post {i}"))
    time.sleep(1.5)
    for i in (9, 10):
        f.on_tweet(_tweet(id=str(i), text=f"post {i}"))
        time.sleep(1.5)
    f.stopped.set()
    # newest of each burst; "6" fails to design so it uses no slot; the cap of 2 then stops "10"
    assert [p.id for p in designed] == ["6", "8", "9"]


def test_feed_sends_reply_drafts_as_reply_on_x_links(tmp_path, monkeypatch):
    import threading
    from urllib.parse import parse_qs, urlparse

    from devbot import bot as bot_mod, content, feed

    def why(**kw):
        t = _tweet(**kw)
        return feed.worth_a_reply(t, feed.to_post(t), 100_000, 300)

    assert why() == ""
    assert why(type="REPLY", isReply=True) == "not an original post"
    assert why(author={"handle": "small", "followersCount": 5000}).startswith("@small has")
    assert why(createdAt=int(time.time() * 1000) - 400_000).endswith("s old")
    assert why(text="gm https://t.co/x") == "too little text to reply to"
    assert why(text="Trump announces new tariffs on cheese") == "grim or political"

    # the model passing (fit false) or writing a copy, a link, a ticker or an overlong line leaves nothing
    post = "my cat just learned to open the fridge"
    assert content.check_replies({"fit": False, "replies": ["lol"]}, post) == []
    good = "an agent will open the fridge for you, the cat is just early"
    assert content.check_replies({"fit": True, "replies": [
        "my cat just learned to open the fridge", "buy $FRIDGE", "see fridge.com", "x" * 201, good, good]}, post) == [good]
    asked = []
    monkeypatch.setattr(content, "_ask", lambda *a, system, schema, **k: asked.append((system, a[3]))
                        or {"fit": True, "replies": [good]})
    assert content.replies("a", "t", "m", "big", 2_000_000, post, ["old one"]) == [good]
    assert asked[0][0] == content.REPLY_SYSTEM and '"recent_replies_do_not_repeat": ["old one"]' in asked[0][1]

    # the feed hands fresh posts to the reply writer only while replying, newest of a burst, within the caps
    written = []
    f = feed.Feed("tok", lambda p: False, min_followers=200_000, max_age_s=60, max_per_hour=6,
                  reply=lambda p: written.append(p.id) or p.id != "3", reply_min_followers=100_000,
                  reply_max_age_s=300, reply_max_per_hour=1)
    f.replying = False
    f.on_tweet(_tweet(id="1"))
    assert f.reply_todo.qsize() == 0
    f.replying = True
    f.on_tweet(_tweet(id="2", text="a different post about my cat and the fridge"))
    f.on_tweet(_tweet(id="3", text="the newest post about my cat and the fridge"))
    threading.Thread(target=f._reply_work, daemon=True).start()
    time.sleep(1.5)
    for i in ("4", "5"):
        f.on_tweet(_tweet(id=i, text=f"post number {i} about my cat and the fridge"))
        time.sleep(1.5)
    f.stopped.set()
    assert written == ["3", "4"] and len(f.asked) == 2   # "3" passed (no slot); "4" used the hour's one draft

    # a draft is Telegram text plus one "Reply N on X" link per reply, opening X as a reply to the post
    b = _bare_bot(tmp_path, cf_account_id="a", cf_api_token="t", llm_model="m", openrouter_key="", openrouter_models=(),
                  x_replies_per_hour=4, x_reply_min_followers=100_000, x_reply_max_age_s=300)
    b.feed, b.feed_error = None, ""
    said = []
    b.say = lambda text, markup=None: said.append((text, markup))
    monkeypatch.setattr(content, "replies", lambda *a, **k: [good, "second take"])
    assert b.reply_draft(feed.to_post(_tweet(id="99")))
    text, markup = said[-1]
    assert text.startswith("REPLY: @big (2,000,000 followers)") and "https://x.com/big/status/1" in text
    buttons = markup["inline_keyboard"][0]
    assert [x["text"] for x in buttons] == ["Reply 1 on X", "Reply 2 on X"]
    q = parse_qs(urlparse(buttons[0]["url"]).query)
    assert buttons[0]["url"].startswith("https://x.com/intent/post?") and q == {"in_reply_to": ["99"], "text": [good]}
    monkeypatch.setattr(content, "replies", lambda *a, **k: [])
    assert not b.reply_draft(feed.to_post(_tweet(id="100"))) and len(said) == 1

    # /replies off|on, quiet hours
    b.command("/replies off")
    assert b.ledger.get("replies") == "0" and not b.replies_on() and said[-1][0] == "replies off (/replies on)"
    b.command("/replies on")
    assert b.replies_on() and said[-1][0].startswith("replies on: up to 4 drafts an hour")
    day = 1_760_000_000 - 1_760_000_000 % 86400
    assert bot_mod.quiet(day + 7 * 3600, (6, 12)) and not bot_mod.quiet(day + 13 * 3600, (6, 12))
    assert bot_mod.quiet(day + 23 * 3600, (22, 4)) and not bot_mod.quiet(day + 5 * 3600, (22, 4))


def test_feed_post_becomes_a_quoting_proposal(tmp_path, monkeypatch):
    import json

    from devbot import concept, feed, image, scanner, social

    b = _bare_bot(tmp_path, cf_account_id="a", cf_api_token="t", llm_model="m", openrouter_key="", openrouter_models=(),
                  image_fallback=True, pollinations_token="", launcher="j7", live=True, pinata_jwt="jwt",
                  feed_max_copies=4)
    b.feed_avoid = ["WSAM"]
    seen = {}

    def from_post(acct, tok, model, handle, followers, text, obvious, avoid, **k):
        seen.update(handle=handle, obvious=obvious, avoid=avoid)
        n = tr.Narrative(["x"], 0, 0, 0.0, 0.0, 0, 0, 0, live=f"@{handle}'s post: {text}")
        return concept.Concept(n, "idea", "Fridge Burglar", "FRIDGE", "d", "p", "w")

    monkeypatch.setattr(concept, "from_post", from_post)
    monkeypatch.setattr(scanner, "symbol_taken", lambda s, n: False)
    monkeypatch.setattr(image, "generate", lambda *a, **k: b"\xff\xd8art")
    monkeypatch.setattr(bot_pin := __import__("devbot.pump", fromlist=["pin_image"]), "pin_image",
                        lambda *a, **k: pytest_fail())
    shown = []
    b.photo = lambda art, caption, markup=None: shown.append(caption)
    post = feed.to_post(_tweet())
    post.suggestion, post.copies = "Fridge Cat", 5
    b.design(post)
    assert not b.proposals and not shown                              # swarmed: 5 copies > FEED_MAX_COPIES
    post.copies = 3
    b.design(post)
    assert seen == {"handle": "big", "obvious": "Fridge Cat", "avoid": ["WSAM"]}
    (pid, p), = b.proposals.items()
    assert p.quote_id == "1" and p.image_uri == ""                    # j7tracker takes the art inline: no pin
    assert p.source == "@big" and p.source_url == "https://x.com/big/status/1"
    assert shown[-1].startswith("FEED: @big (2,000,000 followers) posted") and 'obvious name "Fridge Cat" (3 copies already)' in shown[-1]

    # the launch post asks X to quote the source post (and falls back to plain when X refuses)
    sent = []
    monkeypatch.setattr(http, "request", lambda m, url, *, body=None, headers=None, **k:
                        sent.append((url, body)) or json.dumps({"data": {"id": "9"}}).encode())
    b.cfg.x_keys, b.cfg.x_max_posts_per_day, b.x_error = social.XKeys("a", "b", "c", "d"), 20, ""
    monkeypatch.setattr(social, "upload_image", lambda *a, **k: "")
    assert b.announce(p.concept, "MINT", p.image, p.quote_id) == "9"
    assert json.loads(sent[-1][1])["quote_tweet_id"] == "1"


def pytest_fail():
    import pytest
    pytest.fail("pinned art for a j7tracker launch")


def test_launch_post_falls_back_when_x_refuses_the_quote(tmp_path, monkeypatch):
    import json

    from devbot import concept, social

    b = _bare_bot(tmp_path, x_keys=social.XKeys("a", "b", "c", "d"), x_max_posts_per_day=20)
    b.x_error = ""
    b.say = lambda *a, **k: None
    monkeypatch.setattr(social, "upload_image", lambda *a, **k: "M")
    bodies = []

    def fake(method, url, *, body=None, headers=None, **k):
        p = json.loads(body)
        bodies.append(p)
        if "quote_tweet_id" in p:
            raise http.HttpError(403, '{"detail":"You can only reply to or quote posts where you are mentioned or are the author."}')
        if "CA: " in p["text"]:
            raise http.HttpError(403, '{"detail":"Crypto addresses are prohibited"}')
        return json.dumps({"data": {"id": "42"}}).encode()

    monkeypatch.setattr(http, "request", fake)
    c = concept.Concept(tr.Narrative(["x"], 0, 0, 0.0, 0.0, 0, 0, 0), "idea", "Fridge Burglar", "FRIDGE", "d", "p", "w")
    assert b.announce(c, "MINTADDRESS", b"\xff\xd8", "123") == "42"
    assert ["quote_tweet_id" in p for p in bodies] == [True, False, False]
    assert "CA: MINTADDRESS" in bodies[1]["text"] and "CA:" not in bodies[2]["text"] and bodies[2]["media"]["media_ids"] == ["M"]
    assert b.ledger.count_today("x_post") == 1


def test_humans_scorecard_compares_feed_coins_with_the_rest(tmp_path, monkeypatch):
    import collections

    from devbot import audience
    from devbot.bot import FEED_SOURCE

    b = _bare_bot(tmp_path)
    b.rpc, b.wallet, b.wallet_kinds = None, "W", {}
    now = time.time()
    for i, (sym, src) in enumerate((("FEED1", FEED_SOURCE), ("FEED2", FEED_SOURCE), ("OLD", "PHASOR"))):
        b.ledger.record_launch(mint=f"m{i}", name=sym, symbol=sym, narrative="n", source_mint="s", source_name="n",
                               source_symbol=src, uri="", signature="sig", confirmed=True, dev_buy_sol=0)
    made = {"m0": audience.Audience(collections.Counter(bot=4, human=2), 98.0, 1.0, 19),
            "m1": audience.Audience(collections.Counter(bot=2), None, 0.0, 0),
            "m2": audience.Audience(collections.Counter(bot=10), None, 0.0, 0)}
    monkeypatch.setattr(audience, "audience", lambda rpc, mint, w, ts, cache: made[mint])
    out = b.humans(10)
    assert "$FEED1 [feed]" in out and "19 buys after 60s" in out and "$OLD (" in out
    assert "feed coins: 2 coins, 4.0 buyers and 1.0 humans per coin, 1 with buying after 60s" in out
    assert "other coins: 1 coins, 10.0 buyers and 0.0 humans per coin, 0 with buying after 60s" in out


def test_coin_links_the_x_profile_when_the_launch_post_failed(tmp_path):
    from devbot import social

    b = _bare_bot(tmp_path, x_handle="0xmemephil")
    assert b.twitter_link("123") == social.post_url("123")
    assert b.twitter_link("") == "https://x.com/0xmemephil"
    b.cfg.x_handle = ""
    assert b.twitter_link("") == ""


def test_pvp_clones_the_hottest_coin_exactly_and_only_once(tmp_path, monkeypatch):
    import io

    from PIL import Image

    from devbot import http, image
    from devbot.ledger import CLONE_SOURCE

    b = _bare_bot(tmp_path, pvp_min_buyers=8, pvp_max_copies=2, fresh_minutes=5.0, live=False)
    b.wallet, b.pvp_seen, b.pvp_error, b.next_pvp = "OURS", set(), "", 0.0
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 10, 10)).save(buf, "PNG")
    monkeypatch.setattr(http, "request", lambda m, url, **k: buf.getvalue())

    def coin(mint, creator="them"):
        return tr.Coin(mint, "Moneyball", "MBALL", creator, time.time() - 90, 12_000, 0, False,
                       "A real story. Deployed using https://j7tracker.io https://x.com/orig", "https://x.com/orig",
                       "https://ipfs.io/ipfs/Qm")

    def hot(*cs):
        return [tr.Narrative([c.symbol.lower()], 11, copies + 1, 0.0, 3.0, copies + 1, 1, 0, examples=[c], live="LIVE")
                for c, copies in cs], []

    shown = []
    b.photo = lambda art, caption, markup=None: shown.append((art, caption, markup))
    b.ledger.put("pvp", "off")
    monkeypatch.setattr(tr, "hot_now", lambda *a, **k: pytest_fail())
    b.pvp_tick()                                    # off: not even a look at pump.fun
    b.ledger.put("pvp", "ask")
    monkeypatch.setattr(tr, "hot_now", lambda *a, **k: hot((coin("own", "OURS"), 0), (coin("crowded"), 3),
                                                            (coin("m1"), 1)))
    b.pvp_tick()
    (pid, p), = b.proposals.items()
    c = p.concept
    assert (c.name, c.symbol, p.clone, c.angle) == ("Moneyball", "MBALL", True, "clone")
    assert c.description == "A real story" and "orig" not in c.description        # their links never travel
    assert Image.open(io.BytesIO(p.image)).size == (64, 64) and "unchanged" in p.art_note
    assert shown[-1][1].startswith("PVP") and [x["text"] for x in shown[-1][2]["inline_keyboard"][0]] == ["Launch", "Skip"]
    b.pvp_tick()                                    # the same source is not offered twice
    assert len(b.proposals) == 1 and b.pvp_seen == {"m1"}

    assert not b.ledger.cloned("m1")
    b.ledger.record_launch(mint="C1", name="Moneyball", symbol="MBALL", narrative="x (clone)", source_mint="m1",
                           source_name="Moneyball", source_symbol=CLONE_SOURCE, uri="", signature="s",
                           confirmed=True, dev_buy_sol=0.0)
    assert b.ledger.cloned("m1") and not b.ledger.cloned("m2")


def test_text_providers_groq_first_pollinations_last(monkeypatch):
    import json

    from devbot import concept, http, openrouter

    cand = {"narrative_index": 0, "story": "s", "name": "Cat Agent", "symbol": "CATAG", "description": "d",
            "image_prompt": "p", "why": "w"}
    calls = []

    def post_json(url, body, headers=None, timeout=0):
        calls.append(url.split("/")[2])
        if "groq" in url and "fail-groq" in calls:
            raise http.HttpError(429, "rate limited")
        if "cloudflare" in url or "openrouter" in url:
            raise http.HttpError(429, "daily quota")
        reply = json.dumps({"candidates": [cand]} if "candidates" in json.dumps(body) else {"jokes": []})
        return json.dumps({"choices": [{"message": {"content": reply}}]}).encode()

    monkeypatch.setattr(http, "post_json", post_json)
    try:
        openrouter.configure("gsk", (), "sk", ())
        c = concept.from_idea("a", "t", "m", "a cat adopted an agent", [], openrouter_key="or")
        assert c.symbol.upper() == "CATAG" and calls == ["api.groq.com"]
        calls[:] = ["fail-groq"]                       # Groq limited: Workers AI, OpenRouter, then Pollinations
        concept.from_idea("a", "t", "m", "a cat adopted an agent", [], openrouter_key="or")
        assert calls[1] == "api.groq.com" and calls[-1] == "gen.pollinations.ai" and "openrouter.ai" in calls
        openrouter.configure()                          # nothing configured: the old Workers AI error
        try:
            concept.from_idea("a", "t", "m", "a cat adopted an agent", [])
            raise AssertionError("expected the Workers AI error")
        except concept.ConceptError as e:
            assert "Workers AI" in str(e)
    finally:
        openrouter.configure()


def test_dev_buy_setting_sell_buttons_and_sell(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from devbot import pump

    b = _bare_bot(tmp_path, dev_buy_sol=0.0, slippage_pct=10, priority_fee_sol=0.0, live=True)
    said, markups = [], []
    b.say = lambda text, markup=None: said.append(text) or markups.append(markup)
    b.recent_sells, b.wallet, b.dev = {}, "W", None
    b.cost = lambda: 0.01 + b.dev_buy()
    b.command("/devbuy 0.05")
    assert b.dev_buy() == 0.05 and "0.05 SOL per launch" in said[-1] and "Sell" in said[-1]
    b.command("/devbuy 5")                                   # over MAX_DEV_BUY_SOL: refused, unchanged
    assert b.dev_buy() == 0.05 and said[-1].startswith("/devbuy")
    b.ledger.record_launch(mint="MINT", name="hamsterstack", symbol="HSTK", narrative="", source_mint="",
                           source_name="", source_symbol="", uri="", signature="S", confirmed=True, dev_buy_sol=0.05)
    bal = iter([0.10, 0.13])
    b.rpc = SimpleNamespace(balance_sol=lambda w: next(bal))
    sent = []
    monkeypatch.setattr(pump, "sell", lambda rpc, dev, mint, pct, **kw: sent.append((mint, pct))
                        or pump.Launched(mint, "SIG", True))
    b.tg = lambda *a, **k: None
    b.callback({"id": "q", "data": "sl:MINT.50"})
    assert sent == [("MINT", 50)] and "+0.0300" in said[-1]
    assert abs(b.ledger.totals()["sold_sol"] - 0.03) < 1e-9
    b.callback({"id": "q", "data": "sl:MINT.100"})              # a second tap right away is held back
    assert sent == [("MINT", 50)] and "Already selling" in said[-1]
    b.callback({"id": "q", "data": "sl:MINT.33"})               # only the offered percentages
    b.command("/sell NOPE 50")
    assert sent == [("MINT", 50)] and "isn't one of our launches" in said[-1]


def test_feed_auto_launches_only_first_fresh_posts_within_caps(tmp_path):
    import queue

    from devbot import feed

    b = _bare_bot(tmp_path, feed_auto_sol_per_day=0.0, feed_auto_max_per_hour=3, feed_auto_max_age_s=45.0)
    said, launched, offered = [], [], []
    b.say = lambda text, markup=None: said.append(text)
    b.auto_queue, b.auto_sent = queue.Queue(), []
    b.cost = lambda: 0.01
    b.launch = lambda p, auto=False: launched.append((p, auto)) or b.ledger.record_spend(0.01, "auto launch X") or True
    b.offer = lambda p, header="": offered.append(header)
    b.guarded = lambda what, fn, *a: fn(*a)
    post = lambda copies=0, age=10: feed.Post("1", "nasa", "NASA", 9e7, "t", "u", time.time() - age, copies=copies)

    b.command("/feed auto 0.025")
    assert b.feed_auto() == 0.025 and b.feed_auto_cap == 0.025 and "on" in said[-1]
    b.auto_queue.put(("p1", post(), "H"))
    b.auto_queue.put(("p2", post(copies=1), "H"))          # someone coined it meanwhile
    b.auto_queue.put(("p3", post(age=60), "H"))            # too old by now
    b.auto_queue.put(("p4", post(), "H"))
    b.auto_queue.put(("p5", post(), "H"))                  # 0.02 spent + 0.01 > 0.025
    import types
    # proposals are stand-ins with a concept.symbol
    items = list(b.auto_queue.queue)
    b.auto_queue = queue.Queue()
    for name, pst, h in items:
        b.auto_queue.put((types.SimpleNamespace(concept=types.SimpleNamespace(symbol=name)), pst, h))
    b.auto_tick()
    assert [p.concept.symbol for p, auto in launched] == ["p1", "p4"] and all(a for _, a in launched)
    assert any("1 copies appeared" in o for o in offered) and any("60s old" in o for o in offered)
    assert any("auto budget" in o for o in offered) and len(offered) == 3
    b.auto_sent, launched[:] = [], []                      # a floor refusal doesn't use up the hour
    b.ledger.put("feed_auto_sol", "1")
    b.launch = lambda p, auto=False: launched.append(p) and False
    for i in range(4):
        b.auto_queue.put((types.SimpleNamespace(concept=types.SimpleNamespace(symbol=f"r{i}")), post(), "H"))
    b.auto_tick()
    assert len(launched) == 4 and b.auto_sent == []
    b.command("/feed auto off")
    assert b.feed_auto() == 0 and "off" in said[-1]


# ---- wallet rotation and the sniper auto-sell --------------------------------------------------


def _transfer_of(tx):
    """(fee payer, from, to, lamports) of a one-transfer transaction."""
    msg = tx.message
    keys = msg.account_keys
    ix = msg.instructions[0]
    assert bytes(ix.data)[:4] == bytes([2, 0, 0, 0])            # SystemProgram::Transfer
    return (str(keys[0]), str(keys[ix.accounts[0]]), str(keys[ix.accounts[1]]),
            int.from_bytes(bytes(ix.data)[4:12], "little"))


class _ChainRpc:
    """Balances, sends and confirms for the wallets module."""

    def __init__(self, balances):
        self.balances, self.sent = dict(balances), []

    def blockhash(self):
        from solders.hash import Hash
        return Hash.default()

    def send(self, tx):
        payer, src, dst, lam = _transfer_of(tx)
        assert tx.verify_with_results() and all(tx.verify_with_results())  # every required signer signed
        self.sent.append((payer, src, dst, lam))
        self.balances[src] = self.balances.get(src, 0) - lam
        self.balances[dst] = self.balances.get(dst, 0) + lam
        return f"sig{len(self.sent)}"

    def confirm(self, sig, timeout_s=75):
        return True

    def balance_sol(self, pubkey):
        return self.balances.get(pubkey, 0) / 1e9

    def call(self, method, params):
        if method == "getBalance":
            return {"value": self.balances.get(params[0], 0)}
        if method == "getMultipleAccounts":
            return {"value": [{"lamports": self.balances[a]} if self.balances.get(a) else None for a in params[0]]}
        raise AssertionError(method)


def test_rotated_wallets_are_private_files_and_sweep_to_exactly_zero(tmp_path):
    import os

    from solders.keypair import Keypair

    from devbot import wallets

    store = wallets.Wallets(tmp_path / "wallets")
    w = store.new()
    path = tmp_path / "wallets" / f"{w.pubkey()}.json"
    assert os.stat(path).st_mode & 0o777 == 0o600 and os.stat(tmp_path / "wallets").st_mode & 0o777 == 0o700
    assert store.pubkeys() == [str(w.pubkey())] and store.get(str(w.pubkey())).pubkey() == w.pubkey()

    main = Keypair()
    rpc = _ChainRpc({str(main.pubkey()): 10**9})
    wallets.fund(rpc, main, w.pubkey(), 0.02)
    assert rpc.sent[-1] == (str(main.pubkey()), str(main.pubkey()), str(w.pubkey()), 20_000_000)
    assert wallets.top_up(rpc, main, w) is None                  # 0.02 already above the 0.002 top-up
    moved, _ = wallets.sweep(rpc, main, w)
    # main pays the sweep's fee, so the rotated wallet goes to exactly 0, not to a dust balance
    assert moved == 0.02 and rpc.sent[-1] == (str(main.pubkey()), str(w.pubkey()), str(main.pubkey()), 20_000_000)
    assert rpc.balances[str(w.pubkey())] == 0 and wallets.sweep(rpc, main, w) is None
    wallets.top_up(rpc, main, w)
    assert rpc.balances[str(w.pubkey())] == 2_000_000
    other = str(Keypair().pubkey())
    assert wallets.lamports_of(rpc, [str(w.pubkey()), other]) == {str(w.pubkey()): 2_000_000, other: 0}


def test_curve_real_sol_reads_offset_32():
    import base64

    from devbot import pump

    data = bytes(8) + b"".join(x.to_bytes(8, "little") for x in (1_073_000_000 * 10**6, 37_900_800_000,
                                                                  793_100_000 * 10**6, 7_900_800_000))

    class Rpc:
        def call(self, method, params):
            assert method == "getAccountInfo"
            return {"value": {"data": [base64.b64encode(data).decode(), "base64"]}}

    assert pump.curve_real_sol(Rpc(), "11111111111111111111111111111111") == 7.9008


def test_launch_rotates_the_creator_and_sells_into_the_snipers(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from solders.keypair import Keypair

    from devbot import bot as bot_mod, concept, pump, wallets

    b = _bare_bot(tmp_path, live=True, pinata_jwt="jwt", website_base="", priority_fee_sol=0.0, slippage_pct=10,
                  disclosure="Dev wallet: {wallet}", dev_buy_sol=0.05, rotate_wallets=True,
                  autosell_pct=25, autosell_min_sol=0.05, autosell_watch_s=5)
    b.wallets = wallets.Wallets(tmp_path / "wallets")
    b.dev, b.wallet, b.rpc, b.recent_sells = Keypair(), "MAIN", None, {}
    said = []
    b.say = lambda text, markup=None: said.append(text)
    b.verdict = lambda: SimpleNamespace(ok=True)
    b.cost = lambda: 0.06
    b.announce = lambda c, mint, art, quote_id="": "777"
    funded, topped, sold, made = [], [], [], {}
    monkeypatch.setattr(wallets, "fund", lambda rpc, main, to, sol: funded.append((main, str(to), sol)))
    monkeypatch.setattr(wallets, "top_up", lambda rpc, main, w, sol=0.002: topped.append(str(w.pubkey())))
    monkeypatch.setattr(pump, "pin_metadata", lambda jwt, **kw: made.update(kw) or "ipfs://meta")
    monkeypatch.setattr(pump, "cid_of", lambda u: u)

    def create(rpc, dev, mint, **kw):
        made["creator"] = str(dev.pubkey())
        return SimpleNamespace(mint=str(mint.pubkey()), signature="SIG", confirmed=True)

    monkeypatch.setattr(pump, "create", create)
    reads = iter([None, 0.05, 0.08, 0.31])          # curve not visible yet, our own buy, a little, then snipers
    monkeypatch.setattr(pump, "curve_real_sol", lambda rpc, mint: next(reads))
    monkeypatch.setattr(bot_mod, "AUTOSELL_POLL_S", 0)
    b.rpc = SimpleNamespace(balance_sol=lambda w: 0.01)

    def sell(rpc, key, mint, pct, **kw):
        sold.append((str(key.pubkey()), mint, pct))
        return SimpleNamespace(signature="SELL", confirmed=True)

    monkeypatch.setattr(pump, "sell", sell)
    src = tr.Coin("s", "Phasor", "PHASOR", "c", time.time() - 60, 9000, 0, False)
    c = concept.Concept(tr.Narrative(["phasor"], 9, 1, 0.0, 1.0, 1, 1, 0, [src], live="LIVE"), "idea",
                        "Bead Off", "BEAD", "d", "p", "w")
    p = bot_mod.Proposal(c, b"\xff\xd8art", image_uri="ipfs://img")
    assert b.launch(p)
    (fresh,) = b.wallets.pubkeys()
    mint = str(p.mint.pubkey())
    assert made["creator"] == fresh != str(b.dev.pubkey())        # the NEW wallet signed the create
    assert f"Dev wallet: {fresh}" in made["description"]          # and the disclosure names it, not the main wallet
    assert funded == [(b.dev, fresh, 0.06 + 0.05 * 0.02 + bot_mod.FUND_MARGIN_SOL)]
    assert b.ledger.get(f"creator:{mint}") == fresh and b.creator_of(mint) == fresh
    assert sold == [(fresh, mint, 25)] and topped == [fresh]      # sold from the creator wallet, at 0.26 SOL outside
    assert any("0.260 SOL of outside buying" in t for t in said)

    # no snipers within the window: nothing is sold
    sold.clear()
    monkeypatch.setattr(pump, "curve_real_sol", lambda rpc, m: 0.05)
    b.cfg.autosell_watch_s = 0.01
    b.watch_snipers(mint, "BEAD")
    assert sold == [] and "no auto-sell" in said[-1]


def test_claim_visits_each_rotated_wallet_worth_claiming(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from solders.keypair import Keypair

    from devbot import pump, wallets

    b = _bare_bot(tmp_path, priority_fee_sol=0.0)
    b.wallets = wallets.Wallets(tmp_path / "wallets")
    rich, poor = b.wallets.new(), b.wallets.new()
    b.dev, b.wallet = Keypair(), "MAIN"
    b.rpc = SimpleNamespace(balance_sol=lambda w: 1.0)
    b.say = lambda text, markup=None: None
    owed = {str(rich.pubkey()): 0.004, str(poor.pubkey()): 0.0004}
    monkeypatch.setattr(b, "rotated_unclaimed", lambda: owed)
    monkeypatch.setattr(pump, "unclaimed_fees_sol", lambda rpc, creator: 0.0)
    claimed, swept = [], []
    monkeypatch.setattr(pump, "claim_creator_fees", lambda rpc, key, fee: claimed.append(str(key.pubkey())) or "C")
    monkeypatch.setattr(wallets, "top_up", lambda rpc, main, w, sol=0.002: None)
    monkeypatch.setattr(wallets, "sweep", lambda rpc, main, w: swept.append(str(w.pubkey())) or (0.004, "S"))
    out = b.claim()
    assert claimed == [str(rich.pubkey())] == swept and "solscan.io/tx/C" in out  # main had 0, poor not worth it
    owed[str(rich.pubkey())] = 0.0
    assert "not worth the tx fees" in b.claim()


def test_rotation_needs_pumpportal_and_pump(monkeypatch, tmp_path):
    import pytest

    from devbot import config

    for k, v in {"TELEGRAM_ALLOWED_USERS": "1", "DEVBOT_TELEGRAM_TOKEN": "t", "TELEGRAM_HOME_CHAT": "1",
                 "DEV_KEYPAIR_PATH": "k", "CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "c",
                 "DEVBOT_STATE": str(tmp_path), "ROTATE_WALLETS": "1", "AUTOSELL_PCT": "25"}.items():
        monkeypatch.setenv(k, v)
    cfg = config.load()
    assert cfg.rotate_wallets and cfg.autosell_pct == 25 and cfg.autosell_min_sol == 0.05
    monkeypatch.setenv("LAUNCHER", "j7")
    monkeypatch.setenv("J7_WALLET_KEY", "x")
    with pytest.raises(config.ConfigError, match="ROTATE_WALLETS"):
        config.load()
    monkeypatch.setenv("LAUNCHER", "pumpportal")
    monkeypatch.setenv("LAUNCHPAD", "bags")
    with pytest.raises(config.ConfigError, match="ROTATE_WALLETS"):
        config.load()
    monkeypatch.setenv("ROTATE_WALLETS", "0")
    with pytest.raises(config.ConfigError, match="AUTOSELL_PCT"):
        config.load()


# ---- disclosed launch-block treasury (treasury.py, jito.py) --------------------------------

def _b58encode(data: bytes) -> str:
    """Only the tests need to encode base58 (PumpPortal's bundle form is what decodes it)."""
    from devbot.pump import B58_ALPHABET

    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58_ALPHABET[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + (out or "1")


def _unsigned_tx(payer, *others):
    """A serialized transaction with blank signatures, the shape PumpPortal answers with. `payer` is
    the first required signer and each of `others` one more, like a create (mint, then the creator)."""
    from solders.message import MessageV0
    from solders.hash import Hash
    from solders.signature import Signature
    from solders.system_program import TransferParams, transfer
    from solders.transaction import VersionedTransaction

    ixs = [transfer(TransferParams(from_pubkey=k.pubkey(), to_pubkey=payer.pubkey(), lamports=1))
           for k in (payer, *others)]
    msg = MessageV0.try_compile(payer.pubkey(), ixs, [], Hash.default())
    blanks = [Signature.default()] * msg.header.num_required_signatures
    return bytes(VersionedTransaction.populate(msg, blanks))


def test_base58_decode_matches_solders():
    import pytest

    from solders.pubkey import Pubkey

    from devbot import pump

    dev = "DZo2AkoC418KFiqFDwqwwsiWicVX6wDJHFHgRvh3j1RS"
    assert pump.b58decode(dev) == bytes(Pubkey.from_string(dev))
    assert pump.b58decode("11111111111111111111111111111111") == bytes(32)   # leading '1' is a zero byte
    with pytest.raises(ValueError):
        pump.b58decode("0OIl")                                               # not in the alphabet


def test_bundle_txs_signs_each_transaction_with_its_own_wallet(monkeypatch):
    import pytest

    import json

    from solders.keypair import Keypair

    from devbot import pump

    dev, buyer = Keypair(), Keypair()
    mint = Keypair()
    blobs = [_b58encode(_unsigned_tx(mint, dev)), _b58encode(_unsigned_tx(buyer))]
    posted = {}

    def post(url, payload, **_):
        posted["url"], posted["payload"] = url, payload
        return json.dumps(blobs).encode()

    monkeypatch.setattr(pump.http, "post_json", post)
    txs = pump.bundle_txs([{"action": "create"}, {"action": "buy"}], [[mint, dev], [buyer]])
    assert posted["url"] == pump.PORTAL and isinstance(posted["payload"], list)   # one post, a list of actions
    assert len(txs) == 2 and len(txs[0].signatures) == 2 and len(txs[1].signatures) == 1

    monkeypatch.setattr(pump.http, "post_json", lambda u, p, **k: json.dumps(blobs[:1]).encode())
    with pytest.raises(RuntimeError, match="asked for 2"):
        pump.bundle_txs([{"a": 1}, {"b": 2}], [[dev], [buyer]])
    monkeypatch.setattr(pump.http, "post_json", lambda u, p, **k: json.dumps({"error": "nope"}).encode())
    with pytest.raises(RuntimeError, match="did not return a bundle"):
        pump.bundle_txs([{"a": 1}], [[dev]])


def test_metadata_carries_the_treasury_only_when_there_is_one():
    from devbot import pump

    assert "treasury" not in pump.metadata(name="N", symbol="S", description="d", image_uri="i")
    m = pump.metadata(name="N", symbol="S", description="d", image_uri="i", treasury=["A", "B"])
    assert m["treasury"] == ["A", "B"]


def test_manifest_needs_a_rule_and_sorts_wallets(tmp_path):
    import pytest

    import json

    from devbot import treasury

    with pytest.raises(ValueError, match="rule"):
        treasury.manifest(["B"], dev_wallet="D", rule="   ")
    man = treasury.manifest(["B", "A"], dev_wallet="D", rule=" hold ", updated=0)
    assert man["treasury"] == ["A", "B"] and man["rule"] == "hold" and man["updated"] == "1970-01-01T00:00:00Z"
    path = treasury.write_manifest(tmp_path / "public" / "treasury.json", man)
    assert json.loads(path.read_text())["dev_wallet"] == "D"


def test_launch_refuses_a_wallet_the_site_does_not_publish(monkeypatch):
    import pytest

    import json

    from devbot import treasury

    monkeypatch.setattr(treasury.http, "request",
                        lambda *a, **k: json.dumps({"treasury": ["A", "B"], "rule": "hold"}).encode())
    assert treasury.check_published("https://site/treasury.json", ["A"])["rule"] == "hold"
    with pytest.raises(RuntimeError, match="does not list C"):
        treasury.check_published("https://site/treasury.json", ["A", "C"])

    monkeypatch.setattr(treasury.http, "request",
                        lambda *a, **k: (_ for _ in ()).throw(treasury.http.HttpError(404, "")))
    with pytest.raises(RuntimeError, match="cannot read the published treasury"):
        treasury.check_published("https://site/treasury.json", ["A"])


def test_disclosure_names_every_buying_wallet():
    from devbot import treasury

    line = treasury.disclosure(["B", "A"], "https://site/treasury.json")
    assert "2 wallets" in line and "A, B" in line and "https://site/treasury.json" in line
    assert "the dev, not other buyers" in line
    assert treasury.disclosure([], "https://site/treasury.json") == ""


def test_bundle_launch_puts_the_tip_on_the_create_and_one_signer_per_buy(monkeypatch):
    import pytest
    from types import SimpleNamespace

    from solders.keypair import Keypair

    from devbot import treasury

    dev, mint = Keypair(), Keypair()
    buyers = [Keypair(), Keypair()]
    seen = {}

    def bundle_txs(payloads, signers):
        seen["payloads"], seen["signers"] = payloads, signers
        return ["tx"] * len(payloads)

    monkeypatch.setattr(treasury.pump, "bundle_txs", bundle_txs)
    monkeypatch.setattr(treasury.jito, "send", lambda txs: "BID")
    monkeypatch.setattr(treasury.jito, "landed", lambda bid, timeout_s=60: ("Landed", 42, ["s1", "s2", "s3"]))
    out = treasury.bundle_launch(SimpleNamespace(), dev, mint, buyers, name="N", symbol="S", uri="u",
                                 dev_buy_sol=0.01, buy_sol=0.02, tip_sol=0.0002, slippage_pct=10)
    create, *buys = seen["payloads"]
    assert create["action"] == "create" and create["priorityFee"] == 0.0002 and create["amount"] == 0.01
    assert create["mint"] == str(mint.pubkey()) and create["publicKey"] == str(dev.pubkey())
    assert [b["action"] for b in buys] == ["buy", "buy"]
    assert all(b["priorityFee"] == 0.0 and b["amount"] == 0.02 and b["denominatedInSol"] == "true" for b in buys)
    assert [b["publicKey"] for b in buys] == [str(w.pubkey()) for w in buyers]
    assert seen["signers"][0] == [mint, dev] and seen["signers"][1:] == [[w] for w in buyers]
    assert out.landed and out.slot == 42 and out.bundle_id == "BID" and out.mint == str(mint.pubkey())

    monkeypatch.setattr(treasury.jito, "landed", lambda bid, timeout_s=60: ("Pending", None, []))
    assert not treasury.bundle_launch(SimpleNamespace(), dev, mint, buyers, name="N", symbol="S", uri="u",
                                      dev_buy_sol=0, buy_sol=0.02, tip_sol=0.0002, slippage_pct=10).landed

    with pytest.raises(ValueError, match="below Jito's minimum"):
        treasury.bundle_launch(SimpleNamespace(), dev, mint, buyers, name="N", symbol="S", uri="u",
                               dev_buy_sol=0, buy_sol=0.02, tip_sol=0, slippage_pct=10)
    too_many = [Keypair() for _ in range(treasury.MAX_WALLETS + 1)]
    with pytest.raises(ValueError, match=f"1-{treasury.MAX_WALLETS} treasury wallets"):
        treasury.bundle_launch(SimpleNamespace(), dev, mint, too_many, name="N", symbol="S", uri="u",
                               dev_buy_sol=0, buy_sol=0.02, tip_sol=0.0002, slippage_pct=10)


def test_funding_covers_the_buy_and_skips_wallets_that_have_it(monkeypatch):
    import pytest
    from types import SimpleNamespace

    from solders.keypair import Keypair

    from devbot import treasury
    from devbot import wallets as wallets_mod

    rich, poor = Keypair(), Keypair()
    have = {str(rich.pubkey()): 0.1, str(poor.pubkey()): 0.0}
    asked = []
    rpc = SimpleNamespace(balance_sol=lambda w: have[w])
    monkeypatch.setattr(wallets_mod, "fund", lambda rpc, main, to, sol: asked.append((str(to), round(sol, 6))) or "SIG")
    sigs = treasury.fund_all(rpc, Keypair(), [rich, poor], 0.02)
    assert sigs == ["SIG"] and asked == [(str(poor.pubkey()), 0.0235)]   # 0.02 buy + 0.0035 overhead

    # the dev wallet pays for the whole launch, not just the create
    assert treasury.cost_sol(n_wallets=2, buy_sol=0.02, dev_buy_sol=0.01, tip_sol=0.0002) == pytest.approx(
        0.0098 + 0.01 + 0.0002 + 2 * (0.02 + 0.0035))


def test_jito_send_encodes_base64_and_caps_the_bundle(monkeypatch):
    import pytest

    import base64
    import json

    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction

    from devbot import jito

    dev = Keypair()
    tx = VersionedTransaction.from_bytes(_unsigned_tx(dev))
    posted = {}

    def post(url, payload, **_):
        posted["url"], posted["payload"] = url, payload
        return json.dumps({"result": "BID"}).encode()

    monkeypatch.setattr(jito.http, "post_json", post)
    assert jito.send([tx]) == "BID"
    assert posted["url"] == jito.BLOCK_ENGINE and posted["payload"]["method"] == "sendBundle"
    txs, opts = posted["payload"]["params"]
    assert opts == {"encoding": "base64"} and base64.b64decode(txs[0]) == bytes(tx)

    with pytest.raises(ValueError, match=f"1-{jito.MAX_TXS}"):
        jito.send([tx] * (jito.MAX_TXS + 1))
    with pytest.raises(ValueError):
        jito.send([])

    monkeypatch.setattr(jito.http, "post_json",
                        lambda u, p, **k: json.dumps({"error": {"message": "bundle contains an invalid tx"}}).encode())
    with pytest.raises(RuntimeError, match="invalid tx"):
        jito.send([tx])


def test_jito_status_polling_reports_the_last_state(monkeypatch):
    import json

    from devbot import jito

    replies = {
        "getInflightBundleStatuses": [{"value": [{"status": "Pending"}]}, {"value": [{"status": "Landed"}]}],
        "getBundleStatuses": [{"value": [{"slot": 7, "transactions": ["a", "b"]}]}],
    }

    def post(url, payload, **_):
        return json.dumps({"result": replies[payload["method"]].pop(0)}).encode()

    monkeypatch.setattr(jito.http, "post_json", post)
    monkeypatch.setattr(jito.time, "sleep", lambda s: None)
    assert jito.landed("BID", timeout_s=5, sleep_s=0) == ("Landed", 7, ["a", "b"])

    # a bundle that never resolves is reported as Pending, never as a failure
    monkeypatch.setattr(jito.http, "post_json",
                        lambda u, p, **k: json.dumps({"result": {"value": [{"status": "Pending"}]}}).encode())
    assert jito.landed("BID", timeout_s=0.01, sleep_s=0) == ("Pending", None, [])
    monkeypatch.setattr(jito.http, "post_json", lambda u, p, **k: json.dumps({"result": {"value": [None]}}).encode())
    assert jito.inflight("BID") == "Invalid"


# --- launch_one.py: the standalone launch + exit tool -------------------------------------------

def test_launch_one_floor_is_a_percent_of_the_live_balance():
    import launch_one
    from devbot import pump

    cost = pump.LAUNCH_COST_SOL + 0.5 + launch_one.PRIORITY_FEE_SOL
    assert launch_one.check_floor(0.668, cost, 20).ok                     # 0.668-0.510 >= 0.134
    assert not launch_one.check_floor(0.668, cost, 50).ok                 # 0.668-0.510 < 0.334
    # the floor tracks the balance it is given, so a deposit cannot leave it stale
    assert launch_one.check_floor(2.0, cost, 50).ok
    assert not launch_one.check_floor(0.6, 0.55, 20).ok
    # and the dev buy has a hard ceiling regardless of the floor
    assert not launch_one.check_floor(100.0, 5.0, 20).ok
    assert launch_one.check_floor(4.0, 3.005, 20, max_dev_buy=3.0).ok      # an explicit per-run ceiling
    assert not launch_one.check_floor(3.2, 3.005, 20, max_dev_buy=3.0).ok  # the 20% floor still binds
    assert not launch_one.check_floor(3.25, 3.01845, 5, max_dev_buy=3.0).ok  # j7's 0.0184 is over PumpPortal's
    assert launch_one.check_floor(3.25, 3.01845, 5, max_dev_buy=3.0, launch_cost=0.0184).ok


def _sell_world(monkeypatch, balances, results):
    """A fake chain: `balances` are successive token-balance reads (last value repeats), `results`
    are the outcomes of successive pump.sell calls ("ok" | "timeout" | "raise")."""
    import launch_one
    from devbot import pump

    reads, sells = [], []

    class FakeRpc:
        def call(self, method, params):
            assert method == "getTokenAccountsByOwner"
            amt = balances[min(len(reads), len(balances) - 1)]
            reads.append(amt)
            if amt == 0:
                return {"value": []}
            return {"value": [{"account": {"data": {"parsed": {"info": {
                "tokenAmount": {"amount": str(amt)}}}}}}]}

        def balance_sol(self, pubkey):
            return 1.0 + 0.25 * len(sells)

    def fake_sell(rpc, dev, mint, pct, **kw):
        r = results[min(len(sells), len(results) - 1)]
        sells.append(pct)
        if r == "raise":
            raise RuntimeError("simulated on-chain error")
        return pump.Launched(mint, f"sig{len(sells)}", r == "ok")

    monkeypatch.setattr(launch_one.pump, "sell", fake_sell)
    return FakeRpc(), reads, sells


def test_launch_one_sell_retries_only_while_tokens_remain(monkeypatch, tmp_path):
    import launch_one
    from solders.keypair import Keypair

    dev, mint = Keypair(), "MiNt"

    # a sell that did not confirm but whose tokens are gone LANDED: resending would sell the remainder
    rpc, _reads, sells = _sell_world(monkeypatch, [1000, 0], ["timeout"])
    led = Ledger(tmp_path / "a.sqlite")
    assert launch_one.sell_out(rpc, dev, mint, 100, ledger=led, settle_s=0)
    assert len(sells) == 1
    assert led.db.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1

    # not confirmed and still holding: retry, and stop as soon as one confirms
    rpc, _reads, sells = _sell_world(monkeypatch, [1000, 1000, 1000, 0], ["timeout", "ok"])
    assert launch_one.sell_out(rpc, dev, mint, 100, settle_s=0)
    assert len(sells) == 2

    # an on-chain error is not fatal to the exit path either
    rpc, _reads, sells = _sell_world(monkeypatch, [1000, 1000, 1000, 0], ["raise", "ok"])
    assert launch_one.sell_out(rpc, dev, mint, 100, settle_s=0)
    assert len(sells) == 2

    # every attempt failing is reported as a failure, not swallowed, and stops at the cap
    rpc, _reads, sells = _sell_world(monkeypatch, [1000], ["timeout"])
    assert not launch_one.sell_out(rpc, dev, mint, 100, settle_s=0, attempts=3)
    assert len(sells) == 3

    # nothing held: never send a sell at all
    rpc, _reads, sells = _sell_world(monkeypatch, [0], ["ok"])
    assert launch_one.sell_out(rpc, dev, mint, 100, settle_s=0)
    assert sells == []


def test_launch_one_plan_pins_nothing_and_sends_nothing(monkeypatch, capsys):
    import launch_one
    from solders.keypair import Keypair

    def boom(*a, **k):
        raise AssertionError("plan must not pin, create or sell")

    monkeypatch.setattr(launch_one.pump, "load_keypair", lambda p: Keypair())
    monkeypatch.setattr(launch_one.pump, "Rpc", lambda urls: type("R", (), {"balance_sol": lambda s, p: 0.668})())
    monkeypatch.setattr(launch_one, "art_bytes", lambda a: b"\x89PNG")
    for name in ("pin_image", "pin_metadata", "create", "sell"):
        monkeypatch.setattr(launch_one.pump, name, boom)

    rc = launch_one.main(["plan", "--name", "N", "--symbol", "SYM", "--art", "x.png",
                          "--description", "d", "--dev-buy-sol", "0.5"])
    out = capsys.readouterr().out
    assert rc == 0
    from devbot import pump
    cost = pump.LAUNCH_COST_SOL + 0.5 + launch_one.PRIORITY_FEE_SOL
    assert "dry run" in out and f"{cost:.4f}" in out     # create + dev buy + priority fee
    assert "0.1336" in out                               # the 20% floor, off the live balance
    # the ceiling is enforced before anything reads the chain
    assert launch_one.main(["plan", "--name", "N", "--symbol", "S", "--art", "x", "--dev-buy-sol", "9"]) == 1


def test_launch_one_watch_triggers_on_outside_sol_not_our_own_buy(monkeypatch, tmp_path):
    import launch_one

    monkeypatch.setattr(launch_one, "LOG_DIR", tmp_path)
    reals = iter([0.5, 0.5, 0.62, 0.9])       # our 0.5 dev buy, then a buyer, then a swarm
    monkeypatch.setattr(launch_one.pump, "curve_real_sol", lambda rpc, m: next(reals))
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s

    w = launch_one.watch(None, "M", dev_buy_sol=0.5, hold_s=60, sell_at=0.3, clock=clock, sleep=sleep)
    assert w["reason"] == "trigger" and abs(w["outside"] - 0.4) < 1e-9 and w["dt"] == 0.75
    reals = iter([0.5] * 1000)
    monkeypatch.setattr(launch_one.pump, "curve_real_sol", lambda rpc, m: next(reals))
    t[0] = 0.0
    w = launch_one.watch(None, "M", dev_buy_sol=0.5, hold_s=2, sell_at=0.3, clock=clock, sleep=sleep)
    assert w["reason"] == "deadline" and w["peak_outside"] == 0.0


def test_launch_one_never_sweeps_a_wallet_that_still_holds_the_coin(monkeypatch):
    import launch_one
    from solders.keypair import Keypair

    monkeypatch.setattr(launch_one, "token_amount", lambda rpc, o, m: 5)
    for name in ("claim_creator_fees", "unclaimed_fees_sol"):
        monkeypatch.setattr(launch_one.pump, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError(name)))
    monkeypatch.setattr(launch_one.wallets_mod, "sweep", lambda *a: (_ for _ in ()).throw(AssertionError("sweep")))
    assert launch_one.settle_fresh(None, Keypair(), Keypair(), "M") == {"claimed": 0.0, "swept": 0.0}


def test_launch_one_fresh_dry_run_writes_no_key_and_sends_nothing(monkeypatch, tmp_path, capsys):
    import launch_one
    from solders.keypair import Keypair

    def boom(*a, **k):
        raise AssertionError("dry run must not pin, fund, create or sell")

    monkeypatch.setattr(launch_one, "WALLETS_DIR", tmp_path / "wallets")
    monkeypatch.setattr(launch_one.pump, "load_keypair", lambda p: Keypair())
    monkeypatch.setattr(launch_one.pump, "Rpc", lambda urls: type("R", (), {"balance_sol": lambda s, p: 1.0})())
    monkeypatch.setattr(launch_one, "art_bytes", lambda a: b"\x89PNG")
    for name in ("pin_image", "pin_metadata", "create", "sell"):
        monkeypatch.setattr(launch_one.pump, name, boom)
    monkeypatch.setattr(launch_one.wallets_mod, "fund", boom)
    rc = launch_one.main(["run", "--name", "N", "--symbol", "S", "--art", "x", "--dev-buy-sol", "0.5", "--fresh"])
    out = capsys.readouterr().out
    assert rc == 0 and "FRESH" in out and "dry run" in out
    assert not (tmp_path / "wallets").exists()
