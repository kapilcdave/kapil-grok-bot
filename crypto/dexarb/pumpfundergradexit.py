"""What is the position worth when the bonding curve completes?

`pumpfunderreach.py` exits a completing curve at the completion transaction
itself. After completion the curve is closed, so that state cannot be sold
into: the 2-slot exit latency has nothing to land on. This script fetches the
post-migration PumpSwap pool and re-scores those rows with the same causal
rule.

Prereg: preregistrations/pumpfun_graduation_exit_20261004.md

    python3 pumpfundergradexit.py --fetch    # pool tapes for completing rows
    python3 pumpfundergradexit.py --score    # offline, the gate evaluation

Needs SOLANA_RPC_URL in ../../.env for --fetch. --score is offline.
"""
import argparse
import json
import os
import sys
import time

import pumpcausal as A
import pumpcurve as C
import pumpdeployer as D
import pumpfundinganalyze as F
import pumpfunderreach as R
import pumporacle as O
import pumpprobe as P
import swinghist as S

HERE = os.path.dirname(os.path.abspath(__file__))
CEILING_VSOL = 115.0 * C.LAMPORTS   # curve completion; measured max is 115.0054
SEAT_AHEAD = 1
HORIZON = 300
TRAIL = 0.15
EXIT_LATENCY_SLOTS = 2
POOL_FEE_BPS = 25.0                 # sell-side leg sum, data/pumpswap_fee.json
POOL_FEE_BPS_SENS = 32.5
PAGE_LIMIT = 100


def pool_sell_out(sol_res, tok_res, tok_in, fee_bps):
    """Constant-product sell into a PumpSwap pool, fee outside the invariant."""
    if tok_in <= 0 or sol_res <= 0 or tok_res <= 0:
        return 0.0
    gross = sol_res * tok_in / (tok_res + tok_in)
    return gross * (1.0 - fee_bps / 1e4)


def completing_rows(args):
    """B-period top-funder rows whose curve completes before a causal exit."""
    raw, metas = O.load(args.tape)
    virgin, _, _ = O.build_mints(raw, metas, C.discover_initial_states(raw))
    audit = F.latest(args.birth_audit, ("mint", "first_sig"))
    funding = F.latest(args.funding, ("mint", "create_sig"))
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2
    a_windows, b_windows = D.split_windows(virgin)

    def rows_at(ahead):
        return R.funded_rows(
            virgin, audit, funding, fee, txc, args.clip,
            lambda v, n=ahead: R.entry_after_up_to_same_slot_trades(v, n))

    zero = rows_at(0)
    _state, top, _bottom, _scores = R.fixed_groups(zero, a_windows, args)
    top = set(top)

    out = []
    for (window, mint), value in virgin.items():
        if window not in b_windows:
            continue
        first = value["tape"][0]
        birth = audit.get((mint, first["sig"]))
        if not birth or birth["status"] not in D.VERIFIED_BIRTH:
            continue
        edge = funding.get((mint, birth["create_sig"]))
        if not edge or edge.get("status") != "direct_funder_found":
            continue
        funders = edge.get("funders") or []
        if not funders or not funders[0].get("is_signer"):
            continue
        if funders[0]["address"] not in top:
            continue
        entry_i, ent = R.entry_after_up_to_same_slot_trades(value, SEAT_AHEAD)
        if ent is None or not A.covered(value, ent, HORIZON):
            continue
        clip = args.clip * C.LAMPORTS
        tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
        if tok <= 0:
            continue
        tape, birth_row = value["tape"], value["tape"][0]
        entry_ts = O.ts_of(ent, birth_row)
        indices = [i for i in range(entry_i, len(tape))
                   if O.ts_of(tape[i], birth_row) - entry_ts <= HORIZON]
        if not indices:
            continue
        # Replay the archive's own rule and stop at whichever comes first:
        # a trailing trigger, or the completion of the curve.
        peak = None
        trail_fired = False
        complete_i = None
        for i in indices:
            row = tape[i]
            if row["vsol"] >= CEILING_VSOL:
                complete_i = i
                break
            out_sol, _, _ = C.sell_fill(row["vsol"], row["vtok"], tok, fee)
            if peak is None or out_sol > peak:
                peak = out_sol
            elif out_sol <= (1 - TRAIL) * peak:
                trail_fired = True
                break
        if trail_fired or complete_i is None:
            continue
        last = tape[complete_i]
        archived = R.simulate_from_entry(
            value, fee, txc, args.clip, (entry_i, ent))
        out.append({
            "window": window,
            "mint": mint,
            "funder": funders[0]["address"],
            "deployer": funders[0]["address"],
            "entry_slot": ent["slot"],
            "entry_ts": entry_ts,
            "entry_bt": birth_row["bt"] + entry_ts,
            "tok_held": tok,
            "clip_lamports": clip,
            "txcost_lamports": txc,
            "completion_slot": last["slot"],
            "completion_bt": last["bt"],
            "completion_sig": last["sig"],
            "curve_peak_proceeds_lamports": peak,
            "archived_roi": archived,
            "horizon_end_bt": birth_row["bt"] + entry_ts + HORIZON,
        })
    return out, b_windows


def fetch_pool_tape(url, row, horizon_pad=120):
    """Decoded PumpSwap states for this mint from completion forward.

    Pages the mint's own signature index, which covers the pool's swaps because
    a PumpSwap swap names the base mint. Keeps states up to the horizon plus a
    pad, so a pool that only appears after the horizon is still observed and can
    be reported as `pool_absent_in_horizon` with a bound rather than guessed at.
    """
    stop_bt = row["horizon_end_bt"] + horizon_pad
    before = None
    collected = []
    pages = 0
    while pages < 40:
        sigs, err = S.sigs_before(url, row["mint"], before=before,
                                  limit=PAGE_LIMIT)
        if err:
            return None, "sigs: %s" % err
        if not sigs:
            break
        pages += 1
        collected.extend(sigs)
        oldest = sigs[-1]
        before = oldest["signature"]
        if (oldest.get("slot") or 0) <= row["completion_slot"]:
            break
    window = [
        s for s in collected
        if s.get("blockTime") is not None
        and (s.get("slot") or 0) >= row["completion_slot"]
        and s["blockTime"] <= stop_bt
    ]
    window.sort(key=lambda s: (s["slot"], s["signature"]))
    states = []
    for s in window:
        raw, err = S.tx(url, s["signature"])
        if err:
            continue
        got = S.reserves_from_tx(raw)
        if not got:
            continue
        states.append({
            "slot": s["slot"],
            "bt": s["blockTime"],
            "sol_reserve": got[0],
            "tok_reserve": got[1],
            "sig": s["signature"],
        })
    return {
        "mint": row["mint"],
        "signatures_scanned": len(window),
        "pages": pages,
        "states": states,
    }, None


def score_row(row, tape, fee_bps):
    """Same causal rule, evaluated on pool states instead of curve states."""
    tok = row["tok_held"]
    clip = row["clip_lamports"]
    txc = row["txcost_lamports"]
    states = [st for st in tape["states"]
              if st["slot"] >= row["completion_slot"] + EXIT_LATENCY_SLOTS]
    if not states:
        return {"outcome": "pool_absent_at_all", "roi": None}
    inside = [st for st in states if st["bt"] <= row["horizon_end_bt"]]
    first = states[0]
    migration_delay = first["bt"] - row["completion_bt"]
    if not inside:
        bound = pool_sell_out(first["sol_reserve"], first["tok_reserve"],
                              tok, fee_bps)
        return {
            "outcome": "pool_absent_in_horizon",
            "migration_delay_seconds": migration_delay,
            "first_pool_state_bt": first["bt"],
            "bound_roi": (bound - clip - txc) / clip,
            "total_loss_roi": -1.0,
            "roi": (bound - clip - txc) / clip,
        }
    peak = None
    exit_state = None
    for i, st in enumerate(inside):
        out = pool_sell_out(st["sol_reserve"], st["tok_reserve"], tok, fee_bps)
        if peak is None or out > peak:
            peak = out
        elif out <= (1 - TRAIL) * peak:
            want = st["slot"] + EXIT_LATENCY_SLOTS
            later = [s2 for s2 in inside[i:] if s2["slot"] >= want]
            exit_state = later[0] if later else None
            break
    if exit_state is None:
        exit_state = inside[-1]
    out = pool_sell_out(exit_state["sol_reserve"], exit_state["tok_reserve"],
                        tok, fee_bps)
    return {
        "outcome": "pool_exit_measured",
        "migration_delay_seconds": migration_delay,
        "pool_states_in_horizon": len(inside),
        "exit_bt": exit_state["bt"],
        "exit_slot": exit_state["slot"],
        "roi": (out - clip - txc) / clip,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    ap.add_argument("--birth-audit", default="data/pumpbirthaudit.jsonl")
    ap.add_argument("--funding", default="data/pumpfunding.jsonl")
    ap.add_argument("--clip", type=float, default=0.05)
    ap.add_argument("--min-a", type=int, default=3)
    ap.add_argument("--top-share", type=float, default=0.2)
    ap.add_argument("--draws", type=int, default=4000)
    ap.add_argument("--rows", default="data/pumpgradexit_rows.json")
    ap.add_argument("--tapes", default="data/pumpgradexit_tapes.json")
    ap.add_argument("--json", default="data/pumpgradexit.json")
    ap.add_argument("--rps", type=float, default=4.0)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()

    if args.fetch:
        rows, b_windows = completing_rows(args)
        json.dump({"rows": rows, "b_windows": sorted(b_windows)},
                  open(args.rows, "w"), indent=1, sort_keys=True)
        sys.stderr.write("completing rows: %d\n" % len(rows))
        env = P.load_env()
        url = env.get("SOLANA_RPC_URL") or P.PUBLIC_RPC
        S.LIMIT = S.Limiter(args.rps)
        tapes, failures = {}, {}
        for i, row in enumerate(rows, 1):
            tape, err = fetch_pool_tape(url, row)
            if err:
                failures[row["mint"]] = err
                sys.stderr.write("  %2d/%d %s FAILED %s\n"
                                 % (i, len(rows), row["mint"][:10], err))
                continue
            tapes[row["mint"]] = tape
            sys.stderr.write(
                "  %2d/%d %s sigs=%d states=%d\n"
                % (i, len(rows), row["mint"][:10],
                   tape["signatures_scanned"], len(tape["states"])))
        json.dump({"tapes": tapes, "failures": failures,
                   "rpc_calls": S.CALLS},
                  open(args.tapes, "w"), indent=1, sort_keys=True)
        sys.stderr.write("rpc calls=%d errors=%d throttled=%d\n"
                         % (S.CALLS["n"], S.CALLS["errors"],
                            S.CALLS["throttled"]))
        return

    if not args.score:
        ap.error("pass --fetch or --score")

    payload = json.load(open(args.rows))
    rows = payload["rows"]
    tapedata = json.load(open(args.tapes))
    tapes = tapedata["tapes"]
    scored = []
    for row in rows:
        tape = tapes.get(row["mint"])
        if not tape:
            scored.append({**row, "outcome": "fetch_failed", "roi": None})
            continue
        head = score_row(row, tape, POOL_FEE_BPS)
        sens = score_row(row, tape, POOL_FEE_BPS_SENS)
        scored.append({**row, **head,
                       "roi_at_%.1f_bps" % POOL_FEE_BPS_SENS: sens["roi"]})
    json.dump({"preregistration": (
        "preregistrations/pumpfun_graduation_exit_20261004.md"),
        "pool_fee_bps": POOL_FEE_BPS,
        "scored": scored,
        "failures": tapedata.get("failures", {}),
        "capital_enabled": False,
        "transaction_build_path": False,
        "transaction_sign_path": False,
        "transaction_submit_path": False},
        open(args.json, "w"), indent=1, sort_keys=True)
    for row in scored:
        sys.stderr.write(
            "%s  archived %8.3f  %-24s  realized %s\n"
            % (row["mint"][:10], row["archived_roi"], row["outcome"],
               "n/a" if row["roi"] is None else "%8.3f" % row["roi"]))
    return


if __name__ == "__main__":
    main()
