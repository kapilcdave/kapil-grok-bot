"""Capital-disabled bankroll replay of verified pump.fun follower outcomes.

This is deliberately not `prod(1 + return)`: launches overlap, so capital
committed to one position cannot be reinvested until its causal exit. Position
size is recomputed through the bonding curve for the cash actually available.

Read-only and paper-only.
"""
import argparse
import heapq
import json
import sys
from pathlib import Path

import pumpcurve as C
import pumpdeployer as D
import pumpfunding as F
import pumpgraphexport as G
import pumporacle as O
import pumpsameentry as E


def verified_virgin(virgin, audit):
    return {
        key: value for key, value in virgin.items()
        if (
            (key[1], value["tape"][0]["sig"]) in audit
            and audit[(key[1], value["tape"][0]["sig"])]["status"]
            in D.VERIFIED_BIRTH
        )
    }


def graph_gated_virgin(virgin, audit, funding, source, allowed):
    """Out-of-sample B launches eligible under one frozen graph identity."""
    verified = verified_virgin(virgin, audit)
    _a_windows, b_windows = D.split_windows(virgin)
    out = {}
    for (window, mint), value in verified.items():
        if window not in b_windows:
            continue
        birth = audit[(mint, value["tape"][0]["sig"])]
        if source == "creator" and birth.get("creator") in allowed:
            out[(window, mint)] = value
            continue
        if source != "funder":
            continue
        edge = funding.get((mint, birth.get("create_sig")))
        funders = (edge or {}).get("funders") or []
        if (
            edge
            and edge.get("status") == "direct_funder_found"
            and funders
            and funders[0].get("is_signer")
            and funders[0].get("address") in allowed
        ):
            out[(window, mint)] = value
    return out


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def replay(virgin, fee, txc, initial_sol, risk_fraction, max_clip_sol,
           min_clip_sol, max_positions, roundtrip_tip_sol):
    candidates = sorted(
        virgin.items(), key=lambda item: (
            item[1]["tape"][0]["slot"], item[0][1]
        )
    )
    cash = initial_sol
    open_positions = []
    reserved = 0.0
    peak_equity = initial_sol
    max_drawdown = 0.0
    trades = []
    skipped_capacity = 0
    skipped_cash = 0

    def settle_through(slot):
        nonlocal cash, reserved, peak_equity, max_drawdown
        while open_positions and open_positions[0][0] <= slot:
            _exit_slot, _seq, position = heapq.heappop(open_positions)
            reserved -= position["clip_sol"]
            final_value = (
                position["clip_sol"] + position["pnl_sol"]
                - roundtrip_tip_sol
            )
            cash += max(0.0, final_value)
            position["final_value_sol"] = max(0.0, final_value)
            trades.append(position)
            equity = cash + reserved
            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                max_drawdown = max(
                    max_drawdown, 1.0 - equity / peak_equity
                )

    sequence = 0
    for (window, mint), v in candidates:
        entry_slot = v["tape"][0]["slot"]
        settle_through(entry_slot)
        if len(open_positions) >= max_positions:
            skipped_capacity += 1
            continue
        equity = cash + reserved
        clip = min(max_clip_sol, equity * risk_fraction, cash)
        if clip < min_clip_sol:
            skipped_cash += 1
            continue
        detail = E.simulate_detail(v, fee, txc, clip_sol=clip)
        if detail is None:
            continue
        cash -= clip
        reserved += clip
        position = {
            "window": window,
            "mint": mint,
            "clip_sol": clip,
            **detail,
        }
        heapq.heappush(
            open_positions,
            (detail["exit_slot"], sequence, position),
        )
        sequence += 1

    settle_through(10**30)
    returns = [row["pnl_sol"] - roundtrip_tip_sol for row in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    return {
        "initial_sol": initial_sol,
        "final_sol": cash,
        "net_sol": cash - initial_sol,
        "return_fraction": cash / initial_sol - 1,
        "max_drawdown_fraction": max_drawdown,
        "trades": len(trades),
        "win_share": len(wins) / len(trades) if trades else None,
        "mean_pnl_sol": sum(returns) / len(returns) if returns else None,
        "total_wins_sol": sum(wins),
        "total_losses_sol": sum(losses),
        "skipped_max_positions": skipped_capacity,
        "skipped_insufficient_cash": skipped_cash,
        "parameters": {
            "risk_fraction": risk_fraction,
            "max_clip_sol": max_clip_sol,
            "min_clip_sol": min_clip_sol,
            "max_positions": max_positions,
            "roundtrip_tip_sol": roundtrip_tip_sol,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--birth-audit", default="data/pumpbirthaudit.jsonl")
    parser.add_argument("--json", default="data/pumpcompound.json")
    parser.add_argument("--initial-sol", type=float, default=1.0)
    parser.add_argument("--risk-fraction", type=float, default=0.05)
    parser.add_argument("--max-clip-sol", type=float, default=0.05)
    parser.add_argument("--min-clip-sol", type=float, default=0.005)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--roundtrip-tip-sol", type=float, default=0.0)
    parser.add_argument("--graph-gated", action="store_true")
    parser.add_argument(
        "--deployer-result", default="data/pumpdeployer_verified.json"
    )
    parser.add_argument(
        "--funding-result", default="data/pumpfundinganalyze.json"
    )
    parser.add_argument(
        "--latency-result", default="data/pumpdeplat_verified.json"
    )
    parser.add_argument(
        "--funding-lookup-result",
        default="data/pumpfundinglookup_live.json",
    )
    parser.add_argument("--funding", default="data/pumpfunding.jsonl")
    args = parser.parse_args()

    audit = D.load_birth_audit(args.birth_audit)
    if not audit:
        sys.exit("no birth audit")
    raw, metas = O.load(args.tape)
    virgin, _, _ = O.build_mints(raw, metas, C.discover_initial_states(raw))
    source = None
    allowed = set()
    if args.graph_gated:
        required = (
            args.deployer_result,
            args.funding_result,
            args.latency_result,
        )
        if all(Path(path).exists() for path in required):
            source, allowed = G.authorized_source(
                read_json(args.deployer_result),
                read_json(args.funding_result),
                read_json(args.latency_result),
                (
                    read_json(args.funding_lookup_result)
                    if Path(args.funding_lookup_result).exists()
                    else None
                ),
            )
        funding = (
            F.latest_rows(args.funding, "create_sig")
            if Path(args.funding).exists() else {}
        )
        eligible = graph_gated_virgin(
            virgin, audit, funding, source, allowed
        )
    else:
        eligible = verified_virgin(virgin, audit)
    output = replay(
        eligible, C.FEE_BPS / 1e4, O.txcost(virgin) * 2,
        args.initial_sol, args.risk_fraction, args.max_clip_sol,
        args.min_clip_sol, args.max_positions, args.roundtrip_tip_sol,
    )
    output["coverage"] = {
        "audited_candidates": len(audit),
        "verified_births": len(verified_virgin(virgin, audit)),
        "graph_eligible_b_launches": len(eligible),
    }
    output["signal_gate"] = {
        "enforced": args.graph_gated,
        "authorized_source": source,
        "allowed_identities": len(allowed),
        "verdict": (
            "AUTHORIZED-SHADOW-SIGNAL"
            if source and allowed else "NO-AUTHORIZED-SIGNAL"
        ) if args.graph_gated else "NOT-ENFORCED",
    }
    output["warning"] = (
        "paper replay only; no transaction was built, signed, or submitted"
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
