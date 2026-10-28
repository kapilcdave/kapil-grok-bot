"""What pump.fun is launching and buying right now, measured instead of guessed.

Every POLL_S the bot stores pump.fun's newest launches (supply: what devs are
copying) and most recently traded coins (demand: what is being bought) in a
48 h SQLite window. A narrative is a set of name/ticker terms that recur across
many coins from different creators; it is scored on

    launch velocity  launches in the last hour vs the term's own 6 h baseline
    traction         sum over its coins of log2(peak USD mcap / launch mcap), capped
                     at 64x per coin and halved for every 2 h of coin age

and terms that keep landing in the same coins are merged, so {elon, musk, x}
or {ai, agent, terminal} come out as one multi-faceted theme with evidence.

A term group is only the surface of a wave; the story is usually an X post. Coins link
one in their `twitter` field, so it is stored, and `post_texts` reads the linked posts
through X's public oEmbed endpoint (no account needed), cached in the same file.
"""
from __future__ import annotations

import html
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from urllib.parse import quote

from . import http

API = "https://frontend-api-v3.pump.fun"
FEEDS = (
    "/coins?offset=0&limit=100&sort=created_timestamp&order=DESC&includeNsfw=false",
    "/coins?offset=0&limit=100&sort=last_trade_timestamp&order=DESC&includeNsfw=false",
    "/coins/currently-live?offset=0&limit=100&includeNsfw=false",
)
KEEP_H = 48
NOW_MIN = 60          # "now" window for launch velocity
BASE_H = 6            # baseline window before it, and the traction window
LAUNCH_USD = 4000     # a fresh pump.fun curve's market cap, roughly
MAX_GAIN = 6.0        # log2 cap: 64x (~$256k) per coin
HALF_LIFE_H = 2.0     # traction decay with coin age, so "hot" means now
MIN_COINS = 3
MIN_CREATORS = 2

STOP = set("""
a an and are as at be been but by can do for from get go got had has have he her his how i if in into is it its
just let like me more my no not now of off on one only or our out over so than that the their them then there
these they this to too up us was we were what when who why will with you your yours all any about after again
also am back being both did does each few here him most much must new next oh ok okay own same she should some
such very via yes yet ever every here's it's don't dont im ive youre
coin coins token tokens sol solana pump pumpfun fun memecoin meme memes crypto official real first launch launched
community dev devs ca contract buy buying hold holder holders holding chart moon send sending lfg gm wagmi ngmi
project supply liquidity locked burn burned renounced tax website twitter telegram join x.com https http www com
inu v2 v3 2.0 ai16z the1 jeet jeets degen degens bag bags pumping mooning million billion trillion cto
going gonna everyone everything someone something anyone nothing thing make made say said says know
time day today tonight still really way want need see look right left big small little best great good
deploy deployed deploying using used j7tracker tracker created launchpad platform
""".split())
KEEP_SHORT = {"ai", "x", "us", "uk", "eu", "tv", "og"}


def terms(name: str, symbol: str, extra_stop: frozenset[str] | set[str] = frozenset()) -> set[str]:
    """The meme lives in the name and ticker. Descriptions are left out on purpose: launch tools
    paste the same boilerplate into dozens of creators' coins ("verify before copy", "built on
    vibe energy"), which reads exactly like a narrative and isn't one.

    extra_stop adds caller-specific filler for this call only. A caller must never do `STOP |= ...`:
    that mutates the module's set in place and silently re-stops every other caller in the process."""
    stop = STOP | extra_stop if extra_stop else STOP

    def words(text: str) -> list[str]:
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)          # OnlyDoge -> Only Doge
        out = []
        for w in re.findall(r"[^\W\d_][\w']*", text):              # any script: Маня, 猫
            w = w.lower().strip("'")
            if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
                w = w[:-1]                                          # cats -> cat
            if w in stop or (len(w) < 3 and w not in KEEP_SHORT and w.isascii()):
                continue
            out.append(w)
        return out

    found = set(words(name)) | set(words(symbol))
    sym = symbol.lower().lstrip("$")
    if len(sym) >= 3 and sym not in stop:
        found.add(sym)
    return found


@dataclass
class Coin:
    mint: str
    name: str
    symbol: str
    creator: str
    created: float      # epoch seconds
    peak_usd: float
    replies: int
    complete: bool
    description: str = ""   # context for the model only; never a narrative term
    twitter: str = ""       # the X link the coin's creator attached, often the post the coin is about
    image: str = ""         # its picture's URL: a live coin's variant edits this image

    @property
    def gain(self) -> float:
        """log2 of how far it pumped from launch, 0 if it never did, capped at 64x so one
        outlier runner can't stand in for a narrative that many coins share."""
        return min(MAX_GAIN, max(0.0, math.log2(max(self.peak_usd, 1) / LAUNCH_USD)))


@dataclass
class Narrative:
    terms: list[str]
    score: float
    launches_1h: int
    base_per_h: float
    traction: float
    coins_6h: int
    creators: int
    winners: int        # coins that reached 4x launch mcap
    examples: list[Coin] = field(default_factory=list)
    lift: float | None = None   # None until the collector has an hour of baseline
    live: str = ""              # set by hot_now(): one coin being bought minutes after launch

    def evidence(self) -> str:
        if self.live:
            return self.live
        ex =", ".join(f"{c.name} (${c.symbol}) ${c.peak_usd / 1000:.0f}k" for c in self.examples[:4])
        vel = f"x{self.lift:.1f} vs baseline" if self.lift is not None else "baseline warming up"
        return (f"[{' / '.join(self.terms)}] score {self.score:.1f}: {self.launches_1h} launches last hour "
                f"({vel}), {self.coins_6h} coins / {self.creators} creators in {BASE_H}h, "
                f"{self.winners} hit 4x. e.g. {ex}")


class Trends:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS coins (
                mint TEXT PRIMARY KEY, name TEXT, symbol TEXT, description TEXT, creator TEXT,
                created REAL, peak_usd REAL, replies INTEGER, complete INTEGER, seen REAL);
            CREATE INDEX IF NOT EXISTS coins_created ON coins(created);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS posts (url TEXT PRIMARY KEY, text TEXT, fetched REAL);
        """)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(coins)")}
        if "twitter" not in cols:  # added after the first deploy; old rows just have no link
            self.db.execute("ALTER TABLE coins ADD COLUMN twitter TEXT DEFAULT ''")
        self.db.commit()

    # ---- collection -----------------------------------------------------------
    def poll(self) -> int:
        """One pass over the feeds; returns rows upserted. Errors on one feed don't stop the others."""
        now, n = time.time(), 0
        for path in FEEDS:
            try:
                rows = http.get_json(API + path, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            except (http.HttpError, OSError, ValueError):
                continue
            for c in rows if isinstance(rows, list) else []:
                if not c.get("mint") or c.get("nsfw") or c.get("is_banned"):
                    continue
                peak = max(float(c.get("usd_market_cap") or 0), float(c.get("ath_market_cap") or 0))
                self.db.execute(
                    """INSERT INTO coins (mint, name, symbol, description, creator, created, peak_usd,
                                          replies, complete, seen, twitter)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(mint) DO UPDATE SET peak_usd=MAX(peak_usd, excluded.peak_usd),
                         replies=excluded.replies, complete=excluded.complete, seen=excluded.seen""",
                    (c["mint"], c.get("name") or "", c.get("symbol") or "", (c.get("description") or "")[:400],
                     c.get("creator") or "", (c.get("created_timestamp") or 0) / 1000, peak,
                     int(c.get("reply_count") or 0), int(bool(c.get("complete"))), now,
                     (c.get("twitter") or "")[:300]))
                n += 1
        if self.db.execute("SELECT value FROM meta WHERE key='first_poll'").fetchone() is None:
            self.db.execute("INSERT INTO meta VALUES ('first_poll', ?)", (str(now),))
        self.db.execute("DELETE FROM coins WHERE created < ?", (now - KEEP_H * 3600,))
        self.db.commit()
        return n

    def post_texts(self, n: Narrative, limit: int = 3) -> list[str]:
        """Text of the X posts this narrative's coins link to, most-linked first, as
        "@handle: text". Fetched once per post through X's oEmbed endpoint and cached."""
        counts: dict[str, int] = {}
        for c in n.examples:
            url = status_url(c.twitter)
            if url:
                counts[url] = counts.get(url, 0) + 1
        out = []
        for url in sorted(counts, key=counts.get, reverse=True)[:limit]:
            row = self.db.execute("SELECT text FROM posts WHERE url=?", (url,)).fetchone()
            if row is None:
                text = fetch_post(url)
                self.db.execute("INSERT OR REPLACE INTO posts VALUES (?,?,?)", (url, text, time.time()))
                self.db.commit()
            else:
                text = row[0]
            if text:
                out.append(text)
        return out

    def coverage_h(self) -> float:
        row = self.db.execute("SELECT value FROM meta WHERE key='first_poll'").fetchone()
        return (time.time() - float(row[0])) / 3600 if row else 0.0

    # ---- analysis -------------------------------------------------------------
    def narratives(self, *, limit: int = 8, exclude_terms: set[str] | None = None,
                   now: float | None = None) -> list[Narrative]:
        now = now or time.time()
        rows = self.db.execute(
            "SELECT mint,name,symbol,description,creator,created,peak_usd,replies,complete,twitter FROM coins "
            "WHERE created >= ?", (now - (BASE_H * 3600 + NOW_MIN * 60),)).fetchall()
        coins: dict[str, Coin] = {}
        by_term: dict[str, set[str]] = {}
        for mint, name, symbol, desc, creator, created, peak, replies, complete, twitter in rows:
            coins[mint] = Coin(mint, name, symbol, creator, created, peak, replies, bool(complete), desc,
                               twitter or "")
            for t in terms(name, symbol):
                by_term.setdefault(t, set()).add(mint)
        # A baseline needs history; until the collector has covered it, judge on traction alone.
        base_h = min(BASE_H, max(0.0, self.coverage_h() - NOW_MIN / 60))
        cut_now = now - NOW_MIN * 60

        def score_term(mints: set[str]) -> tuple[float, dict] | None:
            cs = [coins[m] for m in mints]
            if len(cs) < MIN_COINS or len({c.creator for c in cs}) < MIN_CREATORS:
                return None
            launches_1h = sum(c.created >= cut_now for c in cs)
            base = sum(c.created < cut_now for c in cs) / base_h if base_h >= 1 else 0.0
            # A coin's pump counts fully if fresh and half as much every HALF_LIFE_H after launch.
            traction = sum(c.gain * 0.5 ** ((now - c.created) / 3600 / HALF_LIFE_H) for c in cs)
            lift = (launches_1h + 1) / (base + 1) if base_h >= 1 else None
            s = traction + math.log2(1 + launches_1h * (lift or 1.0))
            return s, {"launches_1h": launches_1h, "base_per_h": base, "lift": lift, "traction": traction,
                       "coins_6h": len(cs), "creators": len({c.creator for c in cs}),
                       "winners": sum(c.gain >= 2 for c in cs)}

        scored = []
        for t, mints in by_term.items():
            r = score_term(mints)
            if r:
                scored.append((r[0], t, mints))
        scored.sort(reverse=True)

        # Merge terms that keep landing in the same coins into one narrative.
        top = scored[:80]
        used: set[str] = set()
        out: list[Narrative] = []
        exclude = exclude_terms or set()
        for _s, t, mints in top:
            if t in used:
                continue
            group, union = [t], set(mints)
            for _s2, t2, m2 in top:
                if t2 in used or t2 == t:
                    continue
                if len(mints & m2) / min(len(mints), len(m2)) >= 0.6:
                    group.append(t2)
                    union |= m2
            used.update(group)
            if exclude & set(group):
                continue
            r = score_term(union)
            if not r:
                continue
            examples = sorted((coins[m] for m in union), key=lambda c: c.peak_usd, reverse=True)
            out.append(Narrative(terms=group[:5], score=r[0], examples=examples[:6], **r[1]))
            if len(out) >= limit:
                break
        return sorted(out, key=lambda n: n.score, reverse=True)


TRADES = "https://swap-api.pump.fun/v2/coins/{}/trades?limit=100&cursor=0&minSolAmount=0"
HOT_CHECK = 10      # the trade API rate-limits after ~18 quick calls, so only the top candidates are checked
COPY_WINDOW_MIN = 30


@dataclass
class Heat:
    buyers: int         # unique wallets that bought, minus the creator and the launch-second bundle
    buy_sol: float
    sells: int


def heat(coin: dict, trades: list[dict]) -> Heat:
    """Who is actually buying. The creator's wallet and anything in the launch second (a bundled
    dev buy, often from other wallets) are not attention, so they don't count."""
    launch = (coin.get("created_timestamp") or 0) / 1000
    buys = [t for t in trades if t.get("type") == "buy" and t.get("userAddress") != coin.get("creator")
            and _ts(t.get("timestamp")) > launch + 1]
    return Heat(len({t["userAddress"] for t in buys}), sum(float(t.get("amountSol") or 0) for t in buys),
                sum(t.get("type") == "sell" for t in trades))


def _ts(iso) -> float:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _coin(c: dict) -> Coin:
    return Coin(c["mint"], c.get("name") or "", c.get("symbol") or "", c.get("creator") or "",
                (c.get("created_timestamp") or 0) / 1000,
                max(float(c.get("usd_market_cap") or 0), float(c.get("ath_market_cap") or 0)),
                int(c.get("reply_count") or 0), bool(c.get("complete")), (c.get("description") or "")[:400],
                (c.get("twitter") or "")[:300], c.get("image_uri") or "")


def hot_now(fresh_min: float, min_buyers: int, *, limit: int = 4,
            now: float | None = None) -> tuple[list[Narrative], list[str]]:
    """Coins launched in the last `fresh_min` minutes that people are buying right now, hottest
    first, read live from pump.fun (not from the 2-minute collector). Returns (narratives, one
    line per coin checked) so an empty result can say what it saw."""
    now = now or time.time()
    rows: dict[str, dict] = {}
    for path in FEEDS:
        try:
            got = http.get_json(API + path, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        except (http.HttpError, OSError, ValueError):
            continue
        for c in got if isinstance(got, list) else []:
            if c.get("mint") and not c.get("nsfw") and not c.get("is_banned"):
                rows[c["mint"]] = c
    age = {m: (now - (c.get("created_timestamp") or 0) / 1000) / 60 for m, c in rows.items()}
    fresh = [c for m, c in rows.items() if 0 <= age[m] <= fresh_min and not c.get("complete")]
    fresh.sort(key=lambda c: _coin(c).peak_usd, reverse=True)
    checked, hot = [], []
    for c in fresh[:HOT_CHECK]:
        try:
            trades = http.get_json(TRADES.format(c["mint"]), headers={"User-Agent": "Mozilla/5.0"},
                                   timeout=15).get("trades") or []
        except (http.HttpError, OSError, ValueError, AttributeError) as e:
            checked.append(f"${c.get('symbol')}: trades unavailable ({str(e)[:40]})")
            break  # rate-limited: the rest would fail too
        h = heat(c, trades)
        checked.append(f"${c.get('symbol')} {age[c['mint']]:.1f}m: {h.buyers} buyers")
        if h.buyers >= min_buyers:
            hot.append((h, c))
        time.sleep(0.3)
    hot.sort(key=lambda hc: hc[0].buyers, reverse=True)
    out = []
    for h, c in hot[:limit]:
        coin, ts = _coin(c), terms(c.get("name") or "", c.get("symbol") or "")
        copies = [_coin(o) for m, o in rows.items() if m != c["mint"] and age[m] <= COPY_WINDOW_MIN
                  and ts & terms(o.get("name") or "", o.get("symbol") or "")]
        mcap = float(c.get("usd_market_cap") or 0)
        live = (f"LIVE: {coin.name} (${coin.symbol}) launched {age[c['mint']]:.1f} min ago: {h.buyers} buyers, "
                f"{h.buy_sol:.1f} SOL bought, {h.sells} sells, mcap ${mcap / 1000:.1f}k (peak ${coin.peak_usd / 1000:.1f}k)")
        if copies:
            live += ". Copies already out: " + ", ".join(f"{o.name} (${o.symbol})" for o in copies[:4])
        out.append(Narrative(terms=sorted(ts) or [coin.name.lower()], score=h.buyers, launches_1h=len(copies) + 1,
                             base_per_h=0.0, traction=h.buy_sol, coins_6h=len(copies) + 1,
                             creators=len({o.creator for o in copies} | {coin.creator}), winners=0,
                             examples=[coin] + copies[:3], live=live))
    return out, checked


STATUS_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15}|i)/status/(\d+)")


def fetch_coin(mint: str) -> Coin:
    """One pump.fun coin by mint address (for /clone <mint>)."""
    try:
        c = http.get_json(f"{API}/coins/{quote(mint, safe='')}", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    except (http.HttpError, OSError, ValueError) as e:
        raise RuntimeError(f"pump.fun has no coin {mint}: {str(e)[:80]}") from None
    if not isinstance(c, dict) or not c.get("mint"):
        raise RuntimeError(f"pump.fun has no coin {mint}")
    return _coin(c)


def status_url(link: str) -> str:
    """Canonical post URL if `link` points at a single X post (not a profile or community)."""
    m = STATUS_RE.search(link or "")
    return f"https://x.com/{m.group(1)}/status/{m.group(2)}" if m else ""


def fetch_post(url: str) -> str:
    """'@handle: text' of a public post via oEmbed, or '' if it is gone or unreachable."""
    try:
        data = http.get_json(f"https://publish.twitter.com/oembed?url={quote(url, safe='')}&omit_script=true",
                             timeout=10)
    except (http.HttpError, OSError, ValueError):
        return ""
    m = re.search(r"<p[^>]*>(.*?)</p>", data.get("html", ""), re.S)
    if not m:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", "", m.group(1).replace("<br>", " "))).strip()
    handle = (data.get("author_url") or "").rstrip("/").rsplit("/", 1)[-1]
    return f"@{handle}: {text}"[:300] if text else ""
