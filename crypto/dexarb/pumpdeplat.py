"""Is the deployer-history edge reachable without winning the entry race?

Prereg: preregistrations/pumpfun_deployer_latency_reach_20261002.md

Reruns the frozen deployer-persistence selection at a sweep of entry latencies.
Both the A score and the B outcome move together, so the operator measured at
L_in slots is the slow operator throughout.

    python3 pumpdeplat.py
"""
import argparse
import json
import sys

import pumpcausal as A
import pumpcurve as C
import pumpdeployer as D
import pumporacle as O

LATENCY_SLOTS = (0, 1, 2, 5, 12, 25, 50)
RETAIL_FLOOR_SLOTS = 5
MAGNITUDE_FLOOR_SOL_PER_WINDOW = 0.25
CAPACITY_CLIP_SOL = 0.5


def simulate_at(v, fee, txc, clip_sol, l_in, horizon=300, trail=0.15, l_out=2):
    """pumpsameentry.simulate with the entry fill delayed by l_in slots."""
    tape, birth = v["tape"], v["tape"][0]
    ei, ent = A.entry_of(v, l_in)
    if ent is None or not A.covered(v, ent, horizon):
        return None
    clip = clip_sol * C.LAMPORTS
    tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
    if tok <= 0:
        return None
    t_ent = O.ts_of(ent, birth)
    indices = [i for i in range(ei, len(tape))
               if O.ts_of(tape[i], birth) - t_ent <= horizon]
    if not indices:
        return None
    peak = None
    ex = None
    for i in indices:
        out, _, _ = C.sell_fill(tape[i]["vsol"], tape[i]["vtok"], tok, fee)
        if peak is None or out > peak:
            peak = out
        elif out <= (1 - trail) * peak:
            ex = A.exit_after(tape, i, tape[i]["slot"], l_out)
            break
    if ex is None:
        ex = tape[indices[-1]]
    out, _, _ = C.sell_fill(ex["vsol"], ex["vtok"], tok, fee)
    return (out - clip - txc) / C.LAMPORTS / clip_sol


def outcome_rows_at(virgin, fee, txc, clip_sol, l_in, creators=None):
    rows = []
    for (window, mint), v in virgin.items():
        roi = simulate_at(v, fee, txc, clip_sol, l_in)
        if roi is None:
            continue
        birth = v["tape"][0]
        rows.append({
            "window": window, "mint": mint, "slot": birth["slot"],
            "deployer": (
                creators.get((window, mint), birth["user"])
                if creators else birth["user"]
            ),
            "roi": roi,
        })
    return rows


def magnitude(rows, clip_sol, n_windows):
    """Mean SOL per selected launch and total SOL per B window."""
    if not rows or not n_windows:
        return None
    per_launch = sum(r["roi"] for r in rows) / len(rows) * clip_sol
    return {
        "clip_sol": clip_sol,
        "sol_per_launch": per_launch,
        "sol_per_window": per_launch * len(rows) / n_windows,
        "selected_launches": len(rows),
    }


def stratum_at(virgin, fee, txc, clip_sol, l_in, args, creators=None):
    rows = outcome_rows_at(
        virgin, fee, txc, clip_sol, l_in, creators=creators
    )
    a_windows, b_windows = D.split_windows(virgin)
    state = D.state_by_deployer(rows, a_windows)
    population_b = [r for r in rows if r["window"] in b_windows]
    if not population_b:
        return None
    out = D.run_stratum(state, population_b, args.min_a, args.top_share,
                        args.draws, args.null_draws, args.seed)
    out["latency_slots"] = l_in
    out["latency_seconds"] = l_in * O.SLOT_SEC
    out["n_rows"] = len(rows)
    out["population_b"] = D.describe(population_b, draws=args.draws)
    top, _bottom, _scores = D.select(state, args.min_a, args.top_share)
    out["magnitude"] = magnitude(D.b_rows(state, top), clip_sol, len(b_windows))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--json", default="data/pumpdeplat.json")
    parser.add_argument("--clip", type=float, default=0.05)
    parser.add_argument(
        "--birth-audit",
        default=None,
        help="restrict to RPC-verified CreateEvents and use their creator",
    )
    parser.add_argument("--min-a", type=int, default=3)
    parser.add_argument("--top-share", type=float, default=0.20)
    parser.add_argument("--draws", type=int, default=2000)
    parser.add_argument("--null-draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261002)
    args = parser.parse_args()

    raw, metas = O.load(args.tape)
    if not raw:
        sys.exit("no completed windows")
    virgin, _, _ = O.build_mints(raw, metas, C.discover_initial_states(raw))
    audited_candidates = None
    creators = None
    if args.birth_audit:
        audit = D.load_birth_audit(args.birth_audit)
        audited_candidates = len(audit)
        creators = {}
        filtered = {}
        for key, value in virgin.items():
            window, mint = key
            first_sig = value["tape"][0]["sig"]
            row = audit.get((mint, first_sig))
            if row and row["status"] in D.VERIFIED_BIRTH:
                filtered[key] = value
                creators[(window, mint)] = row["creator"]
        virgin = filtered
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2

    sweep = {}
    for l_in in LATENCY_SLOTS:
        stratum = stratum_at(
            virgin, fee, txc, args.clip, l_in, args, creators=creators
        )
        if stratum is not None:
            sweep[str(l_in)] = stratum
        print(f"latency {l_in} slots done", file=sys.stderr)

    capacity = stratum_at(virgin, fee, txc, CAPACITY_CLIP_SOL,
                          RETAIL_FLOOR_SLOTS, args, creators=creators)
    print("capacity row done", file=sys.stderr)

    floor = sweep.get(str(RETAIL_FLOOR_SLOTS))
    top = floor["top_b"] if floor else None
    contrast = floor["top_vs_population_ci"] if floor else None
    reachable = bool(
        top and contrast
        and top["mean_roi"] > 0 and top["clustered_ci"][0] > 0
        and contrast[0] > 0
    )
    cap_mag = capacity["magnitude"] if capacity else None
    too_small = bool(
        cap_mag is None
        or cap_mag["sol_per_window"] < MAGNITUDE_FLOOR_SOL_PER_WINDOW
    )
    verdict = "REACHABLE-WITHOUT-A-RACE" if reachable else "LATENCY-GATED"

    output = {
        "parameters": {
            "clip_sol": args.clip, "capacity_clip_sol": CAPACITY_CLIP_SOL,
            "fee_bps": C.FEE_BPS, "trail": 0.15, "horizon_seconds": 300,
            "exit_latency_slots": 2, "min_a": args.min_a,
            "top_share": args.top_share,
            "retail_floor_slots": RETAIL_FLOOR_SLOTS,
            "slot_seconds": O.SLOT_SEC,
            "magnitude_floor_sol_per_window": MAGNITUDE_FLOOR_SOL_PER_WINDOW,
            "birth_audit": args.birth_audit,
        },
        "birth_filter": {
            "enabled": bool(args.birth_audit),
            "audited_candidates": audited_candidates,
            "verified_mints": len(virgin),
        },
        "sweep": sweep,
        "capacity_row": capacity,
        "verdict": verdict,
        "magnitude_verdict": "TOO-SMALL-TO-TRADE" if too_small else "SIZEABLE",
        "builds_funding_graph": bool(reachable and not too_small),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
