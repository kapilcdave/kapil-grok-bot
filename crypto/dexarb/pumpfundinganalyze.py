"""Score verified pump.fun launches by their causal direct-funder graph.

Prereg: preregistrations/pumpfun_funder_graph_20261002.md

Requires completed outputs from:

    pumpbirthaudit.py --all
    pumpfunding.py --all
"""
import argparse
import collections
import json
import sys

import pumpcurve as C
import pumpdeplat as L
import pumpdeployer as D
import pumporacle as O
import pumpsameentry as E


def latest(path, key_fields):
    rows = {}
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            rows[tuple(row[field] for field in key_fields)] = row
    return rows


def peak_vsol_300(v):
    tape = v["tape"]
    birth = tape[0]
    values = [
        row["vsol"] / C.LAMPORTS for row in tape
        if O.ts_of(row, birth) <= 300
    ]
    return max(values) if values else None


def funded_outcomes(
    virgin,
    audit,
    funding,
    fee,
    txc,
    clip,
    entry_latency_slots=0,
):
    rows = []
    for (window, mint), v in virgin.items():
        first = v["tape"][0]
        birth = audit.get((mint, first["sig"]))
        if not birth or birth["status"] not in D.VERIFIED_BIRTH:
            continue
        edge = funding.get((mint, birth["create_sig"]))
        if not edge or edge["status"] != "direct_funder_found":
            continue
        if not edge.get("funders"):
            continue
        roi = (
            E.simulate(v, fee, txc, clip_sol=clip)
            if entry_latency_slots == 0
            else L.simulate_at(
                v, fee, txc, clip, entry_latency_slots
            )
        )
        if roi is None:
            continue
        primary = edge["funders"][0]
        if not primary["is_signer"]:
            continue
        rows.append({
            "window": window,
            "mint": mint,
            "slot": birth["create_slot"],
            "creator": birth["creator"],
            "deployer": primary["address"],  # compatibility with deployer scorer
            "funder": primary["address"],
            "funding_slot": edge["funding_slot"],
            "funding_sol": edge["funding_lamports"] / C.LAMPORTS,
            "funding_source_is_signer": primary["is_signer"],
            "roi": roi,
            "peak_vsol_300": peak_vsol_300(v),
        })
    return rows


def persistence_test(rows, virgin, args):
    a_windows, b_windows = D.split_windows(virgin)
    state = D.state_by_deployer(rows, a_windows)
    population_b = [row for row in rows if row["window"] in b_windows]
    if not population_b:
        return {
            "population_b": None,
            "persistence": {
                "top_b": None,
                "selected_top_users": [],
            },
            "gates": {
                "power": False,
                "sign": False,
                "attribution": False,
                "rank": False,
                "selection_null": False,
            },
            "verdict": "INSUFFICIENT-FUNDING-COVERAGE",
        }
    persistence = D.run_stratum(
        state,
        population_b,
        args.min_a,
        args.top_share,
        args.draws,
        args.null_draws,
        args.seed,
    )
    top = persistence["top_b"]
    top_pop = persistence["top_vs_population_ci"]
    top_bottom = persistence["top_vs_bottom_ci"]
    rank = persistence["rank_persistence"]
    null = persistence["selection_null"]
    gates = {
        "power": bool(top and top["n"] >= 50 and top["windows"] >= 6),
        "sign": bool(
            top and top["mean_roi"] > 0 and top["clustered_ci"][0] > 0
        ),
        "attribution": bool(
            top_pop and top_bottom and top_pop[0] > 0 and top_bottom[0] > 0
        ),
        "rank": bool(
            rank["rho"] is not None and rank["rho"] > 0
            and rank["one_sided_permutation_p"] < 0.05
        ),
        "selection_null": bool(
            persistence["observed_top_vs_population"] is not None
            and null["p95"] is not None
            and persistence["observed_top_vs_population"] > null["p95"]
        ),
    }
    return {
        "population_b": D.describe(population_b, draws=args.draws),
        "persistence": persistence,
        "gates": gates,
        "verdict": (
            "FUNDER-HISTORY-PERSISTS"
            if all(gates.values()) else "NO-FUNDER-HISTORY-PERSISTENCE"
        ),
    }


def add_causal_burst(rows, lookback_slots=20):
    """Prior/current launch count by funder; never uses a future creation."""
    histories = collections.defaultdict(list)
    out = []
    for row in sorted(rows, key=lambda r: (r["slot"], r["mint"])):
        history = histories[row["funder"]]
        current = [
            old for old in history
            if old["slot"] >= row["slot"] - lookback_slots
        ]
        copy = dict(row)
        # The preregistered unit is launches, not distinct creator wallets.
        # Keep the distinct-wallet count as a diagnostic rather than silently
        # substituting it for the frozen launch-count feature.
        copy["burst_count"] = len(current) + 1
        copy["burst_distinct_creators"] = len(
            {old["creator"] for old in current} | {row["creator"]}
        )
        out.append(copy)
        histories[row["funder"]] = current + [row]
    return out


def share_ci(rows, predicate, draws=2000, seed=20261002):
    transformed = [
        {**row, "roi": float(predicate(row))}
        for row in rows
    ]
    return D.clustered_mean_ci(transformed, draws=draws, seed=seed)


def burst_test(rows, b_windows, draws):
    b = [row for row in rows if row["window"] in b_windows]
    flagged = [row for row in b if row["burst_count"] >= 2]
    control = [row for row in b if row["burst_count"] < 2]
    contrast = (
        D.clustered_contrast(flagged, control, draws=draws)
        if flagged and control else None
    )
    tail_flagged = share_ci(
        flagged, lambda r: r["roi"] <= -0.15, draws=draws
    ) if flagged else None
    tail_control = share_ci(
        control, lambda r: r["roi"] <= -0.15, draws=draws
    ) if control else None
    peak_flagged = [
        {**row, "roi": row["peak_vsol_300"]} for row in flagged
        if row["peak_vsol_300"] is not None
    ]
    peak_control = [
        {**row, "roi": row["peak_vsol_300"]} for row in control
        if row["peak_vsol_300"] is not None
    ]
    return {
        "flagged": D.describe(flagged, draws=draws),
        "unflagged": D.describe(control, draws=draws),
        "flagged_minus_unflagged_roi_ci": contrast,
        "loss_worse_15pct": {
            "flagged_share": (
                sum(r["roi"] <= -0.15 for r in flagged) / len(flagged)
                if flagged else None
            ),
            "flagged_ci": tail_flagged,
            "unflagged_share": (
                sum(r["roi"] <= -0.15 for r in control) / len(control)
                if control else None
            ),
            "unflagged_ci": tail_control,
        },
        "peak_vsol_300": {
            "flagged_mean": (
                sum(r["roi"] for r in peak_flagged) / len(peak_flagged)
                if peak_flagged else None
            ),
            "unflagged_mean": (
                sum(r["roi"] for r in peak_control) / len(peak_control)
                if peak_control else None
            ),
        },
        "gate_power": len(flagged) >= 100,
        "gate_negative_upper_bound": bool(contrast and contrast[1] < 0),
        "useful_exclusion": bool(
            len(flagged) >= 100 and contrast and contrast[1] < 0
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--birth-audit", default="data/pumpbirthaudit.jsonl")
    parser.add_argument("--funding", default="data/pumpfunding.jsonl")
    parser.add_argument("--json", default="data/pumpfundinganalyze.json")
    parser.add_argument("--clip", type=float, default=0.05)
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
    audit = latest(args.birth_audit, ("mint", "first_sig"))
    funding = latest(args.funding, ("mint", "create_sig"))
    rows = funded_outcomes(
        virgin, audit, funding, C.FEE_BPS / 1e4,
        O.txcost(virgin) * 2, args.clip,
    )
    rows = add_causal_burst(rows)
    retail_rows = funded_outcomes(
        virgin,
        audit,
        funding,
        C.FEE_BPS / 1e4,
        O.txcost(virgin) * 2,
        args.clip,
        entry_latency_slots=L.RETAIL_FLOOR_SLOTS,
    )
    a_windows, b_windows = D.split_windows(virgin)
    primary = persistence_test(rows, virgin, args)
    retail = persistence_test(retail_rows, virgin, args)
    output = {
        "parameters": {
            "clip_sol": args.clip,
            "min_a": args.min_a,
            "top_share": args.top_share,
            "burst_lookback_slots": 20,
            "retail_floor_slots": L.RETAIL_FLOOR_SLOTS,
        },
        "coverage": {
            "verified_audit_rows": sum(
                row["status"] in D.VERIFIED_BIRTH for row in audit.values()
            ),
            "funding_rows": len(funding),
            "eligible_funded_outcomes": len(rows),
            "eligible_funded_outcomes_at_retail_floor": len(retail_rows),
        },
        "a_windows": sorted(a_windows),
        "b_windows": sorted(b_windows),
        "funded_population_b": primary["population_b"],
        "funder_persistence": primary["persistence"],
        "funder_persistence_gates": primary["gates"],
        "funder_persistence_verdict": primary["verdict"],
        "funded_population_b_at_retail_floor": retail["population_b"],
        "funder_persistence_at_retail_floor": retail["persistence"],
        "funder_persistence_at_retail_floor_gates": retail["gates"],
        "funder_persistence_at_retail_floor_verdict": retail["verdict"],
        "coordinated_funding_burst": burst_test(rows, b_windows, args.draws),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
