"""Paper recorder for narrative runners. No keys, no orders.

    python -m runners.record --data-dir ~/runners/data [--stop-file ~/runners/STOP]

Every 2 min it pulls candidates (GeckoTerminal trending and top-volume pools, DexScreener profiles and
boosts). A candidate is ADMITTED the first time it is in the runner zone: market cap $150k-$100M,
liquidity >= $15k (or liquidity unreported with >= $50k traded in the last hour, the Meteora DBC
coins' PumpSwap pools), deepest pair <= 7 days old. Candidates below the zone are re-checked every 5 min
for 24 h, so a coin that is trending at $80k is admitted when it crosses $150k, not missed.

Each admitted coin gets one `enrich` row (story, look-alikes, holders, Jev, and `features`: launch, dev buy,
first-hour candles, organic flow at admission, story kind) from a worker thread, and a
`snap` row every 60 s for its first 12 h, then every 5 min up to 72 h. Rows go to
<data-dir>/runners_YYYYMMDD.jsonl (UTC day); a finished day is gzipped.

Watchdog: if no snapshot lands for 15 min while coins are tracked, or discovery has failed for 30 min,
the process exits non-zero so the supervisor restarts it. A quiet recorder is a short sample.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import queue
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from devbot import http

from . import enrich, features, model, newsfirst, rule, sources

ADMIT_MC_LO, ADMIT_MC_HI = 150_000, 100_000_000
ADMIT_LIQ = 15_000
UNKNOWN_LIQ_V1H = 50_000   # BINA traded $1.2M/h on 2026-10-21 with liquidity $0 on both DexScreener and GeckoTerminal
MAX_AGE_S = 7 * 86400
DISCOVER_S, WATCH_S, WATCH_FOR_S = 120, 300, 86400
FAST_S, SLOW_S, FAST_FOR_S, TRACK_FOR_S = 60, 300, 12 * 3600, 72 * 3600
TICK_S = 15
SNAP_TICK_S = 10
RETRAIN_S = 6 * 3600
MIN_TRAIN = 100
SNAP_QUIET_S, DISCOVER_QUIET_S = 900, 1800

ERRORS = (http.HttpError, OSError, ValueError, KeyError, TypeError)


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%H:%M:%S} {msg}", flush=True)


class Tape:
    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.day = ""
        self.fh = None

    def write(self, kind: str, **row) -> None:
        row = {"t": kind, "ts": round(time.time(), 1), **row}
        with self.lock:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            if day != self.day:
                if self.fh:
                    self.fh.close()
                    threading.Thread(target=self._gzip, args=(self.dir / f"runners_{self.day}.jsonl",), daemon=True).start()
                self.day, self.fh = day, open(self.dir / f"runners_{day}.jsonl", "a")
            self.fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            self.fh.flush()

    @staticmethod
    def _gzip(path: Path) -> None:
        if path.exists():
            with open(path, "rb") as src, gzip.open(f"{path}.gz", "wb") as dst:
                shutil.copyfileobj(src, dst)
            path.unlink()


class Recorder:
    def __init__(self, data_dir: Path) -> None:
        self.tape = Tape(data_dir)
        self.state_path = data_dir / "state.json"
        self.tracked: dict[str, dict] = {}     # mint -> {admit_ts, last}
        self.watch: dict[str, float] = {}      # mint -> first seen (below the zone)
        self.profiles: dict[str, dict] = {}    # mint -> DexScreener profile text and links
        self.last_watch = 0.0
        self.news = newsfirst.NewsWatch()
        self.model_path = data_dir / "model.json"
        self.model = None
        try:
            self.model = model.Model.load(str(self.model_path))
        except (OSError, ValueError, TypeError):
            pass
        self.jobs: queue.Queue = queue.Queue()
        self.tlock = threading.RLock()          # `tracked` is shared by the main loop and the price thread
        self.last_snap = self.last_discover = time.time()
        self._load()

    def _load(self) -> None:
        try:
            s = json.loads(self.state_path.read_text())
            self.tracked, self.watch = s.get("tracked", {}), s.get("watch", {})
            self.news.done = set(s.get("news_done", []))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        with self.tlock:
            self._save_locked()

    def _save_locked(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"tracked": self.tracked, "watch": self.watch, "news_done": sorted(self.news.done)[-20_000:]}))
        tmp.replace(self.state_path)

    # --- discovery and admission -------------------------------------------------------------
    def discover(self) -> None:
        mints: list[str] = []
        try:
            mints += sources.gecko()
        except ERRORS as e:
            log(f"gecko: {str(e)[:120]}")
        try:
            for p in sources.dex_profiles():
                self.profiles[p["mint"]] = p
                mints.append(p["mint"])
        except ERRORS as e:
            log(f"dex profiles: {str(e)[:120]}")
        if not mints:
            return
        self.last_discover = time.time()
        now = time.time()
        for m in dict.fromkeys(mints):
            if not self._v1(m):
                self.watch.setdefault(m, now)
        self.check_watch(force=True)

    def check_watch(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_watch < WATCH_S:
            return
        self.last_watch = now
        for m, first in list(self.watch.items()):
            if now - first > WATCH_FOR_S or self._v1(m):
                del self.watch[m]
        try:
            pairs = sources.best_pairs(list(self.watch))
        except ERRORS as e:
            log(f"watch prices: {str(e)[:120]}")
            return
        snaps = {m: sources.snap(p) for m, p in pairs.items()}
        sources.fill_liquidity(snaps)
        for m, p in pairs.items():
            s = snaps[m]
            age = now - (p.get("pairCreatedAt") or 0) / 1000
            liq_ok = s["liq"] >= ADMIT_LIQ or (s["liq"] < sources.DUST_LIQ and (s["v1h"] or 0) >= UNKNOWN_LIQ_V1H)
            if age > MAX_AGE_S or s["mc"] > ADMIT_MC_HI or not liq_ok:
                if age > MAX_AGE_S or s["mc"] > ADMIT_MC_HI:
                    del self.watch[m]       # never coming into the zone from above or from old age
                continue
            if s["mc"] >= ADMIT_MC_LO:
                self.admit(m, p, s)
        self._save()

    def admit(self, mint: str, p: dict, s: dict) -> None:
        b, info = p.get("baseToken") or {}, p.get("info") or {}
        prof = self.profiles.get(mint, {})
        links = list(dict.fromkeys(prof.get("links", []) + [x.get("url") for x in info.get("socials") or []]
                                   + [x.get("url") for x in info.get("websites") or []]))
        coin = {"mint": mint, "name": b.get("name", ""), "symbol": b.get("symbol", ""),
                "description": prof.get("description", ""), "links": [l for l in links if l],
                "pair_created": (p.get("pairCreatedAt") or 0) / 1000}
        now = time.time()
        with self.tlock:
            self.tracked[mint] = {**self.tracked.get(mint, {}), "admit_ts": now, "last": now, "v1": True}
        self.watch.pop(mint, None)
        self.tape.write("admit", **coin, **s, hour_utc=datetime.now(timezone.utc).hour)
        self.tape.write("snap", mint=mint, **s)
        self.jobs.put(("v1", coin, {"ts": round(now, 1), **{k: s.get(k) for k in ("mc", "liq", "v1h", "b1h", "s1h", "c5", "c1h")}}))
        log(f"admit {coin['symbol']:<10} mc ${s['mc']:,.0f} liq ${s['liq']:,.0f} {mint}")

    def _v1(self, mint: str) -> bool:
        t = self.tracked.get(mint)
        return bool(t) and t.get("v1", True)

    # --- news first --------------------------------------------------------------------------------
    def news_round(self) -> None:
        try:
            cands = self.news.poll()
        except ERRORS as e:
            log(f"news: {str(e)[:120]}")
            return
        if not cands:
            return
        try:
            pairs = sources.best_pairs([c["mint"] for c in cands])
        except ERRORS as e:
            log(f"news prices: {str(e)[:120]}")
            pairs = {}
        snaps = {m: sources.snap(p) for m, p in pairs.items()}
        sources.fill_liquidity(snaps)
        for c in cands:
            m = c["mint"]
            if m not in snaps:
                self.news.done.discard(m)            # no priced pair yet: try again next round
                continue
            p, s = pairs[m], snaps[m]
            if not s["mc"]:                          # DexScreener reports no market cap for some fresh pairs
                s = {**s, "mc": c["mc_jup"], "mc_src": "jup"}
            b = p.get("baseToken") or {}
            coin = {"mint": m, "name": b.get("name") or c["name"], "symbol": b.get("symbol") or c["symbol"],
                    "description": "", "links": [l for l in [c.get("twitter")] if l],
                    "pair_created": (p.get("pairCreatedAt") or 0) / 1000}
            now = time.time()
            with self.tlock:
                t = self.tracked.get(m)
                self.tracked[m] = {**(t or {}), "admit_ts": (t or {}).get("admit_ts", now), "last": now,
                                   "v1": (t or {}).get("v1", False), "news": True}
            self.tape.write("nadmit", **coin, **s, news=c, hour_utc=datetime.now(timezone.utc).hour)
            self.tape.write("snap", mint=m, **s)
            self.jobs.put(("news", coin, {"ts": round(now, 1), "mint": m, "news": c,
                                          **{k: s.get(k) for k in ("mc", "liq", "v1h", "b1h", "s1h", "c5", "c1h")}}))
            log(f"news admit {coin['symbol']:<10} mc ${s['mc']:,.0f} words {c['words']} "
                f"story {c['stories'][0]['title'][:70] if c['stories'] else ''!r}")

    # --- enrichment ------------------------------------------------------------------------------
    def worker(self) -> None:
        while True:
            kind, coin, admit = self.jobs.get()
            if kind == "news":
                self.news_enrich(coin, admit)
                time.sleep(2)
                continue
            try:
                st = enrich.story(coin["name"], coin["symbol"], coin["links"])
                sibs = enrich.siblings(coin["mint"], coin["name"], coin["symbol"], coin["pair_created"] or admit["ts"])
                enr = {"story": st, "siblings": sibs, "vamp": enrich.vamp_flags(coin["mint"], sibs),
                       "holders": enrich.holders(coin["mint"])}
                enr["judge"] = enrich.judge(coin, st, sibs, admit["ts"])
                heads = [h["title"] for h in st["news"] if h["ts"] <= admit["ts"]]
                enr["features"] = features.collect(coin, st["post_url"], st["post"], heads)
                miss = rule.why_not(admit, enr)
                enr["pick"], enr["why_not"], enr["jev_pick"] = not miss, miss, rule.jev_rule(admit, enr)
                self.model_score(coin, {**coin, **admit_row(admit, coin)}, enr, "price")
                self.tape.write("enrich", mint=coin["mint"], admit_ts=admit["ts"], **enr)
                if not miss:
                    log(f"PICK {coin['symbol']} {coin['name']!r} mc ${admit['mc']:,.0f} "
                        f"post={st['post'][:80]!r} news={[h['title'][:60] for h in st['news'][:2]]}")
            except Exception as e:   # noqa: BLE001 -- one bad coin must not kill the worker
                self.tape.write("enrich_err", mint=coin["mint"], err=f"{type(e).__name__}: {str(e)[:200]}")
            time.sleep(2)            # DexScreener search and GeckoTerminal info are rate-limited, slow down

    def model_score(self, coin: dict, admit: dict, enr: dict, path: str) -> None:
        """Adds the learned score to the enrichment row (before it is written) and logs a MODEL PICK."""
        m = self.model
        if m is None:
            return
        try:
            sc = m.score(model.vector(admit, enr, path))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return
        enr["model"] = {"score": round(sc, 4), "thr": round(m.thr, 4), "trained": m.meta.get("trained"), "n": m.meta.get("n")}
        if sc >= m.thr:
            log(f"MODEL PICK ({path}) {coin['symbol']} {coin['name']!r} mc ${admit.get('mc', 0):,.0f} score {sc:.2f} >= {m.thr:.2f}")

    def retrain_loop(self, data_dir: Path) -> None:
        while True:
            time.sleep(RETRAIN_S if self.model else 600)
            try:
                rows = [r for r in model.samples(str(data_dir / "runners_*.jsonl*")) if r["done"]]
                if len(rows) < MIN_TRAIN:
                    log(f"model: {len(rows)} closed samples, need {MIN_TRAIN}")
                    continue
                m = model.fit(rows)
                m.dump(str(self.model_path))
                self.model = m
                log(f"model: retrained on {len(rows)} ({m.meta['pos']:.0f} profitable), threshold {m.thr:.3f}, top "
                    + ", ".join(f"{n} {w:+.2f}" for n, w in m.top(6)))
            except Exception as e:   # noqa: BLE001 -- a failed retrain keeps the last model
                log(f"model retrain: {type(e).__name__}: {str(e)[:120]}")

    def news_enrich(self, coin: dict, admit: dict) -> None:
        c = admit["news"]
        try:
            st = enrich.story(coin["name"], coin["symbol"], coin["links"])
            heads = [h["title"] for h in st["news"] if h["ts"] <= admit["ts"]] + [s["title"] for s in c["stories"]]
            enr = {"story": st, "news_judge": newsfirst.judge({**c, "name": coin["name"], "symbol": coin["symbol"]}),
                   "news_ranks": newsfirst.news_ranks(c, admit["mc"]),
                   "news_ranks_live": newsfirst.live_ranks(c, coin["mint"])}
            # Features cost GeckoTerminal and RPC calls; a coin Jev does not tie to its story cannot be a pick,
            # and collecting for every word match (app, once, jet, dog) 429'd discovery on 2026-10-22.
            if (enr["news_judge"].get("named_after_story") or 0) >= rule.NEWS_NAMED:
                enr["features"] = features.collect(coin, st["post_url"], st["post"], heads)
            miss = rule.news_why_not(admit, enr)
            enr["pick"], enr["why_not"] = not miss, miss
            enr["burst_pick"] = not rule.news_why_not(admit, enr, source="burst")
            enr["why_not_v2"] = rule.news_why_not(admit, enr, ranks="news_ranks_live")
            enr["burst_pick_v2"] = not rule.news_why_not(admit, enr, source="burst", ranks="news_ranks_live")
            self.model_score(coin, {**coin, **admit_row(admit, coin)}, enr, "news")
            self.tape.write("nenrich", mint=coin["mint"], admit_ts=admit["ts"], **enr)
            tags = [k for k, v in (("news", not miss), ("burst", enr["burst_pick"]),
                                   ("news_v2", not enr["why_not_v2"]), ("burst_v2", enr["burst_pick_v2"])) if v]
            if tags:
                log(f"NEWS PICK ({'/'.join(tags)}) {coin['symbol']} {coin['name']!r} mc ${admit['mc']:,.0f} "
                    f"story {c['stories'][0]['title'][:80] if c['stories'] else ''!r} jev {enr['news_judge']}")
        except Exception as e:   # noqa: BLE001 -- one bad coin must not kill the worker
            self.tape.write("enrich_err", mint=coin["mint"], err=f"news {type(e).__name__}: {str(e)[:200]}")

    # --- snapshots -------------------------------------------------------------------------------
    def snapshot(self) -> None:
        now = time.time()
        due = []
        with self.tlock:
            for m, t in list(self.tracked.items()):
                age = now - t["admit_ts"]
                if age > TRACK_FOR_S:
                    del self.tracked[m]
                    continue
                if now - t["last"] >= (FAST_S if age < FAST_FOR_S else SLOW_S):
                    due.append(m)
        if not due:
            if not self.tracked:
                self.last_snap = now
            return
        try:
            pairs = sources.best_pairs(due)
        except ERRORS as e:
            log(f"snap prices: {str(e)[:120]}")
            return
        snaps = {m: sources.snap(p) for m, p in pairs.items()}
        sources.fill_liquidity(snaps, retry=False)     # the price thread never sleeps on a 429
        for m in due:
            with self.tlock:
                if m in self.tracked:
                    self.tracked[m]["last"] = now
            if m in snaps:
                self.tape.write("snap", mint=m, **snaps[m])
            else:
                self.tape.write("gone", mint=m)   # no pair returned: delisted or rugged to nothing
        self.last_snap = now

    def snap_loop(self) -> None:
        """Prices on their own thread: discovery, news and enrichment wait on rate-limited APIs, and on
        one shared loop they stretched the 60 s snapshots to 4-14 min (2026-10-22)."""
        while True:
            t0 = time.time()
            try:
                self.snapshot()
            except Exception as e:   # noqa: BLE001 -- the price thread must not die
                log(f"snapshot: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(max(1.0, SNAP_TICK_S - (time.time() - t0)))

    def run(self, stop_file: Path | None) -> None:
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.snap_loop, daemon=True).start()
        threading.Thread(target=self.retrain_loop, args=(self.tape.dir,), daemon=True).start()
        for coin_mint in list(self.tracked):
            log(f"resumed tracking {coin_mint}")
        next_discover = 0.0
        while not (stop_file and stop_file.exists()):
            now = time.time()
            if now >= next_discover:
                self.discover()
                next_discover = now + DISCOVER_S
                self._save()
            else:
                self.check_watch()
            self.news_round()
            if self.tracked and now - self.last_snap > SNAP_QUIET_S:
                log("watchdog: no snapshot for 15 min, exiting")
                os._exit(3)
            if now - self.last_discover > DISCOVER_QUIET_S:
                log("watchdog: discovery failing for 30 min, exiting")
                os._exit(4)
            time.sleep(TICK_S)
        self._save()
        log("stop file seen, exiting")


def admit_row(admit: dict, coin: dict) -> dict:
    """The fields of the admit row `model.vector` reads, from the job's admit summary."""
    return {"ts": admit["ts"], "mc": admit.get("mc"), "liq": admit.get("liq"), "v1h": admit.get("v1h"),
            "b1h": admit.get("b1h"), "s1h": admit.get("s1h"), "c5": admit.get("c5"), "c1h": admit.get("c1h"),
            "pair_created": coin.get("pair_created"), "mint": coin["mint"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--stop-file", type=Path)
    a = ap.parse_args()
    Recorder(a.data_dir.expanduser()).run(a.stop_file.expanduser() if a.stop_file else None)


if __name__ == "__main__":
    main()
