"""What a coin's story was at the moment it entered the runner zone. Each part is recorded raw, so the
scorer can change a rule later without re-collecting:

    story     the X post the coin links (oEmbed text) and Google News headlines for its name. Only
              headlines dated before the coin's admission may be used by a pick rule.
    siblings  every Solana coin DexScreener finds for the coin's name and ticker. Vamps and variants
              are the siblings, so "is this the original" is answered from this list.
    holders   holder count and top-10 share of supply (GeckoTerminal), the cabal question.
    judge     Jev (TypeSafe), only if TYPESAFE_API_KEY is set: what kind of story it is, whether the
              name misnames its subject, and whether a headline is a fresh event about it. Which
              look-alike is the original stays in code (size and launch order): asked to pick it from
              names, Jev chose the 50-min-late Kimchi vamp over the original (2026-10-21).

Kimchi, 2026-10-21: the coin launched 23:00Z on 10-20 and the English headlines are from 04:06Z on 10-21.
Headlines can lag a coin by hours, so the absence of news is not evidence that there is no story.
"""
from __future__ import annotations

import email.utils
import json
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from devbot import http
from devbot.trends import fetch_post, status_url, terms

from . import sources

JEV = "https://api.typesafe.ai/v1/systemone"
SIB_WINDOW_S = 48 * 3600     # a sibling launched within 48 h of the coin; older coins share words by chance
SIB_MIN_LIQ = 5_000


def news(query: str, limit: int = 8) -> list[dict]:
    """Google News headlines for `query` from the last 2 days: [{ts, title}], newest first."""
    url = f"https://news.google.com/rss/search?q={quote(query + ' when:2d')}&hl=en-US&gl=US&ceid=US:en"
    try:
        root = ET.fromstring(http.request("GET", url, timeout=15))
    except (http.HttpError, OSError, ET.ParseError):
        return []
    out = []
    for it in root.findall(".//item"):
        try:
            ts = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            continue
        out.append({"ts": ts, "title": (it.findtext("title") or "")[:200]})
    return sorted(out, key=lambda h: -h["ts"])[:limit]


def story(name: str, symbol: str, links: list[str]) -> dict:
    post_url = next((status_url(l) for l in links if status_url(l)), "")
    # The ticker is the meme ("Aura Final Boss Kimchi" trades as Kimchi); fall back to the longest name word.
    sym = re.sub(r"[^\w ]", "", symbol).strip()
    words = sorted(terms(name, ""), key=len, reverse=True)
    query = sym if len(sym) >= 3 else (words[0] if words else name)
    return {"post_url": post_url, "post": fetch_post(post_url) if post_url else "",
            "query": query, "news": news(query) if query else []}


def siblings(mint: str, name: str, symbol: str, created_s: float) -> list[dict]:
    """Coins sharing the name or ticker, launched within 48 h of this one, one row per mint."""
    seen: dict[str, dict] = {}
    for q in dict.fromkeys(x for x in (symbol, name) if x):
        try:
            pairs = sources.search(q)
        except (http.HttpError, OSError, ValueError):
            continue
        for p in pairs:
            b = p.get("baseToken") or {}
            m = b.get("address")
            if not m or m in sources.QUOTES:
                continue
            liq = (p.get("liquidity") or {}).get("usd") or 0
            row = seen.setdefault(m, {"mint": m, "name": b.get("name", ""), "symbol": b.get("symbol", ""),
                                      "created": 1e18, "mc": 0, "liq": 0})
            row["created"] = min(row["created"], (p.get("pairCreatedAt") or 1e18) / 1000)
            row["mc"] = max(row["mc"], p.get("marketCap") or p.get("fdv") or 0)
            row["liq"] = max(row["liq"], liq)
    return [r for r in seen.values()
            if r["mint"] == mint or (abs(r["created"] - created_s) <= SIB_WINDOW_S and r["liq"] >= SIB_MIN_LIQ)]


def vamp_flags(mint: str, sibs: list[dict]) -> dict:
    """Rank of this coin among its live siblings by market cap and by launch time. Rank 1 on both is
    the plain reading of "the original"; a coin launched later but bigger is the usual vamp shape."""
    live = [s for s in sibs if s["liq"] >= SIB_MIN_LIQ or s["mint"] == mint]
    by_mc = sorted(live, key=lambda s: -s["mc"])
    by_t = sorted(live, key=lambda s: s["created"])
    me = next((s for s in live if s["mint"] == mint), None)
    first = by_t[0] if by_t else None
    return {"n_sib": len(live) - 1,
            "mc_rank": next((i + 1 for i, s in enumerate(by_mc) if s["mint"] == mint), 0),
            "t_rank": next((i + 1 for i, s in enumerate(by_t) if s["mint"] == mint), 0),
            "lag_s": round(me["created"] - first["created"]) if me and first else 0}


def holders(mint: str) -> dict:
    """GeckoTerminal's holder count and top-10 share (keyless). The public Solana RPC answers
    getTokenLargestAccounts with 429 every time (2026-10-21), so it is not used. The share is recorded
    for every coin and kept OUT of the primary rule: Kimchi, the day's biggest runner, had 78.7% in
    its top 10, so whether a held float is a warning or the cabal's engine is a question for the tape."""
    try:
        a = sources.gt_get(f"/tokens/{mint}/info")["data"]["attributes"]
    except (http.HttpError, OSError, ValueError, KeyError, TypeError) as e:
        return {"err": str(e)[:120]}
    h = a.get("holders") or {}
    dist = h.get("distribution_percentage") or {}

    def pct(k: str) -> float | None:
        try:
            return round(float(dist[k]) / 100, 4)
        except (KeyError, TypeError, ValueError):
            return None
    return {"count": h.get("count"), "top10": pct("top_10"), "top11_20": pct("11_20"),
            "mint_auth": a.get("mint_authority"), "freeze_auth": a.get("freeze_authority"),
            "gt_score": a.get("gt_score"), "updated": h.get("last_updated")}


STORY_KINDS = {
    "real_world_news": "A real event reported or happening outside crypto: news, an incident, a viral real-life moment",
    "celebrity_post": "A post or action by a famous person or brand (e.g. Elon Musk, a CEO, a celebrity, a company)",
    "crypto_meta": "Crypto-internal: a platform, a trader, a KOL, a chain, an exchange or another coin",
    "pure_meme": "An internet joke, character or image with no specific real-world event behind it",
    "no_story": "Nothing in the material says what the coin is about",
}


def judge(coin: dict, st: dict, sibs: list[dict], admit_ts: float) -> dict:
    """One Jev request. Empty dict if there is no key or the call fails (the raw text is on the tape anyway)."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        return {}
    heads = [h["title"] for h in st.get("news", []) if h["ts"] <= admit_ts][:6]
    cands = sorted(sibs, key=lambda s: s["created"])[:12]
    state = {"coin": {"name": coin["name"], "ticker": coin["symbol"], "description": coin.get("description", "")[:500],
                      "linked_post": st.get("post", "")},
             "news_headlines": heads,
             "lookalike_coins": [{"id": f"c{i}", "name": s["name"], "ticker": s["symbol"],
                                  "launched_hours_before_this_coin": round((admit_ts - s["created"]) / 3600, 1)
                                  if s["mint"] != coin["mint"] else "this coin"} for i, s in enumerate(cands)]}
    qs = {
        "kind": {"type": "choice", "instructions": "What is the memecoin `coin` about, judging only from its name, "
                 "ticker, description, linked post and the news headlines?", "criteria": STORY_KINDS},
        "variant": {"type": "noul", "instructions": "Does the name or ticker of `coin` get its subject wrong or mark "
                    "itself as a spin-off: a misspelling or wrong name for the person, animal or thing it is about, "
                    "or a '2.0', 'baby', 'real', 'official', 'CTO', 'wife of' or 'son of' version?"},
        "news_event": {"type": "noul", "instructions": "Does any headline in `news_headlines` report a specific new "
                       "event from the last day or two about the exact subject `coin` is named after, one that would "
                       "explain people buying a coin about it right now? Ordinary articles that merely mention the "
                       "word (recipes, reviews, software, prices) are not such an event."},
    }
    try:
        out = json.loads(http.post_json(JEV, {"model": "jev-latest", "state": state, "questions": qs},
                                        headers={"Authorization": f"Bearer {key}"}, timeout=30))
    except (http.HttpError, OSError, ValueError) as e:
        return {"err": str(e)[:160]}
    a = out.get("answers") or {}
    res = {"model": out.get("model"), "kind": (a.get("kind") or {}).get("choice"),
           "kind_p": (a.get("kind") or {}).get("probabilities"),
           "variant": (a.get("variant") or {}).get("noul"), "news_event": (a.get("news_event") or {}).get("noul")}
    return res
