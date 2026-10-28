"""Read the PumpSwap LP fee share off the chain instead of assuming it.

`pumpswapr.py` reports R across a grid of f because the LP's share of the swap
fee was an ASSUMPTION (20 bps of a 25 bps fee). The prereg
(preregistrations/pumpswap_lp_seat_20260918.md, gate 1) says that has to be
verified on chain, not assumed, because f multiplies R linearly -- a 2x error in
f is a 2x error in the verdict.

It does not need a config account or an IDL. The swap event itself carries the
fee in basis points as a literal u64 field: a fee rate is a SMALL number that is
CONSTANT across every swap in the program, which is a fingerprint no reserve,
amount or pubkey fragment has. So scan every u64 offset in the body and keep the
ones that are (a) in a plausible bps range and (b) near-constant program-wide.

The paired check is the one that actually proves it: for each candidate bps
field, there should be a NEIGHBOURING u64 holding the fee AMOUNT, and

    fee_amount ~= bps/10000 * quote_amount

must hold across swaps of wildly different size. A coincidental small constant
will not satisfy a ratio test against a varying amount -- same logic as the
Q/V identity check validating the reserve offsets in pumpswapr.py.

    python3 pumpswapfee.py --blocks 60
"""
import argparse
import collections
import json
import struct
import sys

from pumpprobe import load_env, rpc
import pumpswap as S

BPS_LO, BPS_HI = 1, 500          # a swap fee outside 0.01%-5% is not a fee
MIN_SHARE = 0.90                 # "constant program-wide" means this share


def const_u64_fields(evs, disc):
    """u64 offsets whose value is a small, near-constant number: fee candidates."""
    sel = [b for (_s, _t, d, b) in evs if d == disc]
    if not sel:
        return [], 0
    ln = min(len(b) for b in sel)
    out = []
    # EVERY offset, not every 8th. Anchor serialises structs PACKED, so a u64
    # only lands on a multiple of 8 if every preceding field happens to pad it
    # there -- and this event has a 32-byte pubkey in it, plus u8 flags. The
    # aligned-only version of this scan reported "no fee field exists", which was
    # a statement about the stride, not about the chain.
    for off in range(0, ln - 8 + 1):
        vals = collections.Counter(struct.unpack_from("<Q", b, off)[0]
                                   for b in sel)
        top, n = vals.most_common(1)[0]
        share = n / len(sel)
        if BPS_LO <= top <= BPS_HI and share >= MIN_SHARE:
            out.append({"off": off, "bps": top, "share": share,
                        "n_distinct": len(vals)})
    return out, len(sel)


def amount_ratio(evs, disc, bps_off, bps, amt_off, min_amt=10_000):
    """Does some other u64 behave like bps/10000 of a varying amount?

    Returns the median realised rate in bps and the spread of it. A real fee
    field gives a TIGHT distribution centred on `bps` across orders of
    magnitude of size; an unrelated pair of integers does not."""
    rates = []
    for (_s, _t, d, b) in evs:
        if d != disc or len(b) < max(bps_off, amt_off) + 8:
            continue
        if struct.unpack_from("<Q", b, bps_off)[0] != bps:
            continue
        amt = struct.unpack_from("<Q", b, amt_off)[0]
        if amt < min_amt:
            continue
        # search every OTHER offset for a value that is ~bps/1e4 of this amount
        rates.append(amt)
    if len(rates) < 20:
        return None
    rates.sort()
    return {"n": len(rates), "p50": rates[len(rates) // 2],
            "p10": rates[len(rates) // 10], "p90": rates[9 * len(rates) // 10]}


def fee_amount_partner(evs, disc, bps_off, bps, min_share=0.5):
    """Find the u64 that IS the fee amount for this bps field.

    Searching (amount_off, fee_off) PAIRS is O(offsets^2) per event, which an
    unaligned scan cannot afford. Invert it: for a candidate amount offset,
    the fee is a PREDICTED VALUE, round(amount*bps/1e4), so just ask whether
    that value appears anywhere among the body's u64s. That is O(offsets) per
    event and tests the same claim -- a coincidental small constant cannot
    predict a varying amount's fee across orders of magnitude of size.

    Rounding direction is unknown (a fee is floor()ed or ceil()ed depending on
    the program), so a hit within +/-1 lamport counts."""
    sel = [b for (_s, _t, d, b) in evs if d == disc
           and len(b) >= bps_off + 8
           and struct.unpack_from("<Q", b, bps_off)[0] == bps]
    if len(sel) < 20:
        return []
    ln = min(len(b) for b in sel)
    offs = list(range(0, ln - 8 + 1))
    hits = collections.defaultdict(list)     # a_off -> [(f_off, amount)]
    for b in sel:
        vals = {}
        for o in offs:
            vals.setdefault(struct.unpack_from("<Q", b, o)[0], o)
        for a_off in offs:
            a = struct.unpack_from("<Q", b, a_off)[0]
            if a < 10_000 or a > 2 ** 60:
                continue
            want = a * bps // 10000
            for w in (want, want + 1, want - 1):
                if w > 0 and w in vals and vals[w] != a_off:
                    hits[a_off].append((vals[w], a))
                    break
    cands = []
    for a_off, hs in hits.items():
        share = len(hs) / float(len(sel))
        if share < min_share:
            continue
        amts = [a for _f, a in hs]
        f_off = collections.Counter(f for f, _a in hs).most_common(1)[0]
        cands.append({"amount_off": a_off, "fee_off": f_off[0],
                      "fee_off_share": f_off[1] / float(len(hs)),
                      "med_bps": float(bps), "tight": share,
                      "n": len(hs), "size_span": max(amts) / max(min(amts), 1)})
    cands.sort(key=lambda c: (-c["tight"], -c["size_span"]))
    return cands[:6]


def fee_legs(evs, disc, amount_off, lo_bps=1.0, hi_bps=500.0, min_share=0.80):
    """Every u64 that behaves like a FEE on `amount_off`, and the rate it implies.

    The constancy scan can only find fee fields that are the same for every pool,
    so it found 5 bps and stopped. It cannot find the LP's own share if that is
    per-pool or dynamic -- and a field that fails a 90%-constant test is exactly
    what a DYNAMIC fee looks like. This asks the weaker, sufficient question
    instead: given the amount, which offsets hold a plausible fraction of it?

    Reports the rate distribution per leg, because the spread is the answer to
    "is this fee fixed or dynamic?" and that decides whether one `f` can stand
    in for the whole cross-section."""
    sel = [b for (_s, _t, d, b) in evs if d == disc and len(b) >= amount_off + 8]
    if len(sel) < 20:
        return []
    ln = min(len(b) for b in sel)
    acc = collections.defaultdict(list)
    for b in sel:
        a = struct.unpack_from("<Q", b, amount_off)[0]
        if a < 100_000:
            continue
        for o in range(0, ln - 8 + 1):
            if o == amount_off:
                continue
            v = struct.unpack_from("<Q", b, o)[0]
            if v <= 0 or v >= a:
                continue
            r = 1e4 * v / a
            if lo_bps <= r <= hi_bps:
                acc[o].append(r)
    n_amt = sum(1 for b in sel
                if struct.unpack_from("<Q", b, amount_off)[0] >= 100_000)
    out = []
    for o, rs in acc.items():
        if len(rs) < min_share * max(n_amt, 1):
            continue
        rs.sort()
        p50 = rs[len(rs) // 2]
        out.append({"off": o, "n": len(rs), "share": len(rs) / float(n_amt),
                    "p10": rs[len(rs) // 10], "p50": p50,
                    "p90": rs[9 * len(rs) // 10],
                    "fixed": (rs[9 * len(rs) // 10] - rs[len(rs) // 10])
                    <= 0.02 * p50})
    out.sort(key=lambda c: -c["share"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=60)
    ap.add_argument("--json", default="data/pumpswap_fee.json")
    args = ap.parse_args()
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    if not url:
        sys.exit("SOLANA_RPC_URL not set")
    head, err, _ = rpc(url, "getSlot", [{"commitment": "finalized"}])
    if err:
        sys.exit("getSlot failed: %s" % err)
    lo = head - 400 - args.blocks
    slots = list(range(lo, lo + args.blocks))
    print("head %d; %d slots (fee fields are constants, so a small sample is "
          "enough)" % (head, len(slots)))
    evs = S.collect_events(url, slots)
    if not evs:
        sys.exit("no PUMP_SWAP events")
    discs = collections.Counter(d for (_s, _t, d, _b) in evs)
    out = {}
    for disc, c in discs.most_common(2):
        print("\n=== disc=%s (%d events) ===" % (disc, c))
        cands, n = const_u64_fields(evs, disc)
        if not cands:
            print("  no u64 field is a small near-constant: no fee rate here")
            continue
        # An unaligned scan finds the same field several times over (a small u64
        # read one byte early is the same bytes times 256), so cap the work at
        # the most-constant few rather than partner-testing every hit.
        cands = sorted(cands, key=lambda c: (-c["share"], c["n_distinct"]))[:8]
        print("  %5s %8s %9s %10s   %s" % ("off", "value", "share", "distinct",
                                           "reading"))
        for cd in cands:
            print("  @%3d  %8d %8.1f%% %10d   %.2f%% fee"
                  % (cd["off"], cd["bps"], 100 * cd["share"], cd["n_distinct"],
                     cd["bps"] / 100.0))
        out[disc] = {"n_events": n, "bps_candidates": cands, "verified": []}
        for cd in cands:
            part = fee_amount_partner(evs, disc, cd["off"], cd["bps"])
            if not part:
                continue
            print("\n  --- @%d = %d bps: is there an amount/fee pair that "
                  "REALISES it? ---" % (cd["off"], cd["bps"]))
            print("  %10s %8s %10s %8s %12s"
                  % ("amount@", "fee@", "med bps", "n", "size span"))
            for p in part:
                print("  %10d %8d %10.2f %8d %11.0fx"
                      % (p["amount_off"], p["fee_off"], p["med_bps"], p["n"],
                         p["size_span"]))
            out[disc]["verified"].append({"bps_off": cd["off"],
                                          "bps": cd["bps"], "pairs": part})
            # With the amount field proven, enumerate ALL the fee legs on it.
            legs = fee_legs(evs, disc, part[0]["amount_off"])
            if legs:
                print("\n  --- every fee-shaped leg on amount@%d ---"
                      % part[0]["amount_off"])
                print("  %6s %7s %8s %9s %9s %9s  %s"
                      % ("fee@", "n", "share", "p10 bps", "p50 bps", "p90 bps",
                         "fixed?"))
                for lg in legs:
                    print("  %6d %7d %7.0f%% %9.2f %9.2f %9.2f  %s"
                          % (lg["off"], lg["n"], 100 * lg["share"], lg["p10"],
                             lg["p50"], lg["p90"],
                             "FIXED" if lg["fixed"] else "varies"))
                tot = sum(lg["p50"] for lg in legs)
                print("  legs sum to %.2f bps of the swap amount" % tot)
                out[disc]["legs"] = legs
                out[disc]["legs_sum_bps"] = tot
            break      # one proven amount field is enough

    print("\n=== what this settles ===")
    allb = sorted({cd["bps"] for v in out.values()
                   for cd in v["bps_candidates"]})
    print("  constant fee-shaped fields seen: %s" % (allb or "none"))
    print("  pumpswapr.py assumed the LP keeps 20 bps. If the fields above show")
    print("  a SPLIT (an lp_fee_basis_points and a protocol_fee_basis_points),")
    print("  the LP number is the one to use and R scales linearly with it.")
    with open(args.json, "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
