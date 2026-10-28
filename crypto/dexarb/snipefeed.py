"""pump.fun collector: one `logsSubscribe`, a bounded watch set, a replayable tape.

Why a tape and not a decision: every arm in
`preregistrations/pumpfun_gliner_decide_paper_20261005.md` must be scored on the
SAME rows, including a turnover-matched null that does not exist until after the
run. A collector that decides as it goes cannot serve the null, so this process
decides nothing. It writes typed rows; `snipepaper.py` consumes them, live or
offline, with identical results.

Feed choice, measured 2026-10-05: PumpPortal's `subscribeTokenTrade` now answers
"only available when connecting with an API key funded with at least 0.02 SOL",
so its free tier gives creates without trades -- useless for a curve mark.
Processed `logsSubscribe` on the pump.fun program is free and carries both, with
the token's text on `CreateEvent` (see `snipedecode`).

Row types, one JSON object per line:

    {"t":"create","ts":..,"recv":..,"slot":..,"mint":..,"name":..,"uri":..,..}
    {"t":"trade","ts":..,"recv":..,"slot":..,"mint":..,"side":..,"v_sol":..,..}
    {"t":"meta","ts":..,"mint":..,"ok":true,"description":"..","fetch_ms":..}
    {"t":"stats","ts":..,"counts":{..}}

    python3 snipefeed.py --probe --duration 60        # nothing written
    python3 snipefeed.py --tape /var/lib/pumpsnipe/tape.jsonl --duration 86400
"""
import argparse
import asyncio
import collections
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

from snipedecode import PUMP_FUN, decode_logs

try:
    import websockets
except ImportError:                                        # pragma: no cover
    websockets = None

DEFAULT_WS = "wss://api.mainnet-beta.solana.com"
SCHEMA = "snipefeed-2"
# Order matters and is measured, not guessed (2026-10-05, same CID):
# pump.mypinata.cloud 235 ms OK, ipfs.io HTTP 429, cloudflare-ipfs.com NXDOMAIN
# (that gateway was retired, so it is not in the list at all).
IPFS_GATEWAYS = ("https://pump.mypinata.cloud/ipfs/",
                 "https://ipfs.io/ipfs/")


def normalise_uri(uri):
    """An ipfs:// or gateway URI -> an https URL we can GET, or None."""
    if not uri:
        return None
    if uri.startswith("ipfs://"):
        return IPFS_GATEWAYS[0] + uri[len("ipfs://"):]
    if uri.startswith("https://") or uri.startswith("http://"):
        return uri
    return None


CID_RE = re.compile(r"/ipfs/([A-Za-z0-9]+)")


def gateway_candidates(uri):
    """The URLs to try for one token URI, in order.

    ipfs.io rate-limits hard (28 of 28 HTTPError in a 70 s capture on
    2026-10-05), and a one-gateway fetch therefore measures the gateway's
    patience rather than the token's text. Any `/ipfs/<cid>` URI is retried on
    the remaining gateways; a non-IPFS host has no alternate and is tried once.
    """
    url = normalise_uri(uri)
    if not url:
        return []
    match = CID_RE.search(url)
    if not match:
        return [url]
    cid = match.group(1)
    seen, out = set(), []
    for candidate in [url] + [g + cid for g in IPFS_GATEWAYS]:
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def fetch_metadata(uri, timeout):
    """Blocking GET of the token's JSON metadata. Returns (dict|None, ms, err).

    Run on a thread, never on the event loop: a slow gateway must not stall the
    socket. Failures are recorded, not hidden -- the text channel's own
    availability is part of what this arm measures.
    """
    urls = gateway_candidates(uri)
    if not urls:
        return None, 0.0, "unsupported_uri"
    t0 = time.monotonic()
    last = "no_attempt"
    for url in urls:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "pumpsnipe/1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(65536)
            return json.loads(body.decode("utf-8", "replace")), \
                (time.monotonic() - t0) * 1e3, None
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, ValueError) as exc:
            last = type(exc).__name__
            if isinstance(exc, urllib.error.HTTPError):
                last = f"HTTP{exc.code}"
    return None, (time.monotonic() - t0) * 1e3, last


class MetaPool:
    """A bounded pool of DAEMON fetch workers.

    Not `loop.run_in_executor`: `ThreadPoolExecutor`'s workers are non-daemon and
    an atexit hook joins them, so one wedged gateway socket keeps the whole
    process alive after its `--duration` has expired. That is exactly what
    happened on 2026-10-05 -- the census feed outlived a 420 s budget and had to
    be killed by hand, which on a 24 h box is a leak that survives every restart.
    Daemon threads cannot pin the interpreter open, and a bounded queue means a
    slow gateway drops fetches (counted) instead of growing without limit.
    """

    def __init__(self, workers, timeout, on_done, maxsize=512):
        self.q = queue.Queue(maxsize=maxsize)
        self.timeout = timeout
        self.on_done = on_done
        self.dropped = 0
        self.stop = threading.Event()
        self.threads = [
            threading.Thread(target=self._work, name=f"meta{i}", daemon=True)
            for i in range(workers)]
        for t in self.threads:
            t.start()

    def submit(self, mint, uri):
        try:
            self.q.put_nowait((mint, uri))
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _work(self):
        while not self.stop.is_set():
            try:
                mint, uri = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                result = fetch_metadata(uri, self.timeout)
            except Exception as exc:                        # noqa: BLE001
                result = (None, 0.0, "raise_" + type(exc).__name__)
            try:
                self.on_done(mint, *result)
            except Exception:                               # noqa: BLE001
                pass
            finally:
                self.q.task_done()

    def shutdown(self):
        self.stop.set()


class Tape:
    def __init__(self, path):
        self.path = path
        self.handle = open(path, "a", buffering=1) if path else None
        self.counts = collections.Counter()

    def write(self, row):
        self.counts[row["t"]] += 1
        if self.handle is not None:
            self.handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    def close(self):
        if self.handle is not None:
            self.handle.close()


async def run(tape, args, url, connect=None):
    """One subscription, reconnecting with exponential backoff.

    Backoff is mandatory, not polite: a bare retry loop earned an HTTP 429 and
    took a feed down for 24 h (`a-restart-supervisor-needs-backoff`). Every
    reconnect is named and counted so a lag sawtooth is attributable to the
    valve rather than to the venue (`a-feed-lag-sawtooth-means-the-reconnect`).
    """
    if connect is None:
        def connect():
            return websockets.connect(url, max_queue=8192, ping_interval=20,
                                      ping_timeout=20, max_size=4_000_000)
    # WALL clock: a monotonic budget spends AWAKE seconds and silently
    # overruns a sleeping box (`monotonic-duration-budgets-awake-seconds`).
    deadline = time.time() + args.duration
    stats = collections.Counter()
    watched = collections.OrderedDict()           # mint -> create ts, bounded
    pending_meta = set()
    backoff = 1.0
    next_stats = time.time() + args.stats_every
    meta_rows = queue.Queue()        # workers hand finished rows back here

    def on_meta(mint, meta, ms, err):
        meta_rows.put((mint, meta, ms, err))

    pool = MetaPool(args.meta_workers, args.meta_timeout, on_meta) \
        if args.fetch_meta else None

    while time.time() < deadline:
        try:
            async with connect() as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                    "params": [{"mentions": [PUMP_FUN]},
                               {"commitment": "processed"}]}))
                backoff = 1.0
                stats["connects"] += 1
                while time.time() < deadline:
                    timeout = min(30.0, max(0.1, deadline - time.time()))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        stats["idle_timeout"] += 1
                        continue
                    recv = time.time()
                    msg = json.loads(raw)
                    if "result" in msg:
                        stats["ack"] += 1
                        continue
                    value = (msg.get("params") or {}).get("result") or {}
                    ctx = value.get("context") or {}
                    value = value.get("value") or {}
                    slot = ctx.get("slot")
                    stats["notif"] += 1
                    if value.get("err"):
                        stats["failed_tx"] += 1
                        continue        # a failed tx never moved the curve
                    creates, trades, counts = decode_logs(value.get("logs"))
                    for key, n in counts.items():
                        if n:
                            stats["log_" + key] += n
                    sig = value.get("signature")
                    for row in creates:
                        stats["create"] += 1
                        tape.write({"t": "create", "ts": row["ts"], "recv": recv,
                                    "slot": slot, "sig": sig, "schema": SCHEMA,
                                    **{k: row[k] for k in
                                       ("mint", "name", "symbol", "uri",
                                        "creator", "user", "curve",
                                        "v_sol", "v_tok")}})
                        watched[row["mint"]] = recv
                        while len(watched) > args.max_watch:
                            watched.popitem(last=False)
                            stats["watch_evicted"] += 1
                        if pool is not None and row["mint"] not in pending_meta:
                            pending_meta.add(row["mint"])
                            if not pool.submit(row["mint"], row["uri"]):
                                pending_meta.discard(row["mint"])
                                stats["meta_queue_full"] += 1
                    drain_meta(meta_rows, tape, stats, pending_meta)
                    for row in trades:
                        if row["mint"] not in watched:
                            stats["trade_unwatched"] += 1
                            continue
                        if args.watch_s and recv - watched[row["mint"]] > args.watch_s:
                            stats["trade_past_window"] += 1
                            continue
                        stats["trade"] += 1
                        tape.write({"t": "trade", "ts": row["ts"], "recv": recv,
                                    "slot": slot, "sig": sig,
                                    **{k: row[k] for k in
                                       ("mint", "user", "side", "sol", "tok",
                                        "v_sol", "v_tok")}})
                    drain_meta(meta_rows, tape, stats, pending_meta)
                    # Periodic counters ON THE TAPE. Without these a 24 h run is
                    # blind until it ends, and a feed that quietly halves its
                    # rate is indistinguishable from a quiet venue -- the exact
                    # ambiguity that cost a wrong diagnosis here on 2026-10-05.
                    if args.stats_every and recv >= next_stats:
                        next_stats = recv + args.stats_every
                        tape.write({"t": "stats", "ts": recv,
                                    "watching": len(watched),
                                    "meta_inflight": len(pending_meta),
                                    "counts": dict(stats)})
                    if args.verbose and stats["notif"] % 2000 == 0:
                        print(f"[snipefeed] {dict(tape.counts)}",
                              file=sys.stderr, flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                            # noqa: BLE001
            # Named and printed. A counter inside a bare except names a count,
            # not a failure (`a-failure-counter-in-a-bare-except`).
            stats["reconnect_" + type(exc).__name__] += 1
            print(f"[snipefeed] reconnect after {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            left = deadline - time.time()
            if left <= 0:
                break
            await asyncio.sleep(min(backoff, left))
            backoff = min(backoff * 2, 60.0)
    if pool is not None:
        pool.shutdown()
        stats["meta_dropped_queue_full"] += pool.dropped
    drain_meta(meta_rows, tape, stats, pending_meta)
    stats["meta_inflight_at_exit"] = len(pending_meta)
    tape.write({"t": "stats", "ts": time.time(), "counts": dict(stats)})
    return stats


def drain_meta(meta_rows, tape, stats, pending):
    """Write whatever the fetch workers have finished. Runs on the event loop,
    so the tape stays single-writer and the rows stay in one file order."""
    while True:
        try:
            mint, meta, ms, err = meta_rows.get_nowait()
        except queue.Empty:
            return
        pending.discard(mint)
        if meta is None:
            stats["meta_fail_" + str(err)] += 1
            tape.write({"t": "meta", "ts": time.time(), "mint": mint,
                        "ok": False, "error": err, "fetch_ms": round(ms, 1)})
            continue
        stats["meta_ok"] += 1
        tape.write({"t": "meta", "ts": time.time(), "mint": mint, "ok": True,
                    "description": (meta.get("description") or "")[:2000],
                    "meta_name": meta.get("name"),
                    "twitter": meta.get("twitter"),
                    "website": meta.get("website"),
                    "telegram": meta.get("telegram"),
                    "image": (meta.get("image") or "")[:500] or None,
                    "fetch_ms": round(ms, 1)})


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tape", help="JSONL output; omit with --probe")
    ap.add_argument("--ws", default=os.environ.get("SOLANA_RPC_WS_URL", DEFAULT_WS))
    ap.add_argument("--duration", type=float, default=3600.0,
                    help="WALL-clock seconds")
    ap.add_argument("--max-watch", type=int, default=4000,
                    help="mints kept for trade joining; FIFO eviction")
    ap.add_argument("--watch-s", type=float, default=0.0,
                    help="keep a mint's trades only this long after its create (0 = while watched); "
                         "a launch study needs ~130 s, and the full firehose is ~1 GB/day")
    ap.add_argument("--fetch-meta", action="store_true", default=True)
    ap.add_argument("--no-fetch-meta", dest="fetch_meta", action="store_false")
    ap.add_argument("--meta-timeout", type=float, default=4.0)
    ap.add_argument("--meta-workers", type=int, default=4)
    ap.add_argument("--stats-every", type=float, default=300.0,
                    help="seconds between counter rows on the tape; 0 disables")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if websockets is None:
        sys.exit("snipefeed needs the `websockets` package")
    # A belt for the urllib braces: a gateway that stalls mid-read ignores the
    # connect timeout, and a default socket timeout bounds the read too.
    socket.setdefaulttimeout(max(args.meta_timeout * 2, 10.0))
    if args.probe:
        args.tape = None
        args.verbose = True
        if args.duration == 3600.0:
            args.duration = 60.0
    elif not args.tape:
        sys.exit("--tape is required unless --probe")
    if args.tape:
        os.makedirs(os.path.dirname(os.path.abspath(args.tape)), exist_ok=True)
    tape = Tape(args.tape)
    try:
        stats = asyncio.run(run(tape, args, args.ws))
    finally:
        tape.close()
    print(json.dumps({"rows": dict(tape.counts), "stats": dict(stats)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
