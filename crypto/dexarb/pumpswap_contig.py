"""Collect a CONTIGUOUS PumpSwap tape, not a stratified burst sample.

`data/pumpswap_events.jsonl` is 36 bursts totalling 7.9 minutes (33 of exactly 30 slots plus one
487-slot run). That sampling design is fine for a seconds-horizon question and it closed the
copytrading branch, but it leaves the ONE right-signed cell in RESULT-copytrade-20260919.md
unmeasurable, for two separate reasons:

  1. THIN TAIL. Arm F (fade a seller) needs sells large relative to the pool's SOL reserve. The
     q = sell/reserve > 1e-1 bin holds 26 events in the whole archive -- below any floor. The cell
     below it (1e-2..1e-1) reaches +75.35 bps gross against a 60 bps round trip and nets
     -4.54 [-47.72, +52.75]: a zero, with the bin that would clear the bar unsampled.
  2. SAMPLED PRICE PATH. The archive does not contain consecutive trades -- only 0.01% of
     consecutive same-pool pairs have a reserve delta matching the later event's own amount -- so a
     copier's fill is "the first OBSERVED price at or after lag k" and the realised lag is >= k.
     Every price is exact, but the path between two prices is not there. A contiguous pull fixes
     this outright, and it can move the fade number in EITHER direction: the true path may contain a
     deeper hole (better) or an immediate refill by a faster arb (worse).

`pumpswap.cached_events` already does the work -- it takes an arbitrary slot list, persists each slot as it lands
(so a killed run keeps its data), and writes the same {slot, bt, disc, body} schema -- so this is a driver that hands it a
contiguous range instead of sampled windows, writing to a SEPARATE file so the burst archive stays
intact as the in-sample corpus.

    python3 pumpswap_contig.py --validate            # 300 slots, rate + yield, no commitment
    python3 pumpswap_contig.py --slots 18000         # ~2 h of chain, ~1.6 GB, ~60 min at 5 blk/s

Read-only RPC (getBlock). No key is loaded, nothing is signed. It does consume RPC quota, which is
why --validate reports the measured rate and yield before the full pull is worth launching.
"""
import argparse
import json
import os
import sys
import time

from pumpprobe import load_env, rpc
from pumpswap import cached_events

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "data/pumpswap_contig.jsonl")
BUY = "67f4521f2cf57777"
SELL = "3e2f370aa503dc2a"


def head_slot(url):
    head, err, _ = rpc(url, "getSlot", [{"commitment": "finalized"}])
    if err:
        sys.exit("getSlot failed: %s" % err)
    return head


def report(path):
    """Contiguity and yield of what is on disk. Contiguity is the whole point, so it is measured,
    not assumed: a resumable collector that silently drops blocks would reproduce the very burst
    structure this file exists to avoid."""
    slots, ev, empty = set(), 0, 0
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        slots.add(r["slot"])
        if r.get("disc") in (BUY, SELL):
            ev += 1
        elif r.get("disc") is None:
            empty += 1
    if not slots:
        print("nothing on disk yet")
        return
    lo, hi = min(slots), max(slots)
    span = hi - lo + 1
    print(f"  {len(slots)} slots on disk, span {lo}..{hi} ({span} slots, {span*0.4/60:.1f} min of "
          f"chain), COVERAGE {len(slots)/span:.2%}")
    print(f"  {ev} buy/sell events ({ev/max(1,len(slots)):.0f}/slot), {empty} slots with no "
          f"PumpSwap trade, {os.path.getsize(path)/1e6:.0f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=0)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--lo", type=int, default=0,
                    help="pin the first slot instead of deriving it from head. A resume MUST pass "
                         "this: head advances while the run is down, so a re-derived lo shifts the "
                         "window and silently mixes two ranges into one file.")
    ap.add_argument("--back", type=int, default=400,
                    help="step back from head so every slot is finalized and archived")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    if args.report:
        report(args.out)
        return

    n = 300 if args.validate else args.slots
    if n <= 0:
        sys.exit("give --slots N, or --validate, or --report")
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    if not url:
        sys.exit("SOLANA_RPC_URL not set")
    head = head_slot(url)
    lo = args.lo or (head - args.back - n)
    print(f"head {head}; pulling a CONTIGUOUS {n} slots from {lo} "
          f"({n*0.4/60:.1f} min of chain){' [PINNED --lo, resuming]' if args.lo else ''}")
    t0 = time.time()
    # keep=False: this driver only ever COUNTED the return value, and retaining it is what killed
    # the first 18,000-slot attempt (1.2 GB at 1,400 slots, machine into 9.9 GB of swap, fetch loop
    # stalled looking like rate limiting). The file is the product; report() reads it back off disk.
    evs = cached_events(url, list(range(lo, lo + n)), args.out, keep=False)
    el = time.time() - t0
    nev = evs if isinstance(evs, int) else len(evs)
    nbs = evs if isinstance(evs, int) else sum(1 for e in evs if e[2] in (BUY, SELL))
    print(f"\n{nev} PumpSwap events in {el:.0f}s = {n/max(el,1e-9):.2f} slot/s "
          f"(streaming counts ALL discriminators; report() below is the buy/sell authority)")
    if el > 0 and nbs:
        print(f"  projected for 18,000 slots (2 h): {18000/max(n/el,1e-9)/60:.0f} min, "
              f"{nbs*18000/n:,.0f} buy/sell events, "
              f"{os.path.getsize(args.out)/max(1,n)*18000/1e9:.2f} GB")
    report(args.out)


if __name__ == "__main__":
    main()
