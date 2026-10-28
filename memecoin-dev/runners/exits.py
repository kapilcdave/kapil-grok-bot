"""Price-driven exit: take profit on the way up, trail the rest, never wait for a clock.

Operator, 2026-10-22: "if the bot sees it at X and it peaks at Y, take profits aggressively on the way
up and let the rest run; we don't know when it crashes, only that it will."

Ladder, run on the 60 s snapshots (a fill is the first snapshot at or past a trigger, never the peak):
    - before the first take-profit, stop out everything at -40% (trailing already once up 50%)
    - sell 1/3 at 2x and 1/3 at 4x of entry
    - the rest (and everything after 2x) rides a trailing stop off the running peak whose width follows
      the coin's own volatility: 4 x the stdev of its last 30 one-minute log returns, held within
      20%-50%; after the first take-profit the stop is never below entry
    - 72 h (end of tracking) closes what is left; a delisted pair loses what is left
Each sell pays the scorer's leg cost (fee + impact of that slice against half the pool).

`Ladder` is a state machine fed one snapshot at a time, so the recorder can carry thousands of open
coins in a few floats each.
"""
from __future__ import annotations

import math
from collections import deque

STOP_LOSS = 0.60
TAKE = ((2.0, 1 / 3), (4.0, 1 / 3))
TRAIL_K, TRAIL_LO, TRAIL_HI, VOL_N = 4.0, 0.20, 0.50, 30
MAX_HOLD_S = 72 * 3600
UNKNOWN_LIQ_IMPACT = 0.05


def leg_cost(liq: float, usd: float, fee: float) -> float:
    return fee + (usd / (liq / 2) if liq >= 100 else UNKNOWN_LIQ_IMPACT)


class Ladder:
    def __init__(self, entry_px: float, entry_ts: float, entry_liq: float, clip: float = 100.0, fee: float = 0.01):
        self.e, self.t0, self.clip, self.fee = entry_px, entry_ts, clip, fee
        self.left = 1.0                      # share of the position still held
        self.cash = 0.0                      # USD back so far, per $clip invested
        self.peak = entry_px
        self.took = 0                        # take-profit levels already hit
        self.rets: deque = deque(maxlen=VOL_N)
        self.last = entry_px
        self.done = False
        self.closed_ts = None
        self.reason = ""
        self.max_mult = 1.0
        bought = clip * (1 - min(leg_cost(entry_liq, clip, fee), 1))
        self.units = bought / entry_px       # tokens bought with the clip after the entry cost

    def _sell(self, share: float, px: float, liq: float) -> None:
        usd = self.units * share * px
        self.cash += usd * (1 - min(leg_cost(liq, usd, self.fee), 1))
        self.left -= share

    def trail(self) -> float:
        if len(self.rets) < 5:
            return TRAIL_HI
        m = sum(self.rets) / len(self.rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in self.rets) / (len(self.rets) - 1))
        return min(TRAIL_HI, max(TRAIL_LO, TRAIL_K * sd))

    def step(self, ts: float, px: float, liq: float) -> None:
        if self.done or not px:
            return
        if self.last > 0:
            self.rets.append(math.log(px / self.last))
        self.last = px
        self.peak = max(self.peak, px)
        self.max_mult = max(self.max_mult, px / self.e)
        while self.took < len(TAKE) and px >= TAKE[self.took][0] * self.e:
            self._sell(min(TAKE[self.took][1], self.left), px, liq)
            self.took += 1
        floor = self.peak * (1 - self.trail())
        if self.took:
            stop = max(floor, self.e)                       # after a take-profit the rest never turns a loss
        elif self.peak >= 1.5 * self.e:
            stop = max(floor, STOP_LOSS * self.e)           # running but short of 2x: trail already
        else:
            stop = STOP_LOSS * self.e
        if px <= stop:
            self._sell(self.left, px, liq)
            self.done, self.reason, self.closed_ts = True, "stop" if not self.took else "trail", ts
        elif ts - self.t0 >= MAX_HOLD_S:
            self._sell(self.left, px, liq)
            self.done, self.reason, self.closed_ts = True, "time", ts

    def gone(self, ts: float | None = None) -> None:
        if not self.done:
            self.left, self.done, self.reason, self.closed_ts = 0.0, True, "gone", ts

    def net(self) -> float:
        """Return on the clip; only final once `done`."""
        return self.cash / self.clip - 1


def run(snaps: list[dict], gone_ts: list[float], clip: float = 100.0, fee: float = 0.01) -> Ladder | None:
    """Replay a coin's snapshots (entry = snaps[0])."""
    if not snaps:
        return None
    lad = Ladder(snaps[0]["px"], snaps[0]["ts"], snaps[0].get("liq") or 0, clip, fee)
    g = min((t for t in gone_ts if t > snaps[0]["ts"]), default=None)
    for s in snaps[1:]:
        if g is not None and s["ts"] >= g:
            break
        lad.step(s["ts"], s["px"], s.get("liq") or 0)
        if lad.done:
            return lad
    if g is not None:
        lad.gone(g)
    return lad
