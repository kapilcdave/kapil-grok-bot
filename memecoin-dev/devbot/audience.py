"""Who bought our coins: launch-block snipers, bots, fresh wallets, or humans.

research/RESULT-human-attention-levers-20261013.md: a low-activity wallet is not a human (holder
farms spray ~1,000 single-use wallets), humans are wallets that come back on other days, and they
arrive minutes after launch. Here each buyer gets one getSignaturesForAddress call:
    bot    100+ transactions inside BOT_SPAN_S
    fresh  nothing older than a day before our launch (single-use sybils, or a brand-new user)
    human  history older than a day, at a human rate
"""
from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field

from . import http, trends
from .pump import MAYHEM_AGENT

BOT_TXS = 100
BOT_SPAN_S = 6 * 3600
FRESH_S = 86400
LATE_S = 60


def classify(rpc, wallet: str, launch_ts: float) -> str:
    for attempt in range(2):
        try:
            sigs = rpc.call("getSignaturesForAddress", [wallet, {"limit": BOT_TXS}]) or []
            break
        except (RuntimeError, http.HttpError, OSError):  # public RPC: transient storage errors
            if attempt:
                return "unknown"
            time.sleep(1)
    times = [s["blockTime"] for s in sigs if s.get("blockTime")]
    if not times:
        return "fresh"
    if len(sigs) >= BOT_TXS and max(times) - min(times) < BOT_SPAN_S:
        return "bot"
    if min(times) > launch_ts - FRESH_S:
        return "fresh"
    return "human"


@dataclass
class Audience:
    counts: collections.Counter = field(default_factory=collections.Counter)
    first_human_s: float | None = None
    human_sol: float = 0.0
    late_buys: int = 0      # buys more than LATE_S after launch: anyone still interested once the bots left
    agent_trades: int = 0   # pump.fun's Mayhem agent; never counted as a buyer

    def line(self) -> str:
        c = self.counts
        first = f", first human at {self.first_human_s:.0f}s" if self.first_human_s is not None else ""
        unknown = f", unknown {c['unknown']}" if c["unknown"] else ""
        agent = f", Mayhem agent {self.agent_trades} trades" if self.agent_trades else ""
        return (f"launch-block {c['launch-block']}, bots {c['bot']}, fresh wallets {c['fresh']}{unknown}, "
                f"HUMANS {c['human']} ({self.human_sol:.2f} SOL){first}, {self.late_buys} buys after {LATE_S}s{agent}")


def all_trades(mint: str, max_pages: int = 25) -> list[dict]:
    """Newest first, 100 per page; a busy coin's early buyers are pages back."""
    out, cursor = [], "0"
    for _ in range(max_pages):
        page = http.get_json(trends.TRADES.format(mint).replace("cursor=0", f"cursor={cursor}"),
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        out += page.get("trades") or []
        cursor = (page.get("pagination") or {}).get("nextCursor")
        if not (page.get("pagination") or {}).get("hasMore") or not cursor:
            break
    return out


def audience(rpc, mint: str, creator: str, launch_ts: float, cache: dict[str, str]) -> Audience:
    everything = all_trades(mint)
    trades = [t for t in everything if t.get("userAddress") != MAYHEM_AGENT]
    first: dict[str, float] = {}
    sol: dict[str, float] = collections.defaultdict(float)
    for t in sorted(trades, key=lambda t: trends._ts(t.get("timestamp"))):
        user = t.get("userAddress")
        if t.get("type") != "buy" or not user or user == creator:
            continue
        first.setdefault(user, trends._ts(t.get("timestamp")) - launch_ts)
        sol[user] += float(t.get("amountSol") or 0)
    out = Audience(late_buys=sum(1 for t in trades if t.get("type") == "buy" and t.get("userAddress") != creator
                                 and trends._ts(t.get("timestamp")) - launch_ts > LATE_S),
                   agent_trades=len(everything) - len(trades))
    for user, delay in sorted(first.items(), key=lambda kv: kv[1]):
        if delay <= 1:
            kind = "launch-block"
        else:
            if user not in cache:
                cache[user] = classify(rpc, user, launch_ts)
                time.sleep(0.15)  # public RPC: stay under its per-method rate limit
            kind = cache[user]
        out.counts[kind] += 1
        if kind == "human":
            out.human_sol += sol[user]
            if out.first_human_s is None:
                out.first_human_s = delay
    return out
