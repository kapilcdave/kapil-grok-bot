"""Test whether the fixed zero-slot funder selector survives reachable seats.

The funder set is selected once from A-period zero-slot outcomes. Every later
seat scores that same frozen set on B. This differs deliberately from
``pumpfundinganalyze.py``'s latency-matched reselection: the operational
question is whether the actual zero-slot signal survives our fill delay.

Preregistration:
    preregistrations/pumpfun_zero_slot_funder_reach_20261004.md
"""
import argparse
import json
import sys

import pumpcausal as A
import pumpcurve as C
import pumpdeployer as D
import pumpfundinganalyze as F
import pumporacle as O
import pumpsameentry as E


SAME_SLOT_AHEAD = (0, 1, 2)
SLOT_FLOORS = (0, 1, 2, 5)
LEAD_GATE_SLOTS = 5


def entry_after_up_to_same_slot_trades(v, ahead):
    """State after at most ``ahead`` later transactions in the birth slot.

    A fixed adverse-ordering count must not discard quiet launches merely
    because fewer competitors arrive. It advances only while a later tape row
    remains in the birth slot, and advances through every event sharing a
    competitor transaction signature.
    """
    base_i, base = E.entry_after_birth_transaction(v)
    if base is None:
        return None, None
    tape = v["tape"]
    index = base_i
    for _ in range(ahead):
        candidate = index + 1
        if candidate >= len(tape):
            break
        if tape[candidate]["slot"] != base["slot"]:
            break
        competitor_signature = tape[candidate]["sig"]
        index = candidate
        while (
            index + 1 < len(tape)
            and tape[index + 1]["slot"] == base["slot"]
            and tape[index + 1]["sig"] == competitor_signature
        ):
            index += 1
    return index, tape[index]


def simulate_from_entry(
    v,
    fee,
    txc,
    clip_sol,
    entry,
    horizon=300,
    trail=0.15,
    exit_latency_slots=2,
):
    """Frozen trailing outcome from an explicitly chosen entry state."""
    entry_i, ent = entry
    if ent is None or not A.covered(v, ent, horizon):
        return None
    tape, birth = v["tape"], v["tape"][0]
    clip = clip_sol * C.LAMPORTS
    tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
    if tok <= 0:
        return None
    entry_ts = O.ts_of(ent, birth)
    indices = [
        i for i in range(entry_i, len(tape))
        if O.ts_of(tape[i], birth) - entry_ts <= horizon
    ]
    if not indices:
        return None
    peak = None
    exit_row = None
    for i in indices:
        out, _, _ = C.sell_fill(
            tape[i]["vsol"], tape[i]["vtok"], tok, fee
        )
        if peak is None or out > peak:
            peak = out
        elif out <= (1 - trail) * peak:
            exit_row = A.exit_after(
                tape, i, tape[i]["slot"], exit_latency_slots
            )
            break
    if exit_row is None:
        exit_row = tape[indices[-1]]
    out, _, _ = C.sell_fill(
        exit_row["vsol"], exit_row["vtok"], tok, fee
    )
    return (out - clip - txc) / C.LAMPORTS / clip_sol


def funded_rows(virgin, audit, funding, fee, txc, clip, entry_fn):
    rows = []
    for (window, mint), value in virgin.items():
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
        roi = simulate_from_entry(
            value, fee, txc, clip, entry_fn(value)
        )
        if roi is None:
            continue
        rows.append({
            "window": window,
            "mint": mint,
            "slot": birth["create_slot"],
            "creator": birth["creator"],
            "deployer": funders[0]["address"],
            "funder": funders[0]["address"],
            "funding_slot": edge["funding_slot"],
            "funding_sig": edge["funding_sig"],
            "funding_slots_before_create": edge["slots_before_create"],
            "roi": roi,
        })
    return rows


def fixed_groups(zero_rows, a_windows, args):
    state = D.state_by_deployer(zero_rows, a_windows)
    top, bottom, scores = D.select(
        state, args.min_a, args.top_share
    )
    return state, set(top), set(bottom), scores


def fixed_seat(rows, b_windows, top, bottom, draws):
    population = [row for row in rows if row["window"] in b_windows]
    top_rows = [row for row in population if row["funder"] in top]
    bottom_rows = [row for row in population if row["funder"] in bottom]
    values = sorted(row["roi"] for row in top_rows)
    total = sum(values)
    by_funder = {}
    for row in top_rows:
        by_funder.setdefault(row["funder"], []).append(row["roi"])
    leave_one_out = [
        (total - sum(group)) / (len(values) - len(group))
        for group in by_funder.values()
        if len(group) < len(values)
    ]
    robustness = {
        "right_tail_capped_mean": {
            str(cap): (
                sum(min(value, cap) for value in values) / len(values)
                if values else None
            )
            for cap in (0.2, 0.5, 1.0, 2.0, 5.0)
        },
        "share_return_at_least_20pct": (
            sum(value >= 0.2 for value in values) / len(values)
            if values else None
        ),
        "maximum_roi": max(values) if values else None,
        "top_1_profit_share": (
            max(values) / total if values and total > 0 else None
        ),
        "top_5_profit_share": (
            sum(values[-5:]) / total if values and total > 0 else None
        ),
        "leave_one_funder_out_mean_range": (
            [min(leave_one_out), max(leave_one_out)]
            if leave_one_out else None
        ),
    }
    return {
        "population_b": D.describe(population, draws=draws),
        "top_b": D.describe(top_rows, draws=draws),
        "bottom_b": D.describe(bottom_rows, draws=draws),
        "top_vs_population_ci": (
            D.clustered_contrast(top_rows, population, draws=draws)
            if top_rows and population else None
        ),
        "top_vs_bottom_ci": (
            D.clustered_contrast(top_rows, bottom_rows, draws=draws)
            if top_rows and bottom_rows else None
        ),
        "selected_top_funders_present": len({
            row["funder"] for row in top_rows
        }),
        "selected_bottom_funders_present": len({
            row["funder"] for row in bottom_rows
        }),
        "robustness_diagnostics": robustness,
    }


def lead_diagnostics(rows, b_windows, top):
    selected = [
        row for row in rows
        if row["window"] in b_windows and row["funder"] in top
        and isinstance(row.get("funding_slots_before_create"), int)
    ]
    leads = sorted(
        row["funding_slots_before_create"] for row in selected
    )

    def quantile(p):
        if not leads:
            return None
        return leads[min(len(leads) - 1, int(p * len(leads)))]

    return {
        "n": len(leads),
        "minimum_slots": min(leads) if leads else None,
        "p05_slots": quantile(0.05),
        "p50_slots": quantile(0.50),
        "p95_slots": quantile(0.95),
        "share_at_least_5_slots": (
            sum(value >= LEAD_GATE_SLOTS for value in leads) / len(leads)
            if leads else None
        ),
        "lead_gate_slots": LEAD_GATE_SLOTS,
    }


def seat_pass(seat):
    top = seat.get("top_b") or {}
    population_ci = seat.get("top_vs_population_ci")
    bottom_ci = seat.get("top_vs_bottom_ci")
    return bool(
        top.get("n", 0) >= 50
        and top.get("windows", 0) >= 6
        and top.get("mean_roi", 0) > 0
        and top.get("clustered_ci")
        and top["clustered_ci"][0] > 0
        and population_ci
        and population_ci[0] > 0
        and bottom_ci
        and bottom_ci[0] > 0
    )


def run(args):
    raw, metas = O.load(args.tape)
    if not raw:
        raise ValueError("no completed windows")
    virgin, _, _ = O.build_mints(
        raw, metas, C.discover_initial_states(raw)
    )
    audit = F.latest(args.birth_audit, ("mint", "first_sig"))
    funding = F.latest(args.funding, ("mint", "create_sig"))
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2
    a_windows, b_windows = D.split_windows(virgin)

    zero_rows = funded_rows(
        virgin,
        audit,
        funding,
        fee,
        txc,
        args.clip,
        lambda value: entry_after_up_to_same_slot_trades(value, 0),
    )
    _state, top, bottom, scores = fixed_groups(
        zero_rows, a_windows, args
    )

    seats = {}
    rows_by_seat = {}
    for ahead in SAME_SLOT_AHEAD:
        name = f"up_to_{ahead}_same_slot_ahead"
        rows = (
            zero_rows if ahead == 0 else funded_rows(
                virgin,
                audit,
                funding,
                fee,
                txc,
                args.clip,
                lambda value, n=ahead:
                    entry_after_up_to_same_slot_trades(value, n),
            )
        )
        rows_by_seat[name] = rows
        seats[name] = fixed_seat(
            rows, b_windows, top, bottom, args.draws
        )

    for slots in SLOT_FLOORS:
        name = (
            "birth_slot_end" if slots == 0
            else f"plus_{slots}_slot" + ("s" if slots != 1 else "")
        )
        rows = funded_rows(
            virgin,
            audit,
            funding,
            fee,
            txc,
            args.clip,
            lambda value, n=slots: A.entry_of(value, n),
        )
        rows_by_seat[name] = rows
        seats[name] = fixed_seat(
            rows, b_windows, top, bottom, args.draws
        )

    original = json.load(open(args.funding_result))
    original_pass = (
        original.get("funder_persistence_verdict")
        == "FUNDER-HISTORY-PERSISTS"
    )
    one_ahead_pass = seat_pass(seats["up_to_1_same_slot_ahead"])
    lead = lead_diagnostics(zero_rows, b_windows, top)
    lead_pass = bool(
        lead["share_at_least_5_slots"] is not None
        and lead["share_at_least_5_slots"] >= 0.95
    )
    candidate = original_pass and one_ahead_pass and lead_pass
    output = {
        "preregistration": (
            "preregistrations/"
            "pumpfun_zero_slot_funder_reach_20261004.md"
        ),
        "parameters": {
            "clip_sol": args.clip,
            "fee_bps": C.FEE_BPS,
            "trail": 0.15,
            "horizon_seconds": 300,
            "exit_latency_slots": 2,
            "min_a": args.min_a,
            "top_share": args.top_share,
        },
        "a_windows": sorted(a_windows),
        "b_windows": sorted(b_windows),
        "coverage": {
            "verified_births": sum(
                row["status"] in D.VERIFIED_BIRTH
                for row in audit.values()
            ),
            "zero_slot_funded_rows": len(zero_rows),
            "selected_top_funders": len(top),
            "selected_bottom_funders": len(bottom),
        },
        "selection_as_of_slot": max(
            row["slot"] for row in zero_rows
            if row["window"] in a_windows
        ),
        "selected_top_funders": sorted(top),
        "selected_bottom_funders": sorted(bottom),
        "a_scores": {
            funder: scores[funder]
            for funder in sorted(top | bottom)
        },
        "funding_lead": lead,
        "seats": seats,
        "gates": {
            "original_zero_slot_persistence": original_pass,
            "one_same_slot_ahead": one_ahead_pass,
            "preindex_lead": lead_pass,
        },
        "verdict": (
            "ZERO-SLOT-FUNDER-SHADOW-CANDIDATE"
            if candidate else "ZERO-SLOT-FUNDER-NOT-REACHABLE"
        ),
        "capital_enabled": False,
        "transaction_build_path": False,
        "transaction_sign_path": False,
        "transaction_submit_path": False,
    }
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument(
        "--birth-audit", default="data/pumpbirthaudit.jsonl"
    )
    parser.add_argument("--funding", default="data/pumpfunding.jsonl")
    parser.add_argument(
        "--funding-result", default="data/pumpfundinganalyze.json"
    )
    parser.add_argument(
        "--json", default="data/pumpfunderreach.json"
    )
    parser.add_argument("--clip", type=float, default=0.05)
    parser.add_argument("--min-a", type=int, default=3)
    parser.add_argument("--top-share", type=float, default=0.20)
    parser.add_argument("--draws", type=int, default=2000)
    args = parser.parse_args()
    try:
        output = run(args)
    except ValueError as error:
        sys.exit(str(error))
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
