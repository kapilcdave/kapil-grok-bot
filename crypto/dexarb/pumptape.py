"""Reconstruct pump.fun per-trade tape from archived blocks.

Why blocks and not candles: an oracle bound needs the exact price a clip would
have filled at, and on a bonding curve that is a closed-form function of the
curve's virtual reserves -- which every TradeEvent carries. Candles cannot
condition on a fill (`candle-volume-cannot-condition-on-a-fill`); reserves can.

Blocks are ~9 MB each, so events are extracted in flight and the block is
discarded. Output is one JSONL row per trade, ~200 bytes.

    python3 pumptape.py --validate                 # parse checks, no writing
    python3 pumptape.py --windows 24 --span-days 80

Resumable: a window whose .jsonl exists and ends with a DONE line is skipped.
"""
import argparse
import base64
import collections
import glob
import gzip
import json
import os
import random
import struct
import sys
import threading
import time
import concurrent.futures as cf

from pumpprobe import PUMP_FUN, load_env, rpc

TRADE_DISC = "bddb7fd34ee661ee"
CPI_EVENT_DISC = bytes.fromhex("e445a52e51cb9a1d")
MAX_TX_VERSION = 1          # measured: 0 rejects v1 blocks with RPC -32015
CONC = 6                    # measured SUSTAINED optimum: 5.05 blk/s, 0 failures.
                            # conc 12 and 18 are SLOWER (4.50) -- past the
                            # endpoint cap the extra requests only buy backoff.
LAMPORTS = 1e9
TOK_DP = 1e6                # pump.fun mints use 6 decimals

STATS = collections.Counter()

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58IDX = {c: i for i, c in enumerate(B58)}


def b58decode(s):
    n = 0
    for ch in s:
        n = n * 58 + _B58IDX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body


def b58encode(b):
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


def parse_trade(raw):
    """TradeEvent -> dict, or None. Layout verified in pumpdecode.py against an
    independent cross-check: sol/token must equal virtual reserve ratio."""
    if len(raw) < 113:
        return None
    off = 8
    mint = raw[off:off + 32]; off += 32
    sol, tok = struct.unpack_from("<QQ", raw, off); off += 16
    is_buy = raw[off]; off += 1
    user = raw[off:off + 32]; off += 32
    ts, = struct.unpack_from("<q", raw, off); off += 8
    vsol, vtok = struct.unpack_from("<QQ", raw, off)
    if is_buy > 1 or not (1.6e9 < ts < 2.5e9):
        return None                     # layout drift guard, not a filter
    if not (sol and tok and vsol and vtok):
        # ~2.7% of events: sol=0 for a large token move, on mints that never
        # appear with nonzero reserves. Not trades -- a zero-SOL transfer has
        # no price, so it can neither fill nor be marked. Counted, not hidden.
        STATS["zero_leg"] += 1
        return None
    return {"mint": b58encode(mint), "user": b58encode(user), "ts": ts,
            "buy": int(is_buy), "sol": sol, "tok": tok,
            "vsol": vsol, "vtok": vtok}


def events_in_tx(tx):
    """Anchor CPI events from inner instructions; pump.fun no longer puts
    TradeEvent in `Program data:` logs reliably, so read the instructions."""
    out = []
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return out                       # failed tx never moved the curve
    for grp in meta.get("innerInstructions") or []:
        for ix in grp.get("instructions") or []:
            d = ix.get("data")
            if not d or len(d) < 20:
                continue
            try:
                raw = b58decode(d)
            except (KeyError, ValueError):
                continue
            if raw[:8] != CPI_EVENT_DISC:
                continue
            body = raw[8:]
            if body[:8].hex() == TRADE_DISC:
                t = parse_trade(body)
                if t:
                    out.append(t)
    return out


def extract(block, slot):
    rows = []
    bt = block.get("blockTime")
    for tx in block.get("transactions") or []:
        sig = (tx.get("transaction") or {}).get("signatures") or [None]
        fee = (tx.get("meta") or {}).get("fee", 0)
        for ev in events_in_tx(tx):
            ev["slot"] = slot
            ev["bt"] = bt
            ev["sig"] = sig[0]
            ev["fee"] = fee
            rows.append(ev)
    return rows


class Fetcher:
    def __init__(self, url):
        self.url = url
        self.lock = threading.Lock()
        self.n429 = 0

    def block(self, slot, tries=6):
        for a in range(tries):
            r, e, _ = rpc(self.url, "getBlock", [slot, {
                "encoding": "json", "transactionDetails": "full",
                "rewards": False,
                "maxSupportedTransactionVersion": MAX_TX_VERSION}], timeout=120)
            if r is not None:
                return r, None
            e = e or ""
            if "skipped" in e.lower() or "-32009" in e or "-32007" in e:
                return None, "skipped"        # real gap in the ledger
            if "429" in e:
                with self.lock:
                    self.n429 += 1
                time.sleep(0.25 * (a + 1) + 0.2 * random.random())
                continue
            time.sleep(0.7 * (a + 1))
        return None, e or "exhausted"


CHUNK = 240        # slots held in flight; bounds peak RSS


def pull_range(f, lo, hi, sink, tag=""):
    """Fetch [lo,hi) concurrently, extract, hand rows to sink in slot order.

    Processed in contiguous chunks, and each future's result is dropped as soon
    as its events are extracted. Holding all futures instead costs ~5 MB of
    retained parsed JSON per block -- 4,500 slots would be ~23 GB and OOM."""
    got = skipped = failed = 0
    t0 = time.time()
    for cstart in range(lo, hi, CHUNK):
        cend = min(cstart + CHUNK, hi)
        rows = {}
        with cf.ThreadPoolExecutor(CONC) as ex:
            futs = {ex.submit(f.block, s): s for s in range(cstart, cend)}
            for fut in cf.as_completed(futs):
                s = futs[fut]
                try:
                    blk, err = fut.result()
                except Exception as exc:                  # noqa: BLE001
                    blk, err = None, type(exc).__name__
                if blk is None:
                    if err == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                else:
                    got += 1
                    rows[s] = extract(blk, s)
                blk = None
                fut._result = None       # release the parsed block immediately
            futs.clear()
        for s in sorted(rows):
            for row in rows[s]:
                sink(row)
        rows.clear()
        el = time.time() - t0
        sys.stderr.write(
            "  %s %d/%d blocks  %.1f blk/s  %d skipped %d FAILED %d x429\n"
            % (tag, got, hi - lo, got / max(el, 1e-9), skipped, failed, f.n429))
        sys.stderr.flush()
    return {"blocks_ok": got, "skipped": skipped, "failed": failed,
            "secs": round(time.time() - t0, 1)}


# --------------------------------------------------------------------------
# validation: the curve is deterministic, so parsing errors are detectable

def validate(url, nblocks=40):
    f = Fetcher(url)
    head, _, _ = rpc(url, "getSlot", [{"commitment": "finalized"}])
    lo = head - 20_000
    rows = []
    pull_range(f, lo, lo + nblocks, rows.append, tag="[validate]")
    print("\n%d trades in %d blocks (%d zero-SOL events excluded)"
          % (len(rows), nblocks, STATS["zero_leg"]))
    if not rows:
        print("NO TRADES PARSED -- layout or extraction is broken")
        return

    # 1. price identity: executed price must equal the curve ratio.
    err = []
    for r in rows:
        px_exec = (r["sol"] / LAMPORTS) / (r["tok"] / TOK_DP)
        px_curve = (r["vsol"] / LAMPORTS) / (r["vtok"] / TOK_DP)
        err.append(abs(px_exec / px_curve - 1))
    err.sort()
    print("|exec/curve - 1|: median %.4f  p90 %.4f  max %.4f"
          % (err[len(err) // 2], err[int(.9 * len(err))], err[-1]))
    print("  (fees + the half-step between pre/post reserves make this small")
    print("   but nonzero; a median above ~0.10 would mean a wrong layout)")

    # 2. constant product: recover pre-trade reserves and check k.
    kerr = []
    for r in rows:
        if r["buy"]:
            vs0, vt0 = r["vsol"] - r["sol"], r["vtok"] + r["tok"]
        else:
            vs0, vt0 = r["vsol"] + r["sol"], r["vtok"] - r["tok"]
        if vs0 <= 0 or vt0 <= 0:
            kerr.append(9.99)
            continue
        kerr.append(abs((r["vsol"] * r["vtok"]) / (vs0 * vt0) - 1))
    kerr.sort()
    print("|k_post/k_pre - 1|: median %.5f  p90 %.5f  max %.5f"
          % (kerr[len(kerr) // 2], kerr[int(.9 * len(kerr))], kerr[-1]))
    print("  (fee accrual makes k grow slightly; large values = bad parse)")

    # 3. what does a fresh curve look like? find the modal pre-trade vtok.
    pre = collections.Counter()
    for r in rows:
        vt0 = r["vtok"] + r["tok"] if r["buy"] else r["vtok"] - r["tok"]
        pre[vt0] += 1
    print("\nmost common pre-trade virtual token reserves (candidate initial):")
    for vt, n in pre.most_common(5):
        print("  %18d (%.1fM tok)  x%d" % (vt, vt / 1e6 / 1e6, n))

    mints = collections.Counter(r["mint"] for r in rows)
    print("\n%d distinct mints; trades/mint median %d max %d"
          % (len(mints), sorted(mints.values())[len(mints) // 2],
             max(mints.values())))
    print("buy share %.3f" % (sum(r["buy"] for r in rows) / len(rows)))


# --------------------------------------------------------------------------

def windows(head, n, span_days, wslots, seed):
    """Deterministic disjoint windows over the archive span."""
    span = int(span_days * 24 * 3600 / 0.4)          # ~400ms slots
    rnd = random.Random(seed)
    lo_bound = head - span
    picks = set()
    while len(picks) < n:
        s = rnd.randrange(lo_bound, head - wslots - 216_000)
        if all(abs(s - p) > wslots * 2 for p in picks):
            picks.add(s)
    return sorted(picks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--windows", type=int, default=24)
    ap.add_argument("--window-slots", type=int, default=4500)  # ~30 min
    ap.add_argument("--span-days", type=float, default=80)
    ap.add_argument("--seed", type=int, default=20260918)
    ap.add_argument("--out", default="data/pumptape")
    args = ap.parse_args()

    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    if not url:
        sys.exit("SOLANA_RPC_URL not set")

    if args.validate:
        return validate(url)

    os.makedirs(args.out, exist_ok=True)
    f = Fetcher(url)
    # Pin head in a manifest: windows() is seeded off it, so letting it drift
    # between restarts would silently redraw the window set and re-fetch work
    # that is already on disk.
    man = os.path.join(args.out, "manifest.json")
    if os.path.exists(man):
        head = json.load(open(man))["head"]
        print("resuming against pinned head %d" % head)
    else:
        head, _, _ = rpc(url, "getSlot", [{"commitment": "finalized"}])
        json.dump({"head": head, "seed": args.seed,
                   "window_slots": args.window_slots,
                   "span_days": args.span_days}, open(man, "w"))
    wins = windows(head, args.windows, args.span_days, args.window_slots,
                   args.seed)
    print("head %d; %d windows x %d slots over %.0f days"
          % (head, len(wins), args.window_slots, args.span_days))

    for i, lo in enumerate(wins):
        path = os.path.join(args.out, "w%d_%d.jsonl.gz" % (i, lo))
        if os.path.exists(path) and done_marker(path):
            print("[%d/%d] slot %d already complete, skip"
                  % (i + 1, len(wins), lo))
            continue
        t0 = time.time()
        n = [0]
        with gzip.open(path, "wt") as fh:
            def sink(row, fh=fh, n=n):
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                n[0] += 1
            stats = pull_range(f, lo, lo + args.window_slots, sink,
                               tag="[%d/%d]" % (i + 1, len(wins)))
            stats.update(window=i, lo_slot=lo, slots=args.window_slots,
                         trades=n[0], DONE=True)
            fh.write(json.dumps(stats, separators=(",", ":")) + "\n")
        print("[%d/%d] slot %d -> %d trades, %d blocks (%d skipped, %d failed)"
              " in %.0fs" % (i + 1, len(wins), lo, n[0], stats["blocks_ok"],
                             stats["skipped"], stats["failed"],
                             time.time() - t0))
        sys.stdout.flush()


def done_marker(path):
    try:
        with gzip.open(path, "rt") as fh:
            last = None
            for last in fh:
                pass
        return bool(last) and '"DONE":true' in last
    except Exception:                                    # noqa: BLE001
        return False


if __name__ == "__main__":
    main()
