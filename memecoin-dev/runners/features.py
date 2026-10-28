"""The rest of a coin's profile, beyond the story: how it was launched, who launched it, whether its
buyers are people, and what kind of story it rides. Recorded raw for every admitted coin, so a rule can
be fitted and preregistered on them later.

    launch   Jupiter token API (keyless): launchpad, creation and graduation time, the dev wallet and
             how many coins it has launched. `grad_lag_s` = 0 means the coin graduated in the second it
             was created: the dev (and friends) bought the whole curve at launch.
    flow     Jupiter's organic stats AT ADMISSION: organic score, organic buyers vs all traders, the
             organic share of buy volume, holder count and change, top-holder and dev share.
    candles  GeckoTerminal 1 min candles of the coin's first hour (curve pool, then the graduated
             pool): market cap at the first candle, at 5, 15 and 60 min, the first-hour high.
             The operator's "first candle at $400k-$3M, then rug" shape is `mc_first` / `mc_5m`.
    dev      Solana RPC: the SOL the dev wallet spent and the supply it got in the create transaction,
             and how long before the launch the wallet was funded (a fresh wallet is a burner).
    post     The linked X post's own timestamp (from its id), so a coin launched after the post is
             following a story and one launched long before it is borrowing one.
    kind     Jev, a finer story type (charity, prediction market, celebrity post, ...) and whether
             the linked post is about exactly what the coin is named.

2026-10-21 interim look: the two picks that rugged (TRUMP -92%, ChatGPT -100% within 2 h) both had
grad_lag_s = 0 and 19 / 0 organic buyers among 1,338 / 2,116 traders.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime

from devbot import http

from . import sources

JUP = "https://lite-api.jup.ag/tokens/v2/search?query="
JEV = "https://api.typesafe.ai/v1/systemone"
RPC = (os.environ.get("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com").split(",")[0].strip()
LAMPORTS = 1e9
X_EPOCH_MS = 1288834974657


class _Spaced:
    """One host's calls spaced `gap` apart across threads, one retry after a 429."""

    def __init__(self, gap: float) -> None:
        self.gap, self.lock, self.last = gap, threading.Lock(), 0.0

    def __call__(self, fn):
        for attempt in range(3):
            with self.lock:
                wait = self.last + self.gap - time.time()
                if wait > 0:
                    time.sleep(wait)
                self.last = time.time()
            try:
                return fn()
            except http.HttpError as e:
                if e.status != 429 or attempt == 2:
                    raise
                time.sleep(10 * (attempt + 1))
        return None


_jup = _Spaced(1.5)     # lite-api 429'd six unspaced calls on 2026-10-21
_rpc = _Spaced(0.5)


def _ts(iso: str | None) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None
    except ValueError:
        return None


def jup_token(mint: str) -> dict:
    rows = _jup(lambda: http.get_json(JUP + mint, timeout=20)) or []
    return next((t for t in rows if t.get("id") == mint), {})


def launch(t: dict) -> dict:
    """Facts fixed at launch (safe to read after the fact)."""
    a = t.get("audit") or {}
    created, grad = _ts(t.get("createdAt")), _ts(t.get("graduatedAt"))
    return {"mint": t.get("id"), "launchpad": t.get("launchpad"), "created": created, "graduated": grad,
            "grad_lag_s": round(grad - created) if grad and created else None,
            "dev": t.get("dev"), "dev_mints": a.get("devMints"), "dev_migrations": a.get("devMigrations"),
            "token2022": t.get("tokenProgram", "").startswith("Tokenz"), "supply": t.get("totalSupply"),
            "first_pool": (t.get("firstPool") or {}).get("id"), "grad_pool": t.get("graduatedPool"),
            "twitter": t.get("twitter"), "website": t.get("website")}


def flow(t: dict) -> dict:
    """Facts that change by the minute; only meaningful when read AT admission."""
    a = t.get("audit") or {}
    out = {"organic_score": t.get("organicScore"), "organic_label": t.get("organicScoreLabel"),
           "holders": t.get("holderCount"), "top_holders_pct": a.get("topHoldersPercentage"),
           "dev_pct": a.get("devBalancePercentage"), "sus": a.get("isSus"),
           "ct_likes": t.get("ctLikes"), "smart_ct_likes": t.get("smartCtLikes"), "verified": t.get("isVerified")}
    for w in ("5m", "1h", "6h", "24h"):
        s = t.get(f"stats{w}") or {}
        bv = s.get("buyVolume") or 0
        out[w] = {"traders": s.get("numTraders"), "org_buyers": s.get("numOrganicBuyers"),
                  "net_buyers": s.get("numNetBuyers"), "buys": s.get("numBuys"), "sells": s.get("numSells"),
                  "buy_vol": bv, "sell_vol": s.get("sellVolume"),
                  "org_buy_share": round((s.get("buyOrganicVolume") or 0) / bv, 4) if bv else None,
                  "holder_chg": s.get("holderChange"), "px_chg": s.get("priceChange")}
    return out


def _candles(pool: str, start: float, end: float) -> list[list[float]]:
    path = (f"/pools/{pool}/ohlcv/minute?aggregate=1&before_timestamp={int(end)}&limit={int((end - start) / 60) + 2}"
            f"&currency=usd&token=base")
    rows = ((sources.gt_get(path).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    return sorted(r for r in rows if r[0] >= start - 60)


def curve_pool(mint: str, grad_pool: str | None) -> str | None:
    """The coin's earliest pair other than the graduated one (its bonding curve), from DexScreener
    (300 req/min) so GeckoTerminal's 30/min goes to the candles."""
    ps = http.get_json(f"{sources.DS}/token-pairs/v1/solana/{mint}", timeout=20) or []
    ps = sorted((p.get("pairCreatedAt") or 0, p["pairAddress"]) for p in ps
                if p.get("pairAddress") and p["pairAddress"] != grad_pool and (p.get("baseToken") or {}).get("address") == mint)
    return ps[0][1] if ps else None


def candles(lc: dict) -> dict:
    """Market cap path over the first hour, curve pool before graduation and graduated pool after."""
    created, supply = lc.get("created"), lc.get("supply") or 1e9
    if not created:
        return {}
    end, grad = created + 3600, lc.get("graduated") or 1e18
    rows: dict[int, list[float]] = {}
    gp = lc.get("grad_pool")
    pools = [p for p in dict.fromkeys((curve_pool(lc["mint"], gp), gp)) if p]
    for p in pools:
        try:
            for r in _candles(p, created, end):
                curve = p != gp
                if (r[0] < grad) == curve or len(pools) == 1:
                    rows.setdefault(int(r[0]), r)
        except (http.HttpError, OSError, ValueError, KeyError, TypeError):
            continue
    if not rows:
        return {}
    cs = [rows[k] for k in sorted(rows)]

    def mc_at(minutes: int) -> float | None:
        r = [c for c in cs if c[0] <= created + minutes * 60]
        return round(r[-1][4] * supply) if r else None
    cs = [c for c in cs if c[4] > 0] or cs     # DBC curve candles can open at a price of 0
    hi = max(cs, key=lambda c: c[2])
    return {"mc_first_hi": round(cs[0][2] * supply), "mc_first": round(cs[0][4] * supply),
            "vol_first": round(cs[0][5]), "mc_5m": mc_at(5), "mc_15m": mc_at(15), "mc_60m": mc_at(60),
            "mc_hi_60m": round(hi[2] * supply), "min_to_hi": round((hi[0] - created) / 60, 1),
            "vol_60m": round(sum(c[5] for c in cs)), "n": len(cs)}


def _rpc_call(method: str, params: list) -> object:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    out = json.loads(_rpc(lambda: http.post_json(RPC, body, timeout=20)) or b"{}")
    if "error" in out:
        raise http.HttpError(429 if "429" in str(out["error"]) else 500, str(out["error"]))
    return out.get("result")


def _dev_delta(sig: str, mint: str, dev: str) -> dict | None:
    """The dev's change in SOL and in the coin's tokens in one transaction."""
    tx = _rpc_call("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
    if not tx:
        return None
    meta = tx.get("meta") or {}

    def bal(key: str) -> float:
        return sum(float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in meta.get(key) or []
                   if b.get("mint") == mint and b.get("owner") == dev)
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    if dev not in keys:
        return None
    i = keys.index(dev)
    return {"sol": (meta["postBalances"][i] - meta["preBalances"][i]) / LAMPORTS,
            "tokens": bal("postTokenBalances") - bal("preTokenBalances"),
            "holders": len({b.get("owner") for b in meta.get("postTokenBalances") or [] if b.get("mint") == mint})}


def dev_buy(mint: str, dev: str | None, created: float | None, pages: int = 3) -> dict:
    """SOL the dev spent and supply it received in the create transaction, and the wallet's funding.
    A dev with thousands of transactions (a serial launcher) is cut at `pages` x 1000 and marked."""
    if not dev or not created:
        return {}
    sigs, before = [], None
    for _ in range(pages):
        opts = {"limit": 1000, **({"before": before} if before else {})}
        page = _rpc_call("getSignaturesForAddress", [dev, opts]) or []
        sigs += page
        if len(page) < 1000 or (page and (page[-1].get("blockTime") or 0) < created - 600):
            break
        before = page[-1]["signature"]
    full = len(sigs) < pages * 1000
    out: dict = {"dev_txs": len(sigs), "dev_hist_full": full}
    # The create is the dev's first transaction that gives it the coin; a burner wallet's funding and setup
    # transactions come a minute before it (TRUMP 2026-10-21: 8 setup txs at -63..-61 s), so look nearest first.
    near = [s for s in sigs if s.get("blockTime") and created - 120 <= s["blockTime"] <= created + 60 and not s.get("err")]
    after = sorted((s for s in sigs if s.get("blockTime") and created <= s["blockTime"] <= created + 600
                    and not s.get("err")), key=lambda s: s["blockTime"])
    create_sig = None
    for s in sorted(near, key=lambda s: abs(s["blockTime"] - created))[:8]:
        d = _dev_delta(s["signature"], mint, dev)
        if d and d["tokens"] > 0:
            out.update({"dev_buy_sol": round(-d["sol"], 3), "dev_buy_tokens": d["tokens"],
                        "dev_buy_ts": s["blockTime"], "create_holders": d["holders"]})
            create_sig = s["signature"]
            break
    if create_sig:
        # Within 10 min: tokens the dev sold (got SOL back) and tokens it moved to other wallets for free.
        sold = moved = 0.0
        for s in [s for s in after if s["signature"] != create_sig][:5]:
            d = _dev_delta(s["signature"], mint, dev)
            if d and d["tokens"] < 0:
                if d["sol"] > 0.01:
                    sold -= d["tokens"]
                else:
                    moved -= d["tokens"]
        out.update({"dev_sold_10m": sold, "dev_moved_10m": moved})
    if full and sigs:
        first = sigs[-1]
        out["dev_funded_s_before"] = round(created - (first.get("blockTime") or created))
    return out


FEE_SAMPLE = 25
BASE_FEE = 5000             # lamports per signature
TIP_MAX = 0.05              # SOL: a system transfer this small from the trader to a bystander account is a tip


def _tx_fees(tx: dict) -> tuple[float, float]:
    """(priority fee, tips) in SOL paid by the fee payer of one trade. Tips are the payer's small native
    SOL transfers to accounts that hold no token in the transaction: Jito, Nozomi, 0slot and the other
    landing services the trading terminals (Axiom, Photon, BullX) pay by default. On a pump.fun curve
    trade the protocol and creator fees are also native transfers and are counted here too."""
    meta, msg = tx.get("meta") or {}, tx["transaction"]["message"]
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg["accountKeys"]]
    payer = keys[0]
    n_sig = len(tx["transaction"].get("signatures") or [1])
    prio = max(0, (meta.get("fee") or 0) - BASE_FEE * n_sig) / LAMPORTS
    holders = {b.get("owner") for b in (meta.get("postTokenBalances") or []) + (meta.get("preTokenBalances") or [])}
    ins = list(msg.get("instructions") or [])
    for inner in meta.get("innerInstructions") or []:
        ins += inner.get("instructions") or []
    tips = 0.0
    for i in ins:
        pr = i.get("parsed") if isinstance(i, dict) else None
        if not isinstance(pr, dict) or i.get("program") != "system" or pr.get("type") != "transfer":
            continue
        info = pr.get("info") or {}
        sol = (info.get("lamports") or 0) / LAMPORTS
        if info.get("source") == payer and info.get("destination") not in holders and 0 < sol <= TIP_MAX:
            tips += sol
    return prio, tips


def fees(mint: str, n_tx_1h: int | None, n_tx_24h: int | None) -> dict:
    """SOL the coin's traders pay on top of the trade, from its last FEE_SAMPLE transactions: wash-trading
    bots pay the minimum and no tip; people on trading terminals pay priority fees and tips. Totals are the
    sample's mean per trade times Jupiter's trade counts."""
    sigs = [s for s in _rpc_call("getSignaturesForAddress", [mint, {"limit": FEE_SAMPLE}]) or [] if not s.get("err")]
    per = []
    for s in sigs:
        try:
            tx = _rpc_call("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        except http.HttpError:
            continue
        if tx:
            per.append(_tx_fees(tx))
    if not per:
        return {}
    prio = sum(p for p, _ in per) / len(per)
    tip = sum(t for _, t in per) / len(per)
    span = (sigs[0].get("blockTime") or 0) - (sigs[-1].get("blockTime") or 0)
    return {"n": len(per), "span_s": span, "prio_sol": round(prio, 6), "tip_sol": round(tip, 6),
            "paid_share": round(sum(p + t > 0.0002 for p, t in per) / len(per), 3),
            "est_1h_sol": round((prio + tip) * n_tx_1h, 3) if n_tx_1h else None,
            "est_24h_sol": round((prio + tip) * n_tx_24h, 3) if n_tx_24h else None}


def post_time(post_url: str) -> dict:
    """An X status id carries its own millisecond timestamp."""
    m = re.search(r"/status/(\d+)", post_url or "")
    if not m:
        return {}
    ts = ((int(m.group(1)) >> 22) + X_EPOCH_MS) / 1000
    h = re.search(r"x\.com/([^/]+)/status", post_url)
    return {"post_ts": round(ts), "post_author": h.group(1).lower() if h else None}


STORY_KINDS = {
    "charity": "A charity, fundraiser, donation drive or good cause (the coin says fees or funds go to it)",
    "prediction_market": "A Polymarket or Kalshi market, bet or prediction-market post",
    "celebrity_post": "A post or action by a famous person, brand or company (Elon Musk, a CEO, a celebrity)",
    "real_world_news": "A real event outside crypto: news, an incident, a viral real-life moment",
    "viral_animal_or_person": "A specific animal or ordinary person who went viral (a pet, a zoo animal, a kid)",
    "crypto_meta": "Crypto-internal: a platform, a trader, a KOL, a chain, an exchange or another coin",
    "pure_meme": "An internet joke, character or image with no specific real-world event behind it",
    "no_story": "Nothing in the material says what the coin is about",
}


def kind(coin: dict, post: str, heads: list[str]) -> dict:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        return {}
    state = {"coin": {"name": coin.get("name", ""), "ticker": coin.get("symbol", ""),
                      "description": (coin.get("description") or "")[:500], "linked_post": post},
             "news_headlines": heads[:6]}
    qs = {
        "kind": {"type": "choice", "instructions": "What is the memecoin `coin` about, judging only from its name, "
                 "ticker, description, linked post and the news headlines?", "criteria": STORY_KINDS},
        "post_names_coin": {"type": "noul", "instructions": "Is `coin.linked_post` about exactly the person, animal, "
                            "thing or event that the coin's name and ticker name, so the coin is plainly the coin "
                            "for that post (not a joke on a side detail, and not an unrelated post)?"},
        "post_by_subject": {"type": "noul", "instructions": "Was `coin.linked_post` written by the famous person, "
                            "brand or organisation the coin is about, rather than by a fan, a trader or a news account?"},
        "mainstream": {"type": "noul", "instructions": "Is the coin's subject something ordinary people outside "
                       "crypto would be talking about today (a big news story, a famous person, a viral moment)?"},
    }
    try:
        out = json.loads(http.post_json(JEV, {"model": "jev-latest", "state": state, "questions": qs},
                                        headers={"Authorization": f"Bearer {key}"}, timeout=30))
    except (http.HttpError, OSError, ValueError) as e:
        return {"err": str(e)[:160]}
    a = out.get("answers") or {}
    return {"kind": (a.get("kind") or {}).get("choice"), "kind_p": (a.get("kind") or {}).get("probabilities"),
            **{q: (a.get(q) or {}).get("noul") for q in ("post_names_coin", "post_by_subject", "mainstream")}}


def text_flags(coin: dict, post: str) -> dict:
    blob = " ".join([coin.get("name", ""), coin.get("description") or "", post, " ".join(coin.get("links") or [])]).lower()
    return {"polymarket": "polymarket" in blob or "kalshi" in blob,
            "charity": bool(re.search(r"charit|donat|fundrais|gofundme|non-?profit|shelter|rescue", blob)),
            "n_links": len(coin.get("links") or []),
            "has_tg": any("t.me/" in l for l in coin.get("links") or []),
            "has_site": any(not re.search(r"x\.com|twitter\.com|t\.me/", l) for l in coin.get("links") or [])}


def collect(coin: dict, post_url: str, post: str, heads: list[str], at_admission: bool = True) -> dict:
    """Everything above for one coin. Each part fails alone (its error is recorded, the rest stands)."""
    out: dict = {}
    try:
        t = jup_token(coin["mint"])
        out["launch"] = launch(t)
        if at_admission:
            out["flow"] = flow(t)
    except (http.HttpError, OSError, ValueError) as e:
        out["launch"] = {"err": str(e)[:160]}
    lc = out["launch"]
    try:
        out["candles"] = candles(lc)
    except (http.HttpError, OSError, ValueError, KeyError, TypeError) as e:
        out["candles"] = {"err": str(e)[:160]}
    try:
        out["dev"] = dev_buy(coin["mint"], lc.get("dev"), lc.get("created"))
    except (http.HttpError, OSError, ValueError, KeyError, TypeError) as e:
        out["dev"] = {"err": str(e)[:160]}
    if at_admission:
        fl = out.get("flow") or {}
        n1 = sum(x or 0 for x in ((fl.get("1h") or {}).get("buys"), (fl.get("1h") or {}).get("sells")))
        n24 = sum(x or 0 for x in ((fl.get("24h") or {}).get("buys"), (fl.get("24h") or {}).get("sells")))
        try:
            out["fees"] = fees(coin["mint"], n1, n24)
        except (http.HttpError, OSError, ValueError, KeyError, TypeError) as e:
            out["fees"] = {"err": str(e)[:160]}
    out["post"] = post_time(post_url)
    out["text"] = text_flags(coin, post)
    out["kind"] = kind(coin, post, heads)
    return out
