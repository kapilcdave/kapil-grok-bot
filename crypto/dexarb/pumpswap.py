"""Decode PumpSwap (post-graduation AMM) swaps, and measure the LP screen.

Prereg: preregistrations/pumpswap_lp_seat_20260918.md. The decision variable is
committed there:

    R = 8 * f * (Q/V) / sigma^2      LP beats its rebalancing benchmark iff R>1

This module does the two things needed to evaluate it: discover the swap event
layout from the chain (never guessed -- the pump.fun TradeEvent layout was found
the same way), then measure turnover and vol per pool over the same window.

    python3 pumpswap.py --discover --blocks 300     # event layout, from bytes
    python3 pumpswap.py --measure  --blocks 3000    # R, per pool
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import struct
import sys
import time

from pumpprobe import PUMP_SWAP, load_env, rpc
import pumptape as T

LAMPORTS = 1e9
CPI_EVENT_DISC = bytes.fromhex("e445a52e51cb9a1d")


def cpi_payloads(tx):
    """Every Anchor CPI event payload in a transaction, with its program id.

    The event sits in INNER instructions, wrapped by the 8-byte self-CPI
    discriminator; the 8 bytes after that are the event's own discriminator."""
    meta = tx.get("meta") or {}
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = msg.get("accountKeys") or []
    keys = [k if isinstance(k, str) else k.get("pubkey") for k in keys]
    loaded = (meta.get("loadedAddresses") or {})
    keys = keys + list(loaded.get("writable") or []) + \
        list(loaded.get("readonly") or [])
    out = []
    for grp in meta.get("innerInstructions") or []:
        for ins in grp.get("instructions") or []:
            pid_i = ins.get("programIdIndex")
            if pid_i is None or pid_i >= len(keys):
                continue
            data = ins.get("data")
            if not data:
                continue
            try:
                raw = T.b58decode(data)
            except Exception:                                 # noqa: BLE001
                continue
            if not raw.startswith(CPI_EVENT_DISC) or len(raw) < 16:
                continue
            out.append((keys[pid_i], raw[8:16].hex(), raw[16:]))
    return out


def discover(url, slots):
    """Histogram event discriminators and body lengths for PUMP_SWAP.

    Two things get verified here before any number is produced: that the program
    id in pumpprobe.py is real and active, and what its events actually look
    like. `ignored-unknown-fields-make-a-wrong-name-look-unset` is the adjacent
    trap -- assuming a layout and reading zeros would look like a quiet venue."""
    shapes = collections.Counter()
    progs = collections.Counter()
    samples = {}
    n_tx = 0
    f = T.Fetcher(url)
    with cf.ThreadPoolExecutor(max_workers=T.CONC) as ex:
        futs = {ex.submit(f.block, s): s for s in slots}
        for fut in cf.as_completed(futs):
            try:
                blk, _err = fut.result()
            except Exception:                                 # noqa: BLE001
                continue
            if not blk:
                continue
            for tx in blk.get("transactions") or []:
                n_tx += 1
                for pid, disc, body in cpi_payloads(tx):
                    progs[pid] += 1
                    if pid != PUMP_SWAP:
                        continue
                    shapes[(disc, len(body))] += 1
                    samples.setdefault((disc, len(body)), body)
    print("=== program ids emitting Anchor CPI events, top 8 ===")
    for pid, c in progs.most_common(8):
        mark = "  <-- PUMP_SWAP" if pid == PUMP_SWAP else ""
        print("  %6d  %s%s" % (c, pid, mark))
    if not shapes:
        print("\n  PUMP_SWAP emitted NO CPI events in %d transactions." % n_tx)
        print("  That is a finding, not a bug: either the id is wrong or the")
        print("  program is not in use at these slots. Do not proceed to a")
        print("  measurement on an unverified id.")
        return None
    print("\n=== PUMP_SWAP event shapes (disc, body length) ===")
    for (disc, ln), c in shapes.most_common(10):
        print("  %7d  disc=%s  body=%dB" % (c, disc, ln))
    # Locate reserve-shaped u64 pairs: a pool reserve is large, and changes
    # between events, so scan offsets and report which ones look like reserves.
    print("\n=== u64 fields in the most common body, by offset ===")
    (disc, ln), _ = shapes.most_common(1)[0]
    body = samples[(disc, ln)]
    for off in range(0, ln - 8 + 1, 8):
        v = struct.unpack_from("<Q", body, off)[0]
        note = ""
        if 1e8 < v < 1e13:
            note = "  <- SOL-scale (%.3f if lamports)" % (v / LAMPORTS)
        elif 1e13 <= v < 1e18:
            note = "  <- token-scale"
        elif v in (0, 1):
            note = "  <- flag"
        print("  @%3d  %22d%s" % (off, v, note))
    return {"shapes": {"%s:%d" % k: v for k, v in shapes.items()},
            "n_tx": n_tx, "top_disc": disc, "top_len": ln}


def collect_events(url, slots, want_discs=None):
    """Every PUMP_SWAP CPI event in these slots, as (slot, disc, body).

    Kept raw so the layout search below runs offline and repeatedly against one
    fetch -- `historical-api-substitutes-for-live-collection`."""
    evs = []
    f = T.Fetcher(url)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=T.CONC) as ex:
        futs = {ex.submit(f.block, s): s for s in slots}
        for fut in cf.as_completed(futs):
            s = futs[fut]
            try:
                blk, _e = fut.result()
            except Exception:                                 # noqa: BLE001
                blk = None
            done += 1
            if not blk:
                continue
            bt = blk.get("blockTime")
            for tx in blk.get("transactions") or []:
                for pid, disc, body in cpi_payloads(tx):
                    if pid != PUMP_SWAP:
                        continue
                    if want_discs and disc not in want_discs:
                        continue
                    evs.append((s, bt, disc, body))
    evs.sort(key=lambda r: r[0])
    sys.stderr.write("  fetched %d slots, %d PUMP_SWAP events\n"
                     % (done, len(evs)))
    return evs


FETCH_CHUNK = 24   # = 4 x CONC. NOT a throughput knob -- a MEMORY one, and the binding constraint.
# `as_completed` holds a reference to every future for the whole iteration, and a future retains its
# result, so peak RSS is (chunk size) x (parsed block), not (workers) x (parsed block). At T.CHUNK=240
# a full-detail mainnet block (~13 MB parsed) gave 3.2 GB peak and the 18,000-slot pull thrashed the
# machine into 9.9 GB of swap. Dropping to 24 caps retention at ~300 MB. Deleting from the futs dict
# does NOT help: as_completed still holds its own refs.


def _rss_mb():
    """Own RSS. Printed every 50 slots because the collector's real failure mode was invisible:
    it looked like rate-limit backoff and was memory pressure, so the progress line now carries
    the number that would have identified it."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6   # macOS reports bytes
    except Exception:                                           # noqa: BLE001
        return 0.0


def cached_events(url, slots, path, quiet=False, offline=False, keep=True):
    """collect_events, but every fetched slot is persisted and never refetched.

    `keep=False` streams: slots are written and then FORGOTTEN, and the return
    value is only a count. Persisting a slot fixed the "a dead run bought no
    data" failure below, but the in-memory `evs` list survived it, and on an
    18,000-slot pull that is the new way to die: 1,400 slots cost 1.2 GB RSS,
    projecting to ~15 GB, and the machine thrashed into swap (9.9 GB of 11.3 GB)
    until the fetch loop stalled outright with free RAM at 67 MB. The stall
    looked exactly like rate-limit backoff and was not -- 429s were incidental.
    When the file IS the product, holding the product in memory too is what
    breaks the collection.

    The 1200-block selection run stalled for 2h46m in rate-limit backoff and
    produced NOTHING, because collect_events holds its results in memory: a run
    that dies has bought no data at all. That is a self-inflicted wound on a
    public endpoint. Here each slot is appended to a JSONL as soon as it lands,
    and slots already in the file are skipped, so a stalled run can be killed
    and resumed and the analysis re-run for free
    (`historical-api-substitutes-for-live-collection`).

    A slot that was fetched and held NO PumpSwap event must be recorded too,
    otherwise it looks unfetched forever and every resume refetches it. Those
    go in as a sentinel row with disc=null."""
    # The cache may span a WIDER window than this call asks for, so it is
    # filtered to `slots`. Without that, re-running with a different --blocks
    # would analyse whatever happened to be on disk while printing the
    # requested count -- a silent sample expansion, the mirror image of
    # `a-hardcoded-input-list-is-a-silent-sample-restriction`.
    want = set(slots)
    have, evs, n_seen = set(), [], 0
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue            # truncated final line from a kill
                have.add(r["slot"])
                if r.get("disc") and r["slot"] in want:
                    if keep:
                        evs.append((r["slot"], r.get("bt"), r["disc"],
                                    bytes.fromhex(r["body"])))
                    else:
                        n_seen += 1
    todo = [s for s in slots if s not in have]
    if not quiet:
        print("  cache %s: %d slots on disk, %d events; fetching %d more%s"
              % (path, len(have), len(evs) or n_seen, len(todo),
                 "  [streaming: events are written, not retained]" if not keep else ""))
    if offline:
        got = sorted(want & have)
        if not quiet:
            # WHERE the hole is matters more than how big it is: a contiguous
            # tail shortens the second half of a split-half test, while a
            # scattered hole only thins it. Report the span actually covered.
            print("  OFFLINE: analysing %d of %d requested slots (%.1f%%)"
                  % (len(got), len(slots), 100 * len(got) / max(len(slots), 1)))
            if got:
                print("  covered span %d..%d of requested %d..%d"
                      % (got[0], got[-1], slots[0], slots[-1]))
        evs.sort(key=lambda r: r[0])
        return evs
    if todo:
        f = T.Fetcher(url)
        t0, done = time.time(), 0
        with open(path, "a") as fh:
            # Submit in CHUNKS, and drop each future the moment its block is written. Submitting
            # every slot up front is what actually killed the 18,000-slot pull: a future retains
            # its result until it is released, so `futs` held the parsed JSON of every block
            # already fetched -- ~2 MB each, 12 MB/slot of RSS growth observed, 4.5 GB by slot 350.
            # `pull_range` documents this exact hazard ("4,500 slots would be ~23 GB and OOM") and
            # this function had the bug anyway. Streaming the EVENTS was not enough; the futures
            # were the corpus being retained.
            for cstart in range(0, len(todo), FETCH_CHUNK):
                chunk = todo[cstart:cstart + FETCH_CHUNK]
                with cf.ThreadPoolExecutor(max_workers=T.CONC) as ex:
                    futs = {ex.submit(f.block, s): s for s in chunk}
                    for fut in cf.as_completed(futs):
                        s = futs[fut]
                        try:
                            blk, _e = fut.result()
                        except Exception:                     # noqa: BLE001
                            blk = None
                        done += 1
                        n_here = 0
                        if blk:
                            bt = blk.get("blockTime")
                            for tx in blk.get("transactions") or []:
                                for pid, disc, body in cpi_payloads(tx):
                                    if pid != PUMP_SWAP:
                                        continue
                                    fh.write(json.dumps(
                                        {"slot": s, "bt": bt, "disc": disc,
                                         "body": body.hex()}) + "\n")
                                    if keep:
                                        evs.append((s, bt, disc, body))
                                    else:
                                        n_seen += 1
                                    n_here += 1
                            if n_here == 0:
                                fh.write(json.dumps({"slot": s, "bt": bt,
                                                     "disc": None}) + "\n")
                            fh.flush()
                        blk = None          # release the parsed block before the next as_completed
                        if not quiet and done % 50 == 0:
                            el = time.time() - t0
                            print("    %d/%d slots  %.2f blk/s  %d events  "
                                  "%d x 429  rss %.0fMB"
                                  % (done, len(todo), done / max(el, 1e-9),
                                     len(evs) or n_seen, f.n429, _rss_mb()), flush=True)
                    futs.clear()
        if not quiet:
            print("    fetched %d slots in %.0fs (%d x 429)"
                  % (done, time.time() - t0, f.n429))
    if not keep:
        return n_seen           # a count, not a corpus: the caller reads the file back off disk
    evs.sort(key=lambda r: r[0])
    return evs


def layout(evs, disc, nshow=4):
    """Print the numeric header of one event type across several samples.

    Varying vs constant across samples is what separates reserves and amounts
    from pubkey bytes and config. Reading a single sample cannot do that."""
    sel = [b for (_s, _t, d, b) in evs if d == disc][:400]
    if not sel:
        print("  no events with disc=%s" % disc)
        return
    ln = min(len(b) for b in sel)
    print("\n=== disc=%s : first 112B as u64, %d samples (of %d events) ==="
          % (disc, nshow, len(sel)))
    print("  %5s %s" % ("off", "".join("%22s" % ("sample %d" % i)
                                       for i in range(nshow))))
    for off in range(0, min(112, ln - 8) + 1, 8):
        vals = [struct.unpack_from("<Q", b, off)[0] for b in sel[:nshow]]
        allv = [struct.unpack_from("<Q", b, off)[0] for b in sel]
        uniq = len(set(allv))
        tag = " const" if uniq == 1 else " uniq=%d" % uniq
        print("  @%3d  %s%s" % (off, "".join("%22d" % v for v in vals), tag))


TOK_OFF, SOL_OFF = 40, 48       # read off the layout dump, validated below


def find_pool_key(evs, disc, tok_off=TOK_OFF, sol_off=SOL_OFF, min_ev=3):
    """Which 32-byte field identifies the pool? Decided by the CPMM invariant.

    A constant-product pool's `k = x*y` is NOT conserved -- fees are added to
    the reserves, so k *grows slowly and monotonically*. That is a much sharper
    fingerprint than "k is constant", and it is only reproduced when the events
    are grouped by the RIGHT key: group by the wrong pubkey and consecutive
    rows come from unrelated pools, so k jumps by orders of magnitude.

    Checking the trajectory rather than each row is the lesson in
    `per-observation-invariance-does-not-validate-the-state-machine` -- the
    per-event invariant is what fooled me on the bonding curve."""
    sel = [(s, b) for (s, _t, d, b) in evs if d == disc]
    if not sel:
        return None
    ln = min(len(b) for _s, b in sel)
    best = []
    for off in range(56, ln - 32 + 1, 8):
        groups = collections.defaultdict(list)
        for s, b in sel:
            groups[b[off:off + 32]].append((s, b))
        multi = {k: v for k, v in groups.items() if len(v) >= min_ev}
        if len(multi) < 5:
            continue
        drift, grow = [], 0
        for v in multi.values():
            v.sort(key=lambda r: r[0])
            ks = []
            for _s, b in v:
                x = struct.unpack_from("<Q", b, tok_off)[0]
                y = struct.unpack_from("<Q", b, sol_off)[0]
                if x and y:
                    ks.append(x * y)
            for a, c in zip(ks, ks[1:]):
                if a:
                    drift.append(abs(c - a) / a)
                    grow += (c >= a)
        if not drift:
            continue
        drift.sort()
        best.append((drift[len(drift) // 2], off, len(multi), len(drift),
                     grow / len(drift)))
    best.sort()
    print("\n=== which field is the pool? ranked by median |dk/k| between")
    print("    consecutive events of the same group (fees make k grow, so the")
    print("    right key gives a SMALL drift that is mostly POSITIVE) ===")
    print("  %5s %8s %8s %10s %9s" % ("off", "pools", "pairs", "med|dk/k|",
                                      "frac k up"))
    for d, off, npool, npair, up in best[:6]:
        print("  @%3d  %8d %8d %10.5f %8.1f%%" % (off, npool, npair, d,
                                                  100 * up))
    if not best:
        print("  no candidate field grouped events into repeat pools")
        return None
    return {"off": best[0][1], "med_drift": best[0][0], "pools": best[0][2],
            "frac_up": best[0][4]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--layout", action="store_true")
    ap.add_argument("--poolkey", action="store_true")
    ap.add_argument("--blocks", type=int, default=300)
    ap.add_argument("--json", default="data/pumpswap_discover.json")
    args = ap.parse_args()

    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    if not url:
        sys.exit("SOLANA_RPC_URL not set")
    head, err, _ = rpc(url, "getSlot", [{"commitment": "finalized"}])
    if err:
        sys.exit("getSlot failed: %s" % err)
    # step back a little from head so every slot is finalized and archived
    lo = head - 400 - args.blocks
    slots = list(range(lo, lo + args.blocks))
    print("head %d; sampling %d slots from %d" % (head, len(slots), lo))

    out = {}
    t0 = time.time()
    if args.discover:
        out["discover"] = discover(url, slots)
    if args.layout:
        evs = collect_events(url, slots)
        seen = collections.Counter(d for (_s, _t, d, _b) in evs)
        for disc, c in seen.most_common(2):
            print("\n(%d events with this discriminator)" % c)
            layout(evs, disc)
    if args.poolkey:
        evs = collect_events(url, slots)
        seen = collections.Counter(d for (_s, _t, d, _b) in evs)
        out["poolkey"] = {}
        for disc, c in seen.most_common(2):
            print("\n--- disc=%s (%d events) ---" % (disc, c))
            out["poolkey"][disc] = find_pool_key(evs, disc)
    print("\n%.1fs" % (time.time() - t0))
    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)
    print("wrote %s" % args.json)


if __name__ == "__main__":
    main()
