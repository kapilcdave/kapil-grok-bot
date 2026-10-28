"""Where runner candidates come from and where their prices are read, all free and keyless.

    GeckoTerminal  /networks/solana/trending_pools and the top pools by 24 h volume (30 req/min)
    DexScreener    /token-profiles/latest, /token-boosts/latest and /top (60 req/min), and
                   /tokens/v1/solana/<up to 30 mints> for prices (300 req/min)

Measured 2026-10-21: trending_pools returns 20 pools a page; the tokens endpoint returns every
pair of every mint, so the deepest pair whose base token is the mint is the coin's price.
"""
from __future__ import annotations

import threading
import time

from devbot import http

GT = "https://api.geckoterminal.com/api/v2/networks/solana"
DS = "https://api.dexscreener.com"
QUOTES = {"So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}   # SOL, USDC, USDT


GT_GAP_S = 3.0          # GeckoTerminal 429'd an unspaced burst of 7 calls on 2026-10-21; its free limit is 30/min
_gt_lock = threading.Lock()
_gt_last = 0.0


def gt_get(path: str, retry: bool = True) -> dict:
    """One GeckoTerminal GET, spaced GT_GAP_S from any other (the discovery loop and the enrichment
    worker share the budget), with one retry after a 429."""
    global _gt_last
    for attempt in range(2):
        with _gt_lock:
            wait = _gt_last + GT_GAP_S - time.time()
            if wait > 0:
                time.sleep(wait)
            _gt_last = time.time()
        try:
            return http.get_json(f"{GT}{path}", timeout=20)
        except http.HttpError as e:
            if e.status != 429 or attempt or not retry:
                raise
            time.sleep(30)
    return {}


def _gt_mints(path: str) -> list[str]:
    out = []
    for p in gt_get(path).get("data") or []:
        rel = p.get("relationships") or {}
        for side in ("base_token", "quote_token"):
            tid = ((rel.get(side) or {}).get("data") or {}).get("id", "")
            mint = tid.split("_", 1)[-1]
            if mint and mint not in QUOTES:
                out.append(mint)
                break
    return out


GECKO_PATHS = ("/trending_pools?page=1&duration=5m", "/trending_pools?page=2&duration=5m",
               "/trending_pools?page=1&duration=1h", "/trending_pools?page=2&duration=1h",
               "/trending_pools?page=1&duration=6h", "/pools?page=1&sort=h24_volume_usd_desc",
               "/pools?page=1&sort=h24_tx_count_desc")


def gecko() -> list[str]:
    """Trending (5 m and 1 h two pages deep, 6 h), top 24 h volume and top 24 h tx count. Page 1
    alone missed every Meteora DBC runner of 2026-10-21 (NI, Lovot, MOON COIN: $50k to $3-5M in
    1-2 h); the tx-count sort and second pages surfaced them. A failed call costs only its own page."""
    mints: list[str] = []
    for path in GECKO_PATHS:
        try:
            mints += _gt_mints(path)
        except (http.HttpError, OSError, ValueError) as e:
            print(f"gecko {path}: {str(e)[:100]}", flush=True)
    return mints


def dex_profiles() -> list[dict]:
    """Newest DexScreener profiles and boosts (Solana only): {mint, description, links}."""
    out = []
    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        for p in http.get_json(f"{DS}{path}", timeout=20) or []:
            if p.get("chainId") == "solana" and p.get("tokenAddress"):
                out.append({"mint": p["tokenAddress"], "description": p.get("description") or "",
                            "links": [l.get("url") for l in p.get("links") or [] if l.get("url")]})
    return out


ALT_TTL_S = 600
ALT_PER_CALL = 15       # /token-pairs lookups per best_pairs call; hundreds at once stalled snapshots to 14 min (10-22)
RES_TTL_S = 900         # a GeckoTerminal reserve is reused for 15 min: liquidity moves slowly, its budget is 30/min
DUST_LIQ = 100          # a drained DAMM pool reads $0.02, which must not outrank a PumpSwap pool reading $0
_alt: dict[str, tuple[float, str]] = {}   # mint -> (looked up at, pair address it really trades in)


def best_pairs(mints: list[str]) -> dict[str, dict]:
    """mint -> its deepest DexScreener pair (base token = the mint), 30 mints per request.

    The batch /tokens endpoint returns only the dead Meteora DBC curve pair for a DBC coin that
    trades in a PumpSwap pool (MOON COIN, JONATHAN on 2026-10-21), so a coin whose best batch pair is
    a DBC curve or has no depth is looked up once per ALT_TTL_S through /token-pairs, and its real pair
    is priced by address."""
    best = _batch(mints)
    weak = [m for m in mints if m not in best or best[m].get("dexId") == "meteoradbc" or _depth(best[m])[1] < 1000]
    now = time.time()
    stale = sorted((m for m in weak if now - _alt.get(m, (0, ""))[0] > ALT_TTL_S), key=lambda m: _alt.get(m, (0, ""))[0])
    for m in stale[:ALT_PER_CALL]:
        if True:
            try:
                pairs = [p for p in http.get_json(f"{DS}/token-pairs/v1/solana/{m}", timeout=20) or []
                         if (p.get("baseToken") or {}).get("address") == m and p.get("dexId") != "meteoradbc"]
            except (http.HttpError, OSError, ValueError):
                continue
            _alt[m] = (now, max(pairs, key=_depth)["pairAddress"] if pairs else "")
    alts = {_alt[m][1]: m for m in weak if _alt.get(m, (0, ""))[1]}
    addrs = list(alts)
    for i in range(0, len(addrs), 30):
        try:
            got = http.get_json(f"{DS}/latest/dex/pairs/solana/{','.join(addrs[i:i + 30])}", timeout=20).get("pairs") or []
        except (http.HttpError, OSError, ValueError):
            continue
        for p in got:
            m = alts.get(p.get("pairAddress"))
            if m and (m not in best or _depth(p) > _depth(best[m])):
                best[m] = p
    return best


def _batch(mints: list[str]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        for p in http.get_json(f"{DS}/tokens/v1/solana/{','.join(chunk)}", timeout=20) or []:
            mint = (p.get("baseToken") or {}).get("address")
            if mint not in chunk:
                continue
            if mint not in best or _depth(p) > _depth(best[mint]):
                best[mint] = p
    return best


def _depth(p: dict) -> tuple[float, float]:
    """Liquidity, then 24 h volume: DexScreener reports liquidity 0 on the PumpSwap pools that
    Meteora DBC coins trade in, so without the tie-break the dead curve pair could win."""
    liq = (p.get("liquidity") or {}).get("usd") or 0
    return (liq if liq >= DUST_LIQ else 0, (p.get("volume") or {}).get("h24") or 0)


def gt_reserves(pools: list[str], retry: bool = True) -> dict[str, float]:
    """pool address -> GeckoTerminal reserve in USD, 30 pools per call. The fallback liquidity when
    DexScreener says 0 (2026-10-21: MOON COIN's pool read $0 there and $71k on GeckoTerminal)."""
    out: dict[str, float] = {}
    for i in range(0, len(pools), 30):
        for p in gt_get(f"/pools/multi/{','.join(pools[i:i + 30])}", retry).get("data") or []:
            a = p.get("attributes") or {}
            try:
                out[a["address"]] = float(a.get("reserve_in_usd") or 0)
            except (KeyError, TypeError, ValueError):
                continue
    return out


_res: dict[str, tuple[float, float]] = {}   # pool -> (read at, reserve USD)
_res_lock = threading.Lock()


def fill_liquidity(snaps: dict[str, dict], max_calls: int = 1, retry: bool = True) -> None:
    """In place: a snap whose DexScreener liquidity is 0 takes GeckoTerminal's reserve, marked liq_src.
    Reserves are cached RES_TTL_S and at most `max_calls` GeckoTerminal requests are made per call, so a
    price snapshot never waits on GeckoTerminal's rate limit."""
    zero = {m: s for m, s in snaps.items() if s["liq"] < DUST_LIQ and s.get("pair")}
    if not zero:
        return
    now = time.time()
    with _res_lock:
        need = [s["pair"] for s in zero.values() if now - _res.get(s["pair"], (0, 0))[0] > RES_TTL_S]
    if need:
        try:
            got = gt_reserves(need[:30 * max_calls], retry)
        except (http.HttpError, OSError, ValueError) as e:
            print(f"gt reserves: {str(e)[:100]}", flush=True)
            got = None                                   # not cached: retried on the next snapshot
        if got is not None:
            with _res_lock:
                for pool in need[:30 * max_calls]:
                    _res[pool] = (now, got.get(pool, 0.0))
    with _res_lock:
        for s in zero.values():
            r = _res.get(s["pair"], (0, 0.0))[1]
            if r >= DUST_LIQ:
                s["liq"], s["liq_src"] = r, "gt"


def search(term: str) -> list[dict]:
    """Every Solana pair DexScreener finds for `term` (it caps a search at 30 pairs)."""
    from urllib.parse import quote
    pairs = http.get_json(f"{DS}/latest/dex/search?q={quote(term)}", timeout=20).get("pairs") or []
    return [p for p in pairs if p.get("chainId") == "solana"]


def snap(p: dict) -> dict:
    """The fields of a pair the scorer marks with."""
    vol, tx, ch = p.get("volume") or {}, p.get("txns") or {}, p.get("priceChange") or {}
    return {"px": float(p.get("priceUsd") or 0), "mc": p.get("marketCap") or p.get("fdv") or 0,
            "liq": (p.get("liquidity") or {}).get("usd") or 0, "pair": p.get("pairAddress"), "dex": p.get("dexId"),
            "v5": vol.get("m5"), "v1h": vol.get("h1"), "v24": vol.get("h24"),
            "b1h": (tx.get("h1") or {}).get("buys"), "s1h": (tx.get("h1") or {}).get("sells"),
            "c5": ch.get("m5"), "c1h": ch.get("h1"), "c24": ch.get("h24")}
