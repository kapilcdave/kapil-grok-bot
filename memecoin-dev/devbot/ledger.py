"""Durable state in one SQLite file: launches, spend, claims, and the bot's switches.

Daily caps and the stop switch live here, not in process memory or a STOP file,
so a restart neither resets today's spend nor silently re-arms a stopped bot.
The balance floor's baseline moves on an explicit /rebase, and otherwise only UP (after a fee
claim raises the balance), never down: following the balance down would disable the floor.
"""
from __future__ import annotations

import calendar
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

CLONE_SOURCE = "clone"  # source_symbol of a launch that copies another coin (/clone, /pvp)

SCHEMA = """
CREATE TABLE IF NOT EXISTS launches (
    mint TEXT PRIMARY KEY, ts REAL, name TEXT, symbol TEXT, narrative TEXT,
    source_mint TEXT, source_name TEXT, source_symbol TEXT,
    uri TEXT, signature TEXT, confirmed INTEGER, dev_buy_sol REAL);
CREATE TABLE IF NOT EXISTS spend (ts REAL, day TEXT, sol REAL, what TEXT);
CREATE TABLE IF NOT EXISTS claims (ts REAL, signature TEXT, sol_before REAL, sol_after REAL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS pins (mint TEXT, cid TEXT, PRIMARY KEY (mint, cid));
CREATE TABLE IF NOT EXISTS cleaned (mint TEXT PRIMARY KEY, ts REAL, files INTEGER);
CREATE TABLE IF NOT EXISTS events (ts REAL, day TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS pad_mints (pad TEXT, mint TEXT, PRIMARY KEY (pad, mint));
CREATE TABLE IF NOT EXISTS sales (ts REAL, mint TEXT, pct INTEGER, signature TEXT, sol REAL);
"""


def utc_day(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts if ts is not None else time.time()))


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str


def check_launch(*, stopped: bool, live: bool, launches_today: int, max_launches: int,
                 spent_today: float, max_sol_per_day: float, cost: float,
                 balance: float, baseline: float | None, floor_pct: float) -> Verdict:
    if stopped:
        return Verdict(False, "bot is stopped (/resume to re-enable)")
    if not live:
        return Verdict(False, "dry run (LIVE=0)")
    if max_launches and launches_today >= max_launches:  # 0 = no count cap; the SOL cap still binds
        return Verdict(False, f"daily launch cap reached ({launches_today}/{max_launches})")
    if spent_today + cost > max_sol_per_day + 1e-12:
        return Verdict(False, f"daily SOL cap: spent {spent_today:.4f} + {cost:.4f} > {max_sol_per_day:.4f}")
    if baseline is None:
        return Verdict(False, "no balance baseline recorded")
    floor = baseline * floor_pct / 100
    if balance - cost < floor:
        return Verdict(False, f"balance floor: {balance:.4f} - {cost:.4f} < {floor:.4f}"
                              f" ({floor_pct:g}% of {baseline:.4f})")
    return Verdict(True, "ok")


class Ledger:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def put(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, value))
        self.db.commit()

    @property
    def stopped(self) -> bool:
        return self.get("stopped", "0") == "1"

    @property
    def auto(self) -> bool:
        return self.get("auto", "0") == "1"

    @property
    def baseline(self) -> float | None:
        v = self.get("baseline_sol")
        return float(v) if v is not None else None

    def raise_baseline(self, balance: float) -> bool:
        """Ratchet: move the floor's baseline up to `balance` if higher. Never lowers it."""
        if self.baseline is not None and balance <= self.baseline:
            return False
        self.put("baseline_sol", str(balance))
        return True

    def spent_today(self, what_prefix: str = "") -> float:
        """Today's spend; with `what_prefix`, only rows whose label starts with it (e.g. "auto ")."""
        return self.db.execute("SELECT COALESCE(SUM(sol),0) FROM spend WHERE day=? AND what LIKE ?",
                               (utc_day(), what_prefix.replace("%", "") + "%")).fetchone()[0]

    def launches_today(self) -> int:
        start = calendar.timegm(time.strptime(utc_day(), "%Y-%m-%d"))
        return self.db.execute("SELECT COUNT(*) FROM launches WHERE ts>=?", (start,)).fetchone()[0]

    def record_spend(self, sol: float, what: str) -> None:
        now = time.time()
        self.db.execute("INSERT INTO spend VALUES (?,?,?,?)", (now, utc_day(now), sol, what))
        self.db.commit()

    def record_launch(self, *, mint: str, name: str, symbol: str, narrative: str, source_mint: str,
                      source_name: str, source_symbol: str, uri: str, signature: str, confirmed: bool,
                      dev_buy_sol: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO launches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mint, time.time(), name, symbol, narrative, source_mint, source_name, source_symbol, uri,
             signature, int(confirmed), dev_buy_sol))
        self.db.commit()

    def record_pad_mint(self, pad: str, mint: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO pad_mints VALUES (?,?)", (pad, mint))
        self.db.commit()

    def pad_mints(self, pad: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT mint FROM pad_mints WHERE pad=?", (pad,))]

    def record_claim(self, signature: str, before: float, after: float) -> None:
        self.db.execute("INSERT INTO claims VALUES (?,?,?,?)", (time.time(), signature, before, after))
        self.db.commit()

    def record_sale(self, mint: str, pct: int, signature: str, sol: float) -> None:
        """A sell of the dev wallet's own tokens; `sol` is what the wallet received, net of the tx fee."""
        self.db.execute("INSERT INTO sales VALUES (?,?,?,?,?)", (time.time(), mint, pct, signature, sol))
        self.db.commit()

    def launched(self, ref: str) -> tuple[str, str] | None:
        """(mint, symbol) of our launch whose mint or ticker is `ref` (newest first)."""
        row = self.db.execute("SELECT mint, symbol FROM launches WHERE mint=? OR UPPER(symbol)=UPPER(?) "
                              "ORDER BY ts DESC LIMIT 1", (ref, ref.lstrip("$"))).fetchone()
        return (row[0], row[1]) if row else None

    def count_event(self, kind: str, n: int = 1) -> None:
        now = time.time()
        self.db.executemany("INSERT INTO events VALUES (?,?,?)", [(now, utc_day(now), kind)] * n)
        self.db.commit()

    def count_today(self, kind: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events WHERE day=? AND kind=?", (utc_day(), kind)).fetchone()[0]

    def record_pins(self, mint: str, cids: list[str]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO pins VALUES (?,?)", [(mint, c) for c in cids])
        self.db.commit()

    def pins(self, mint: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT cid FROM pins WHERE mint=?", (mint,))]

    def uncleaned(self, older_than_s: float) -> list[tuple[str, str, str]]:
        """(mint, symbol, metadata uri) of launches whose Pinata files may still be stored."""
        return self.db.execute(
            "SELECT l.mint, l.symbol, l.uri FROM launches l LEFT JOIN cleaned c ON c.mint = l.mint "
            "WHERE c.mint IS NULL AND l.ts < ? ORDER BY l.ts", (time.time() - older_than_s,)).fetchall()

    def mark_cleaned(self, mint: str, files: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO cleaned VALUES (?,?,?)", (mint, time.time(), files))
        self.db.commit()

    def launches(self, limit: int = 30) -> list[tuple]:
        return self.db.execute(
            "SELECT mint, ts, name, symbol, source_symbol, confirmed FROM launches ORDER BY ts DESC LIMIT ?",
            (limit,)).fetchall()

    def cloned(self, source_mint: str) -> bool:
        return self.db.execute("SELECT 1 FROM launches WHERE source_mint=? AND source_symbol=?",
                               (source_mint, CLONE_SOURCE)).fetchone() is not None

    def used_names(self) -> list[str]:
        out: list[str] = []
        for name, symbol in self.db.execute("SELECT name, symbol FROM launches"):
            out += [name, symbol]
        return out

    def totals(self) -> dict[str, float]:
        spent = self.db.execute("SELECT COALESCE(SUM(sol),0) FROM spend").fetchone()[0]
        claimed = self.db.execute("SELECT COALESCE(SUM(sol_after - sol_before),0) FROM claims").fetchone()[0]
        sold = self.db.execute("SELECT COALESCE(SUM(sol),0) FROM sales").fetchone()[0]
        n = self.db.execute("SELECT COUNT(*) FROM launches").fetchone()[0]
        return {"launches": n, "spent_sol": spent, "claimed_sol": claimed, "sold_sol": sold}
