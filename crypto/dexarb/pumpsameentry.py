"""Concrete same-slot entry with the already-fixed causal trailing exit."""

import argparse
import collections
import json
import random
import sys

import pumpcausal as A
import pumpcurve as C
import pumporacle as O


def entry_after_birth_transaction(v):
    tape = v["tape"]
    birth = tape[0]
    indices = [i for i, row in enumerate(tape)
               if row["slot"] == birth["slot"] and row["sig"] == birth["sig"]]
    if not indices:
        return None, None
    index = indices[-1]
    return index, tape[index]


def simulate_detail(v, fee, txc, clip_sol=1.0, horizon=300, trail=0.15,
                    L_out=2):
    tape, birth = v["tape"], v["tape"][0]
    ei, ent = entry_after_birth_transaction(v)
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
            ex = A.exit_after(tape, i, tape[i]["slot"], L_out)
            break
    if ex is None:
        ex = tape[indices[-1]]
    out, _, _ = C.sell_fill(ex["vsol"], ex["vtok"], tok, fee)
    pnl_sol = (out - clip - txc) / C.LAMPORTS
    return {
        "roi": pnl_sol / clip_sol,
        "pnl_sol": pnl_sol,
        "entry_slot": ent["slot"],
        "exit_slot": ex["slot"],
        "duration_slots": ex["slot"] - ent["slot"],
    }


def simulate(v, fee, txc, clip_sol=1.0, horizon=300, trail=0.15, L_out=2):
    detail = simulate_detail(
        v, fee, txc, clip_sol=clip_sol, horizon=horizon,
        trail=trail, L_out=L_out,
    )
    return detail["roi"] if detail else None


def clustered_ci(rows, draws=2000, seed=20260929):
    by_window = collections.defaultdict(list)
    for window, value in rows:
        by_window[window].append(value)
    windows = sorted(by_window)
    rnd = random.Random(seed)
    values = []
    for _ in range(draws):
        sample = []
        for _ in windows:
            sample.extend(by_window[rnd.choice(windows)])
        values.append(sum(sample) / len(sample))
    values.sort()
    return values[int(0.025 * draws)], values[int(0.975 * draws)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--json", default="data/pumpsameentry.json")
    parser.add_argument("--clip", type=float, default=1.0)
    parser.add_argument(
        "--birth-audit",
        default=None,
        help="restrict to RPC-verified CreateEvents",
    )
    args = parser.parse_args()
    raw, metas = O.load(args.tape)
    if not raw:
        sys.exit("no completed windows")
    states = C.discover_initial_states(raw)
    virgin, _, _ = O.build_mints(raw, metas, states)
    audited_candidates = None
    if args.birth_audit:
        audited = {}
        with open(args.birth_audit) as handle:
            for line in handle:
                row = json.loads(line)
                audited[(row["mint"], row["first_sig"])] = row
        audited_candidates = len(audited)
        verified = {"create_in_first_trade_tx", "create_precedes_first_trade"}
        virgin = {
            key: value for key, value in virgin.items()
            if (
                (key[1], value["tape"][0]["sig"]) in audited
                and audited[(key[1], value["tape"][0]["sig"])]["status"]
                in verified
            )
        }
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2
    rows = []
    for (window, _), v in virgin.items():
        value = simulate(v, fee, txc, clip_sol=args.clip)
        if value is not None:
            rows.append((window, value))
    values = sorted(value for _, value in rows)
    ci = clustered_ci(rows)
    output = {
        "n": len(rows),
        "windows": len({window for window, _ in rows}),
        "mean_return": sum(values) / len(values),
        "median_return": C.median(values),
        "clustered_ci": ci,
        "share_positive": sum(value > 0 for value in values) / len(values),
        "passed_mechanism": ci[0] > 0,
        "parameters": {"clip_sol": args.clip, "fee_bps": C.FEE_BPS,
                       "trail": 0.15, "horizon_seconds": 300,
                       "exit_latency_slots": 2,
                       "birth_audit": args.birth_audit},
        "birth_filter": {
            "enabled": bool(args.birth_audit),
            "audited_candidates": audited_candidates,
            "verified_mints": len(virgin),
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
