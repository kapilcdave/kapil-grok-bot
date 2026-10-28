"""DexScreener lookups: is a ticker already taken on Solana, and how are our own coins trading.

(Trend discovery lives in trends.py and reads pump.fun directly.)
"""
from __future__ import annotations

from urllib.parse import quote

from . import http

API = "https://api.dexscreener.com"


def _pairs(mints: list[str]) -> list[dict]:
    pairs: list[dict] = []
    for i in range(0, len(mints), 30):  # /tokens/v1 takes up to 30 addresses
        try:
            pairs += http.get_json(f"{API}/tokens/v1/solana/{','.join(mints[i:i + 30])}")
        except (http.HttpError, OSError):
            continue
    return pairs


def symbol_taken(symbol: str, name: str) -> bool:
    """True if a Solana token already trades under this exact ticker or name."""
    try:
        hits = http.get_json(f"{API}/latest/dex/search?q={quote(symbol, safe='')}").get("pairs") or []
    except (http.HttpError, OSError):
        return True  # cannot check -> treat as taken rather than risk a lookalike
    s, n = symbol.strip().lower(), name.strip().lower()
    return any(
        p.get("chainId") == "solana"
        and ((p.get("baseToken") or {}).get("symbol", "").strip().lower() == s
             or (p.get("baseToken") or {}).get("name", "").strip().lower() == n)
        for p in hits
    )


def token_stats(mints: list[str]) -> dict[str, dict]:
    """mint -> {vol_h24, market_cap, url} for our own launches."""
    stats: dict[str, dict] = {}
    for p in _pairs(mints):
        mint = (p.get("baseToken") or {}).get("address")
        if not mint:
            continue
        vol = float((p.get("volume") or {}).get("h24") or 0)
        if mint not in stats or vol > stats[mint]["vol_h24"]:
            stats[mint] = {"vol_h24": vol, "market_cap": float(p.get("marketCap") or 0), "url": p.get("url", "")}
    return stats
