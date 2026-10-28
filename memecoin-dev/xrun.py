"""Coins off fresh X posts, built as fast as we can, with launch_one's sell-on-arrival exit.

    python xrun.py replay TAPE.jsonl --creates CREATES.jsonl [--limit 12]   # offline: real design calls, nothing sent
    J7_ACCESS_TOKEN=eyJ... python xrun.py live [--hours 1]                  # live feed, dry: nothing pinned or sent
    J7_ACCESS_TOKEN=eyJ... python xrun.py live --send                       # launches, from the main wallet

Why. The only multi-SOL flow our 41 coins ever saw was bot SWARMS (AMDER 12.7 SOL, BEAD 7.7, REFFS 5.7):
15-20 wallets buy 0.3-2.5 SOL each ~8 s after the create and dump 1-2 s later. Two of the three were coins
off a seconds-old X post, from the main wallet AFTER bots stopped sniping it, so a swarm does not care about
wallet freshness. Research 10-14: the first coin on a post <= 15 s old caught on 19.6% vs 7.1%, and the
leader beats the next creator by a median 12 s. On 10-14 devbot's feed made 9 such coins at 0 dev buy: 1 swarm,
and with no position we earned only its creator fee. So: a small dev buy, a coin out within ~10 s of the post,
and launch_one.watch selling 100% the moment outside SOL shows a swarm.

Each post that passes devbot.feed.worth_a_coin is designed in two parallel threads, the concept (devbot's LLM
chain, with j7tracker's obvious name as the fallback when the model is slow) and the art (Workers AI, straight
from the post text, so it never waits for the model). A dry run then builds the real create transaction through
PumpPortal and SIMULATES it: free and sent nowhere. Rivals come from PumpPortal's free create stream (live) or
the create tape recorded beside the feed tape (replay), matched on j7tracker's obvious name and ticker, so
"first" means first under that name. Coins off the same post under other names are not counted.
A row per post goes to research/data/xrun.jsonl 60 s after the post, with the rivals seen by then.

Send gates, all checked right before anything is pinned: the STOP file, the post's age, no rival yet, the
per-hour cap, a session loss budget (a PERCENT of the main balance read when the first session started, kept
in xrun.json until you delete it) and launch_one's own floor. The coin's `twitter` stays empty: it is only ever
our own launch post, never the post the coin is about.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from solders.keypair import Keypair

import launch_one
from devbot import concept as concept_mod, feed as feed_mod, image as image_mod, j7
from devbot import http, ledger as ledger_mod, openrouter, pump, social

ROWS = launch_one.LOG_DIR / "xrun.jsonl"
STATE = launch_one.STATE_DIR / "xrun.json"
STOP = launch_one.STATE_DIR / "XRUN_STOP"
CREATES_WS = "wss://pumpportal.fun/api/data"

MIN_FOLLOWERS = 200_000
MAX_AGE_S = 12.0          # post age when the create would go out; the leader leads by a median 12 s
LLM_TIMEOUT_S = 5.0       # then j7tracker's obvious name, if it has one
ART_TIMEOUT_S = 10.0
DEV_BUY_SOL = 0.05        # a dud costs the create + ~2% round trip; the curve cannot fall below its fresh state
SELL_AT_SOL = 1.0         # a swarm is 6-13 SOL; lone snipers (0.26-0.31 on the 10-15 night) are not worth the exit
HOLD_S = 120.0
MAX_PER_HOUR = 4
# Workers AI's free 10,000 neurons a day ran out after ~70 replay images on 10-15 (shared with devbot).
DRY_PER_HOUR = 20
KEYED_RETRY_S = 900.0     # after Workers AI + keyed Pollinations fail, go straight to keyless this long
KEYLESS_GAP_S = 46.0      # keyless Pollinations: "Free limit reached: 1 image every 45 sec"
DESIGN_GAP_S = 50.0
ART_EST_S = 2.6           # replay median post -> art (Workers AI), for dry runs with --no-art
BUDGET_PCT = 10.0
WORST_DEV_LOSS = 0.05    # of the dev buy: pump 1.25% x2 + PumpPortal 0.5% x2 + slippage on the way out
FIRST_WINDOW_S = 60.0     # rivals counted this long after the post
# Not measured in a dry run (nothing is pinned). devbot's logs, 10-14: metadata pin 0.5-0.6 s; the art pin
# runs beside the concept model and is assumed at 1.0 s.
PIN_ART_EST_S, PIN_META_EST_S = 1.0, 0.6
ZW = re.compile(r"[​-‏⁠﻿]")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", ZW.sub("", s or "").lower())


def is_rival(name: str, symbol: str, obvious: str, ticker: str) -> bool:
    """A create under j7tracker's obvious name for the post, its ticker, or most of the name as a prefix
    ("Agent Capital" for "Agent Capital Markets", not "Agent")."""
    n, o = norm(name), norm(obvious)
    if o and n == o:
        return True
    short, long_ = sorted((n, o), key=len)
    if short and len(short) >= max(5, 0.6 * len(long_)) and long_.startswith(short):
        return True
    t = norm(ticker)
    return len(t) >= 3 and norm(symbol) == t


def keys() -> dict[str, str]:
    names = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "DEVBOT_MODEL", "OPENROUTER_API_KEY", "GROQ_API_KEY",
             "POLLINATIONS_TOKEN", "PINATA_JWT", "SOLANA_RPC_URL", "J7_ACCESS_TOKEN",
             "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    return {k: os.environ.get(k, "").strip() or launch_one._env(launch_one.ENV_PATH, k) for k in names}


def live_rpcs(urls: list[str]) -> list[str]:
    """The endpoints that answer now. pump.Rpc only falls through on rate limits, so a dead key
    (the VM's Helius key answered 401 on 10-15) would fail every call; it is dropped here instead."""
    ok = []
    for url in urls:
        try:
            pump.Rpc([url]).call("getSlot", [])
            ok.append(url)
        except Exception as e:
            log(f"RPC {url.split('/')[2]} dropped: {str(e)[:60]}")
    if not ok:
        raise RuntimeError("no RPC endpoint answers")
    return ok


# ---- rivals ------------------------------------------------------------------------------------------
class Creates:
    """pump.fun creates as (local time, name, symbol, mint), newest last."""

    def __init__(self) -> None:
        self.rows: list[tuple[float, str, str, str]] = []
        self.info: dict[str, dict] = {}        # mint -> creator, dev buy SOL, metadata uri (hot themes)
        self.lock = threading.Lock()
        self.error = ""

    def add(self, t: float, d: dict) -> None:
        if d.get("mint") and d.get("txType", "create") == "create":
            with self.lock:
                self.rows.append((t, d.get("name") or "", d.get("symbol") or "", d["mint"]))
                self.info[d["mint"]] = {"creator": d.get("traderPublicKey") or "", "sol": d.get("solAmount") or 0.0,
                                        "uri": d.get("uri") or "", "mayhem": bool(d.get("is_mayhem_mode"))}
                if len(self.rows) > 20000:
                    gone = self.rows[:10000]
                    del self.rows[:10000]
                    for r in gone:
                        self.info.pop(r[3], None)

    def recent(self, since: float) -> list[tuple[float, str, str, str, dict]]:
        with self.lock:
            return [(*r, self.info.get(r[3], {})) for r in self.rows if r[0] >= since]

    def rivals(self, obvious: str, ticker: str, since: float, until: float) -> list[tuple[float, str, str, str]]:
        with self.lock:
            return [r for r in self.rows if since <= r[0] <= until and is_rival(r[1], r[2], obvious, ticker)]

    def stream(self) -> None:
        """PumpPortal's free create stream on its own thread; reconnects."""
        import websocket  # websocket-client, already a python-socketio dependency

        def on_open(ws):
            ws.send(json.dumps({"method": "subscribeNewToken"}))

        def on_message(_ws, msg):
            try:
                self.add(time.time(), json.loads(msg))
            except ValueError:
                pass

        def run():
            while True:
                app = websocket.WebSocketApp(CREATES_WS, on_open=on_open, on_message=on_message)
                app.run_forever(ping_interval=20, ping_timeout=10)
                self.error = "create stream dropped; reconnecting"
                time.sleep(2)

        threading.Thread(target=run, daemon=True, name="creates").start()


# ---- hot themes ----------------------------------------------------------------------------------------
HOT_STOP = {"the", "of", "and", "coin", "token", "on", "in", "for", "to", "is", "by", "sol", "official", "daily",
            "etf", "pump", "fun", "meme", "inu", "new", "first", "real", "just", "this", "that", "with"}
HOT_WINDOW_S = 1800.0      # PREREG P3: >= hot_n creates by OTHER creators in the prior 30 min share a word
HOT_LIVE_S = 300.0         # and the theme is still moving: one of them in the last 5 min
HOT_COOLDOWN_S = 7200.0    # never two of our coins on one word inside 2 h


def theme_words(name: str, symbol: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", f"{name} {symbol}".lower()) if len(w) > 2 and w not in HOT_STOP}


def hot_themes(rows: list, now: float, n: int, exclude: set) -> list[tuple[str, int, list]]:
    """[(word, distinct creators, rows newest first)] for words held by >= n distinct creators in the window,
    with one row inside HOT_LIVE_S; most recently active first. Mayhem creates and `exclude` creators skipped."""
    by: dict[str, list] = {}
    for r in rows:
        info = r[4]
        if info.get("mayhem") or info.get("creator") in exclude:
            continue
        for w in theme_words(r[1], r[2]):
            by.setdefault(w, []).append(r)
    out = []
    for w, rs in by.items():
        creators = {r[4].get("creator") for r in rs}
        if len(creators) >= n and now - max(r[0] for r in rs) <= HOT_LIVE_S:
            out.append((w, len(creators), sorted(rs, key=lambda r: -r[0])))
    return sorted(out, key=lambda x: (-sum(now - r[0] <= HOT_LIVE_S for r in x[2]), -x[1]))


def source_post_id(rows: list, limit: int = 6) -> str:
    """The X post most of the theme's coins link in `twitter` (read from their metadata); "" if none."""
    ids: dict[str, int] = {}
    for r in rows[:limit]:
        uri = r[4].get("uri") or ""
        if not uri:
            continue
        try:
            meta = json.loads(http.request("GET", image_mod.source_url(uri), timeout=6,
                                           headers={"User-Agent": "Mozilla/5.0"}))
        except Exception:
            continue
        m = re.search(r"/status/(\d+)", str(meta.get("twitter") or ""))
        if m:
            ids[m.group(1)] = ids.get(m.group(1), 0) + 1
    return max(ids, key=ids.get) if ids else ""


class HotWatch:
    """PREREG P3 trigger: when a theme turns hot, hand the runner a post to make OUR coin on it.
    The post is the X post the theme's coins link when the j7 feed carried it; otherwise a stand-in built
    from the theme's names. Our coin's `twitter` is still only our own announcement (pump.metadata rule)."""

    def __init__(self, a: argparse.Namespace, creates: "Creates", feed: "XFeed", runner: "Runner") -> None:
        self.a, self.creates, self.feed, self.runner = a, creates, feed, runner
        self.used: dict[str, float] = {}
        self.used_mints: set[str] = set()      # a theme fires once, whichever of its words turns hot

    def tick(self) -> None:
        now = time.time()
        rows = self.creates.recent(now - HOT_WINDOW_S)
        for word, ncr, rs in hot_themes(rows, now, self.a.hot_n, {str(self.runner.main.pubkey())}):
            if now - self.used.get(word, 0) < HOT_COOLDOWN_S or any(r[3] in self.used_mints for r in rs):
                continue
            pid = source_post_id(rs)
            post = self.feed.posts.get(pid) if pid else None
            names = [r[1] for r in rs[:6]]
            if post is None:
                post = feed_mod.Post(id=f"hot-{word}-{int(now)}", handle="pumpfun", name="", followers=0,
                                     text=f"A coin theme is hot on pump.fun right now: {word}. Coins on it: "
                                          + "; ".join(names), url="", created=now)
                post.seen, post.ticker = now, ""
            post.suggestion = post.suggestion or names[0]
            log(f"HOT '{word}': {ncr} creators in {HOT_WINDOW_S / 60:.0f} min, e.g. {names[:3]}; "
                f"source {'@' + post.handle if pid and post.handle != 'pumpfun' else 'names only'}")
            self.used[word] = now
            self.used_mints |= {r[3] for r in rs}
            self.runner.avoid += list(dict.fromkeys(names))   # our own name, never a copy of theirs
            self.runner.handle(post)
            return

    def run(self) -> None:
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:
                    log(f"hot watch: {type(e).__name__}: {str(e)[:120]}")
                time.sleep(15)
        threading.Thread(target=loop, daemon=True, name="hot").start()


# ---- design ------------------------------------------------------------------------------------------
@dataclass
class Design:
    name: str = ""
    symbol: str = ""
    description: str = ""
    source: str = ""            # "llm" | "j7" | ""
    art: bytes = b""
    image_uri: str = ""
    t: dict = field(default_factory=dict)   # stage -> seconds after the post
    errors: list[str] = field(default_factory=list)


def uncaption(raw: bytes) -> bytes:
    """Keyless Pollinations stamps a watermark in the bottom ~8%: crop it off, as night.py does."""
    import io
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as im:
        w, h = im.size
        cut = int(h * 0.1)
        buf = io.BytesIO()
        im.convert("RGB").crop((cut // 2, 0, w - cut // 2, h - cut)).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def art_prompt(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text).strip()
    return f"a funny cartoon about this news: {text[:300]}"


class Runner:
    def __init__(self, a: argparse.Namespace, k: dict[str, str], creates: Creates, clock_shift: float = 0.0) -> None:
        self.a, self.k, self.creates = a, k, creates
        openrouter.configure(k["GROQ_API_KEY"], (), k["POLLINATIONS_TOKEN"])
        self.model = k["DEVBOT_MODEL"] or "@cf/meta/llama-3.1-8b-instruct-fp8-fast"
        urls = ([k["SOLANA_RPC_URL"]] if k["SOLANA_RPC_URL"] else []) + launch_one.rpc_urls()
        self.rpc = pump.Rpc(live_rpcs(list(dict.fromkeys(urls))))
        # --wallet: a fresh creator wallet from the store. A wallet's draw persists across its launches
        # (rank rho +0.33, 416 same-wallet pairs, 10-16), and DZ is 0 for 5, so the creator can be swapped.
        self.main = launch_one.signer(a.wallet)
        self.j7_key = launch_one._env(launch_one.ENV_PATH, a.j7_key_env) if a.launcher == "j7" else ""
        self.avoid: list[str] = []
        self.sent: list[float] = []
        self.designed_at = self.keyless_at = self.keyed_dead_until = 0.0
        self.done = 0
        self.busy = threading.Lock()
        self.state = self._state() if a.send else {}

    def _state(self) -> dict:
        if STATE.exists():
            return json.loads(STATE.read_text())
        st = {"start_balance": self.rpc.balance_sol(str(self.main.pubkey())), "started": time.time()}
        STATE.write_text(json.dumps(st))
        return st

    def design(self, post: feed_mod.Post) -> Design:
        d = Design()
        out: dict = {}

        def concept():
            try:
                c = concept_mod.from_post(self.k["CLOUDFLARE_ACCOUNT_ID"], self.k["CLOUDFLARE_API_TOKEN"], self.model,
                                          post.handle, post.followers, post.text, post.suggestion, self.avoid,
                                          openrouter_key=self.k["OPENROUTER_API_KEY"])
                out["concept"] = c
            except Exception as e:
                out["concept_err"] = f"{type(e).__name__}: {str(e)[:120]}"
            out["t_concept"] = time.time() - post.created

        def art():
            try:
                raw = None
                if time.time() >= self.keyed_dead_until:
                    try:
                        raw = image_mod.generate(self.k["CLOUDFLARE_ACCOUNT_ID"], self.k["CLOUDFLARE_API_TOKEN"],
                                                 art_prompt(post.text), fallback=True,
                                                 pollinations_token=self.k["POLLINATIONS_TOKEN"])
                    except Exception as e:   # both keyed services out (10-15): stop asking them for a while
                        self.keyed_dead_until = time.time() + KEYED_RETRY_S
                        out["art_fallback"] = f"keyed art out for {KEYED_RETRY_S / 60:g} min ({str(e)[:60]})"
                if raw is None:              # keyless: 1 image / 45 s, ~5.7 s; a refused call still counts
                    self.keyless_at = time.time()
                    raw = uncaption(image_mod._pollinations(art_prompt(post.text), ""))
                out["art"] = image_mod.fit(raw)
                out["t_art"] = time.time() - post.created
                if self.a.send:
                    out["image_uri"] = pump.pin_image(self.k["PINATA_JWT"], "x", out["art"])
                    out["t_art_pinned"] = time.time() - post.created
            except Exception as e:
                out["art_err"] = f"{type(e).__name__}: {str(e)[:120]}"

        if getattr(self.a, "no_art", False):   # dry only: the art's time is the replay median, not measured
            def art():
                out["art"], out["t_art"] = b"stub", ART_EST_S
        tc = threading.Thread(target=concept, daemon=True)
        ta = threading.Thread(target=art, daemon=True)
        tc.start(), ta.start()
        tc.join(self.a.llm_timeout)
        c = out.get("concept")
        if c is not None:
            d.name, d.symbol, d.description, d.source = c.name, c.symbol, c.description, "llm"
        elif post.suggestion and post.ticker:   # the model is slow or failed: j7tracker's obvious name
            d.name, d.symbol, d.source = post.suggestion[:32], re.sub(r"[^A-Za-z0-9]", "", post.ticker)[:10], "j7"
            d.description = d.name
        d.t["concept"] = out.get("t_concept", time.time() - post.created)
        ta.join(max(0.0, self.a.art_timeout - (time.time() - post.created)))
        d.art, d.image_uri = out.get("art", b""), out.get("image_uri", "")
        d.t.update({k[2:]: v for k, v in out.items() if k.startswith("t_") and k != "t_concept"})
        d.errors = [out[k] for k in ("concept_err", "art_err", "art_fallback") if k in out]
        if c is None and "concept_err" not in out:
            d.errors.append(f"concept took over {self.a.llm_timeout:g}s")
        return d

    # ---- the feed's worker calls this, newest post first ----
    def handle(self, post: feed_mod.Post) -> bool:
        if self.a.limit and self.done >= self.a.limit:
            return False
        now = time.time()
        # Art quota is the scarce thing: Workers AI's 10k neurons/day is ~70 images (shared with devbot) and
        # keyless Pollinations refuses more than 1 image / 45 s. So at most one design per --design-gap-s.
        if now - self.designed_at < self.a.design_gap_s:
            return False
        if time.time() < self.keyed_dead_until and now - self.keyless_at < KEYLESS_GAP_S:
            return False
        self.designed_at = now
        with self.busy:
            self.done += 1
            return self._handle(post)

    def _handle(self, post: feed_mod.Post) -> bool:
        seen = post.seen - post.created
        d = self.design(post)
        row = {"post_id": post.id, "url": post.url, "handle": post.handle, "followers": post.followers,
               "text": post.text[:200], "age_seen": round(seen, 2), "obvious": post.suggestion,
               "ticker": post.ticker, "name": d.name, "symbol": d.symbol, "source": d.source,
               **{f"age_{k}": round(v, 2) for k, v in d.t.items()}, "errors": d.errors, "send": self.a.send,
               "art_est": bool(getattr(self.a, "no_art", False))}
        if not d.name or not d.art:
            row["skip"] = "no concept" if not d.name else "no art"
            log(f"@{post.handle}: {row['skip']} ({'; '.join(d.errors)[:160]})")
            self._finish(post, row, 0.0)
            return False
        # When the create would go out: the slower of concept and pinned art, then the metadata pin.
        art_ready = d.t.get("art_pinned", d.t["art"] + (0 if self.a.send else PIN_ART_EST_S))
        mint = Keypair()
        payload = pump.create_payload(self.main, mint, name=d.name, symbol=d.symbol,
                                      uri="https://ipfs.io/ipfs/bafkreidryrundryrundryrundryrundryrundryrundryrundryrun",
                                      dev_buy_sol=self.a.dev_buy_sol, slippage_pct=launch_one.SLIPPAGE_PCT,
                                      priority_fee_sol=launch_one.PRIORITY_FEE_SOL, mayhem=launch_one.MAYHEM)
        if not self.a.send:
            t0 = time.time()
            try:
                sim = pump.simulate_create(self.rpc, payload, [mint, self.main])
            except Exception as e:
                sim = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:120]}", "build_s": time.time() - t0}
            build_s = sim["build_s"]   # PumpPortal's build is on the live path; the simulation is not
            row.update(sim_ok=sim["ok"], sim_err=sim.get("err"), build_s=round(build_s, 2))
            send_age = max(d.t["concept"], art_ready) + PIN_META_EST_S + build_s
        else:
            send_age = max(d.t["concept"], art_ready) + PIN_META_EST_S
        row["age_send"] = round(send_age, 2)
        before = self.creates.rivals(post.suggestion, post.ticker, post.created - 600, post.created + send_age)
        row["rivals_before"] = len(before)
        why = self.gate(post, send_age, before) or ("" if self.a.send or row.get("sim_ok") else "simulation failed")
        row["gate"] = why or "pass"
        log(f"@{post.handle} ({post.followers:,}): {d.name} / ${d.symbol} [{d.source}]  concept {d.t['concept']:.1f}s "
            f"art {d.t.get('art', 0):.1f}s  send at {send_age:.1f}s  rivals before {len(before)}  "
            f"{'sim ' + ('OK' if row.get('sim_ok') else str(row.get('sim_err'))[:60]) if not self.a.send else ''}  "
            f"-> {why or 'GO'}")
        if why or not self.a.send:
            self._finish(post, row, send_age)
            return not why
        wait = self.a.min_age_s - (time.time() - post.created)
        if wait > 0:                       # PREREG P4: the linked post 1-10 min old did best
            log(f"  holding {wait:.0f}s until the post is {self.a.min_age_s:g}s old")
            time.sleep(wait)
            if STOP.exists():
                self._finish(post, row, send_age)
                return False
        self.avoid += [d.name, d.symbol]
        self.sent.append(time.time())
        row.update(self.launch(post, d, mint))
        self._finish(post, row, send_age)
        return True

    def gate(self, post: feed_mod.Post, send_age: float, before: list) -> str:
        if send_age > self.a.max_age_s and self.a.trigger == "first":
            return f"too slow ({send_age:.1f}s > {self.a.max_age_s:g}s)"
        if before and self.a.trigger == "first":
            return f"{len(before)} rival(s) already: {before[0][1]} / {before[0][2]}"
        if not self.a.send:
            return ""
        if STOP.exists():
            return f"STOP file {STOP}"
        now = time.time()
        self.sent = [t for t in self.sent if now - t < 3600]
        if len(self.sent) >= self.a.max_per_hour:
            return f"{len(self.sent)} launches this hour"
        bal = self.rpc.balance_sol(str(self.main.pubkey()))
        launch_cost = j7.LAUNCH_COST_SOL if self.a.launcher == "j7" else pump.LAUNCH_COST_SOL
        cost = launch_cost + self.a.dev_buy_sol + launch_one.PRIORITY_FEE_SOL
        # The dev buy comes back when the sell lands (>= ~97% at 0.05 SOL; an unverified sell writes STOP),
        # so the budget is charged the most one launch can lose, not the SOL it moves.
        worst = launch_cost + launch_one.PRIORITY_FEE_SOL + self.a.dev_buy_sol * WORST_DEV_LOSS
        floor = self.state["start_balance"] * (1 - self.a.budget_pct / 100)
        if bal - worst < floor:
            return f"budget: {bal:.4f} - {worst:.4f} < {floor:.4f} ({self.a.budget_pct:g}% of the session start)"
        v = launch_one.check_floor(bal, cost, self.a.floor_pct, max_dev_buy=self.a.max_dev_buy,
                                   launch_cost=launch_cost)
        return "" if v.ok else v.reason

    def launch(self, post: feed_mod.Post, d: Design, mint: Keypair) -> dict:
        a, jwt = self.a, self.k["PINATA_JWT"]
        before = self.rpc.balance_sol(str(self.main.pubkey()))
        website = f"{launch_one.WEBSITE_BASE}/{mint.pubkey()}"
        description = (d.description + "\n\n"
                       + launch_one.DISCLOSURE.format(dev_buy=a.dev_buy_sol, wallet=self.main.pubkey()))
        post_id = self.announce(d, str(mint.pubkey())) if a.x_post else ""
        twitter = social.post_url(post_id) if post_id else ""
        uri = image_uri = ""
        if a.launcher != "j7":             # j7tracker hosts the image and metadata itself
            image_uri = d.image_uri or pump.pin_image(jwt, d.symbol, d.art)
            uri = pump.pin_metadata(jwt, name=d.name, symbol=d.symbol, description=description,
                                    image_uri=image_uri, website=website, twitter=twitter)
        ledger = ledger_mod.Ledger(launch_one.STATE_DIR / "devbot.sqlite")
        ledger.record_spend(pump.LAUNCH_COST_SOL + a.dev_buy_sol + launch_one.PRIORITY_FEE_SOL, f"xrun {d.symbol}")
        t_send = time.time() - post.created
        try:
            if a.launcher == "j7":         # 205 of 519 launches at 1.5-5.5 SOL were j7tracker's (10-15 night)
                out = j7.deploy(self.rpc, j7.BASES[a.j7_region], self.k["J7_ACCESS_TOKEN"], self.j7_key, mint,
                                name=d.name, symbol=d.symbol, description=description, image=d.art,
                                website=website, twitter=twitter, dev_buy_sol=a.dev_buy_sol)
            else:
                out = pump.create(self.rpc, self.main, mint, name=d.name, symbol=d.symbol, uri=uri,
                                  dev_buy_sol=a.dev_buy_sol, slippage_pct=launch_one.SLIPPAGE_PCT,
                                  priority_fee_sol=launch_one.PRIORITY_FEE_SOL, mayhem=launch_one.MAYHEM)
        except Exception:
            if post_id:                    # no coin: the announcement must not stay up pointing at nothing
                try:
                    social.delete(self.x_keys(), post_id)
                except Exception as e:
                    log(f"  could not delete X post {social.post_url(post_id)}: {e}")
            raise
        ledger.record_launch(mint=out.mint, name=d.name, symbol=d.symbol, narrative=f"xrun @{post.handle}",
                             source_mint="", source_name="", source_symbol="", uri=uri, signature=out.signature,
                             confirmed=out.confirmed, dev_buy_sol=a.dev_buy_sol)
        if uri:
            ledger.record_pins(out.mint, [pump.cid_of(image_uri), pump.cid_of(uri)])
        log(f"  LIVE {d.symbol} https://pump.fun/coin/{out.mint} (sent at {t_send:.1f}s)")
        if not out.confirmed:
            return {"mint": out.mint, "age_sent": round(t_send, 2), "confirmed": False}
        w = launch_one.watch(self.rpc, out.mint, dev_buy_sol=a.dev_buy_sol, hold_s=a.hold_s, sell_at=a.sell_at)
        sold = launch_one.sell_out(self.rpc, self.main, out.mint, 100, ledger=ledger) if a.dev_buy_sol > 0 else True
        time.sleep(launch_one.SETTLE_S)
        after = self.rpc.balance_sol(str(self.main.pubkey()))
        res = {"mint": out.mint, "age_sent": round(t_send, 2), "confirmed": True, "sold": sold,
               **{f"watch_{k}": v for k, v in w.items()}, "pnl_sol": after - before}
        with launch_one.EXPERIMENTS.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "tag": f"xrun @{post.handle}", "name": d.name, "symbol": d.symbol,
                                "dev_buy_sol": a.dev_buy_sol, "sell_at": a.sell_at, "hold_s": a.hold_s,
                                "main_before": before, "main_after": after, **res}) + "\n")
        log(f"  RESULT {d.symbol}: {w['reason']} at t+{w['dt']}s, peak outside {w['peak_outside']:+.4f}; "
            f"{after - before:+.4f} SOL")
        if not sold:
            STOP.write_text(f"sell of {out.mint} not verified; look before removing this file\n")
            log(f"  SELL NOT VERIFIED: wrote {STOP}")
        return res

    def x_keys(self) -> social.XKeys:
        return social.XKeys(self.k["X_API_KEY"], self.k["X_API_SECRET"], self.k["X_ACCESS_TOKEN"], self.k["X_ACCESS_SECRET"])

    def announce(self, d: Design, mint: str) -> str:
        """Post the launch with its art from OUR X account (devbot's announce, bot.py), so the coin's `twitter`
        is our own post. The 3-SOL coins that paid (research/devpnl.py, 10-15) were 94% X-post-linked. A failed
        post never blocks the launch. X refuses a CA for 7 days after app authorization: then post without it."""
        t0 = time.time()
        try:
            media = social.upload_image(self.x_keys(), d.art, *image_mod.kind(d.art))
        except Exception as e:
            log(f"  X image upload failed, posting text only: {e}")
            media = ""
        ca = mint
        for _ in range(2):
            try:
                pid = social.post(self.x_keys(), social.launch_text(d.name, d.symbol, d.description, ca, ""), media)
                log(f"  X post {social.post_url(pid)} in {time.time() - t0:.1f}s{'' if ca else ' (no CA: X new-app rule)'}")
                return pid
            except Exception as e:
                if ca and social.address_blocked(e):
                    ca = ""
                    continue
                log(f"  X post failed, launching without it: {e}")
                return ""
        return ""

    def _finish(self, post: feed_mod.Post, row: dict, send_age: float) -> None:
        """Writes the row once FIRST_WINDOW_S has passed since the post, with every rival seen by then."""
        def write():
            rivals = self.creates.rivals(post.suggestion, post.ticker, post.created - 600,
                                         post.created + FIRST_WINDOW_S)
            row["rivals_60s"] = len(rivals)
            row["first_rival_age"] = round(min(r[0] for r in rivals) - post.created, 2) if rivals else None
            row["would_be_first"] = bool(send_age) and (not rivals or min(r[0] for r in rivals) > post.created + send_age)
            row["ts"] = time.time()
            launch_one.LOG_DIR.mkdir(parents=True, exist_ok=True)
            with ROWS.open("a") as f:
                f.write(json.dumps(row) + "\n")
        delay = post.created + FIRST_WINDOW_S - time.time()
        threading.Timer(max(0.0, delay), write).start()


# ---- feeds ---------------------------------------------------------------------------------------------
class XFeed(feed_mod.Feed):
    """devbot's j7tracker feed, plus the obvious ticker and when we first saw each post."""

    def on_tweet(self, t: dict) -> None:
        p = feed_mod.to_post(t)
        if p is not None and p.id not in self.posts:
            p.seen = time.time()
            p.ticker = ""
            self.posts[p.id] = p
            if not feed_mod.worth_a_coin(t, p, self.min_followers, self.max_age_s):
                self.todo.put(p)

    def on_suggestion(self, s: dict) -> None:
        p = self.posts.get(str(s.get("tweet_id")))
        if p is not None and not p.suggestion:
            p.suggestion = ZW.sub("", s.get("prediction") or "").strip()
            p.ticker = (s.get("ticker") or s.get("ai_ticker") or "").strip()


def make_feed(a: argparse.Namespace, runner: Runner, token: str = "") -> XFeed:
    return XFeed(token, runner.handle, min_followers=a.min_followers, max_age_s=a.max_age_s,
                 max_per_hour=a.max_per_hour if a.send else a.dry_per_hour)


def replay(a: argparse.Namespace, k: dict[str, str]) -> int:
    """Plays a j7tape (and its create tape) at real speed, shifted to now, through the live code path."""
    events = [json.loads(line) for line in open(a.tape)]
    events = [e for e in events if e.get("event") in ("tweet", "ai_suggestion") and isinstance(e.get("data"), dict)]
    creates = [json.loads(line) for line in open(a.creates)] if a.creates else []
    t_first = (events[0]["recv_ns"] / 1e9) if events else 0.0
    if a.skip_min:
        events = [e for e in events if e["recv_ns"] / 1e9 >= t_first + a.skip_min * 60]
        creates = [c for c in creates if c["recv_ns"] / 1e9 >= t_first + a.skip_min * 60 - 600]
    shift = time.time() - (events[0]["recv_ns"] / 1e9) + 1.0
    cs = Creates()
    for c in creates:
        if isinstance(c.get("data"), dict):
            cs.add(c["recv_ns"] / 1e9 + shift, c["data"])
    runner = Runner(a, k, cs)
    f = make_feed(a, runner)
    threading.Thread(target=f._work, daemon=True, name="design").start()
    log(f"replaying {len(events)} feed events, {len(creates)} creates, from {a.tape}; dry run, nothing sent")
    for e in events:
        at = e["recv_ns"] / 1e9 + shift
        if (wait := at - time.time()) > 0:
            time.sleep(wait)
        d = dict(e["data"])
        if e["event"] == "tweet":
            if isinstance(d.get("createdAt"), (int, float)):
                d["createdAt"] = d["createdAt"] + shift * 1000
            f.on_tweet(d)
        else:
            f.on_suggestion(d)
        if a.limit and runner.done >= a.limit:
            break
    while not f.todo.empty() or runner.busy.locked():
        time.sleep(1)
    log(f"replay done; waiting {FIRST_WINDOW_S:g}s for the last rows")
    time.sleep(FIRST_WINDOW_S + 2)
    f.stop()
    return 0


def live(a: argparse.Namespace, k: dict[str, str]) -> int:
    token = k["J7_ACCESS_TOKEN"]
    if not token:
        print("no J7_ACCESS_TOKEN: copy the __Secure-j7_at cookie from a logged-in j7tracker tab", file=sys.stderr)
        return 1
    left = (j7.expiry(token) - time.time()) / 60
    if left <= 0:
        print(f"that j7tracker token expired {-left:.0f} min ago", file=sys.stderr)
        return 1
    if a.send and not k["PINATA_JWT"]:
        print("--send needs PINATA_JWT", file=sys.stderr)
        return 1
    cs = Creates()
    cs.stream()
    runner = Runner(a, k, cs)
    f = make_feed(a, runner, token)
    if a.trigger == "hot":                 # posts are kept for HotWatch; the feed itself triggers nothing
        f.design = lambda post: False
        HotWatch(a, cs, f, runner).run()
    f.start()
    log(f"live feed, token good for {left:.0f} min; {'SENDING' if a.send else 'dry run, nothing sent'}")
    end = time.time() + a.hours * 3600
    while time.time() < end and f.alive and not (a.limit and runner.done >= a.limit):
        time.sleep(5)
    if not f.alive:
        log(f"feed stopped: {f.error}")
    failed = not f.alive
    while runner.busy.locked():
        time.sleep(1)
    time.sleep(FIRST_WINDOW_S + 2)
    f.stop()
    return 3 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("replay", "live"):
        p = sub.add_parser(name)
        p.add_argument("--no-art", action="store_true",
                       help=f"dry only: skip the art call and count it as {ART_EST_S:g}s (saves Workers AI quota)")
        if name == "replay":
            p.add_argument("tape")
            p.add_argument("--creates", default="")
            p.add_argument("--skip-min", type=float, default=0.0)
        else:
            p.add_argument("--hours", type=float, default=1.0)
            p.add_argument("--send", action="store_true")
        p.add_argument("--limit", type=int, default=0, help="stop after this many designed posts")
        p.add_argument("--min-followers", type=int, default=MIN_FOLLOWERS)
        p.add_argument("--max-age-s", type=float, default=MAX_AGE_S)
        p.add_argument("--llm-timeout", type=float, default=LLM_TIMEOUT_S)
        p.add_argument("--art-timeout", type=float, default=ART_TIMEOUT_S)
        p.add_argument("--dev-buy-sol", type=float, default=DEV_BUY_SOL)
        p.add_argument("--max-dev-buy", type=float, default=launch_one.MAX_DEV_BUY_SOL,
                       help="per-run dev-buy ceiling; raising it is an explicit act on THIS run only (devpnl.py: "
                            "a 2.5-5.5 SOL bag sold at t+30 s replicated on 2 hours; 0.3-0.7 is break-even)")
        p.add_argument("--sell-at", type=float, default=SELL_AT_SOL, help="outside SOL that triggers the sell; 0 = hold-s only")
        p.add_argument("--wallet", default="", help="creator wallet pubkey from ~/.devbot/wallets (default: main DZ)")
        p.add_argument("--trigger", choices=("first", "hot"), default="first",
                       help="first: be first on a fresh post (old); hot: join a theme >= --hot-n creators share (P3)")
        p.add_argument("--hot-n", type=int, default=4)
        p.add_argument("--min-age-s", type=float, default=0.0,
                       help="hold the launch until the post is this old (P4: 60); raise --max-age-s with it")
        p.add_argument("--launcher", choices=("pumpportal", "j7"), default="pumpportal")
        p.add_argument("--j7-key-env", default="J7_WALLET_KEY",
                       help="the .env name holding j7tracker's encrypted key for THIS creator wallet")
        p.add_argument("--j7-region", choices=tuple(j7.BASES), default="nyc")
        p.add_argument("--x-post", action="store_true",
                       help="announce each launch from our X account first and link that post as the coin's twitter")
        p.add_argument("--floor-pct", type=float, default=launch_one.FLOOR_PCT,
                       help="keep this %% of the live balance untouched after a launch's full cost (per run)")
        p.add_argument("--hold-s", type=float, default=HOLD_S)
        p.add_argument("--max-per-hour", type=int, default=MAX_PER_HOUR)
        p.add_argument("--design-gap-s", type=float, default=DESIGN_GAP_S,
                       help="at least this long between designs (art quota); 0 = design every post")
        p.add_argument("--dry-per-hour", type=int, default=DRY_PER_HOUR,
                       help="designs an hour in a dry run: each costs Workers AI quota the bot shares")
        p.add_argument("--budget-pct", type=float, default=BUDGET_PCT)
    a = ap.parse_args(argv)
    if a.cmd == "replay":
        a.send = False
    if a.send and a.no_art:
        ap.error("--no-art is for dry runs: a launch needs real art")
    if a.x_post and not all(keys().get(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")):
        ap.error("--x-post needs X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN and X_ACCESS_SECRET")
    if a.launcher == "j7" and not launch_one._env(launch_one.ENV_PATH, a.j7_key_env):
        ap.error(f"--launcher j7 needs {a.j7_key_env} in {launch_one.ENV_PATH}: import the creator wallet in "
                 "j7tracker (Deploy Settings > Wallets) and store the string it shows")
    if a.dev_buy_sol > a.max_dev_buy:
        ap.error(f"--dev-buy-sol {a.dev_buy_sol:g} is over --max-dev-buy {a.max_dev_buy:g}")
    k = keys()
    return replay(a, k) if a.cmd == "replay" else live(a, k)


if __name__ == "__main__":
    sys.exit(main())
