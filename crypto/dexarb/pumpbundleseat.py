"""Is the zero-slot funder edge a launcher's bundle, and does it survive a seat?

Reproduces every table in RESULT-pumpfun-bundle-seat-20261004.md:

    python3 pumpbundleseat.py --exit-audit   # how the archived exits were chosen
    python3 pumpbundleseat.py --bundle       # profit concentration + interleaving
    python3 pumpbundleseat.py --seats        # the signal at reachable seats
    python3 pumpbundleseat.py --all

Offline. Reads the same tape, birth audit, and funding edges as
`pumpfunderreach.py`, and reuses its frozen funder selection unchanged.

Prereg: preregistrations/pumpfun_graduation_exit_20261004.md
"""
import argparse
import collections
import json
import random

import pumpcausal as A
import pumpcurve as C
import pumpdeployer as D
import pumpfundinganalyze as F
import pumpfunderreach as R
import pumporacle as O

CEILING_VSOL = 115.0 * C.LAMPORTS
HORIZON = 300
TRAIL = 0.15
EXIT_LATENCY_SLOTS = 2
SEAT_AHEAD = 1
CLUSTER_THRESHOLDS = (2, 3, 4)


# ------------------------------------------------------------------ the data

def load(args):
    raw, metas = O.load(args.tape)
    virgin, _, _ = O.build_mints(raw, metas, C.discover_initial_states(raw))
    audit = F.latest(args.birth_audit, ("mint", "first_sig"))
    funding = F.latest(args.funding, ("mint", "create_sig"))
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2
    a_windows, b_windows = D.split_windows(virgin)
    return virgin, audit, funding, fee, txc, a_windows, b_windows


def frozen_top(virgin, audit, funding, fee, txc, a_windows, args):
    zero = R.funded_rows(
        virgin, audit, funding, fee, txc, args.clip,
        lambda v: R.entry_after_up_to_same_slot_trades(v, 0))
    _state, top, bottom, _scores = R.fixed_groups(zero, a_windows, args)
    return set(top), set(bottom)


def top_funder_mints(virgin, audit, funding, top, b_windows):
    """B-period launches whose verified direct funder is a selected top funder."""
    out = {}
    for (window, mint), value in virgin.items():
        if window not in b_windows:
            continue
        birth = audit.get((mint, value["tape"][0]["sig"]))
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
        out[(window, mint)] = value
    return out


# ------------------------------------------------------------ the bundle mark

def fee_cluster(value):
    """Largest set of distinct wallets sharing one identical priority fee.

    A launcher running several wallets through one submission path signs them
    with one fee. The mark needs no bundle API: it is read off the tape.
    """
    tape = value["tape"]
    birth = tape[0]
    by_fee = collections.defaultdict(set)
    for row in tape:
        if row["slot"] == birth["slot"] and row["buy"]:
            by_fee[row["fee"]].add(row["user"])
    return max((len(w) for w in by_fee.values()), default=0), by_fee


def interleaving(value):
    """Did any non-cluster transaction land inside the cluster's run?"""
    size, by_fee = fee_cluster(value)
    if size < 3:
        return None
    cluster_fee = max(by_fee, key=lambda k: len(by_fee[k]))
    members = by_fee[cluster_fee]
    tape = value["tape"]
    birth = tape[0]
    sequence = []
    for row in tape:
        if row["slot"] != birth["slot"]:
            continue
        if not sequence or sequence[-1][0] != row["sig"]:
            sequence.append((row["sig"], row["user"], row["fee"]))
    positions = [
        i for i, (_s, user, fee_v) in enumerate(sequence)
        if user in members and fee_v == cluster_fee]
    first, last = min(positions), max(positions)
    outsiders = [
        i for i, (_s, user, fee_v) in enumerate(sequence)
        if first < i < last and not (user in members and fee_v == cluster_fee)]
    return {
        "cluster_wallets": size,
        "birth_slot_transactions": len(sequence),
        "cluster_span": [first, last],
        "outsiders_inside_span": len(outsiders),
        "starts_immediately_after_create": first == 1,
    }


# --------------------------------------------------------------- exit audits

def audit_archived_exit(value, fee, txc, clip_sol, entry):
    """Replay `simulate_from_entry` and record HOW the exit state was chosen."""
    entry_i, ent = entry
    if ent is None or not A.covered(value, ent, HORIZON):
        return None
    tape, birth = value["tape"], value["tape"][0]
    clip = clip_sol * C.LAMPORTS
    tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
    if tok <= 0:
        return None
    entry_ts = O.ts_of(ent, birth)
    indices = [i for i in range(entry_i, len(tape))
               if O.ts_of(tape[i], birth) - entry_ts <= HORIZON]
    if not indices:
        return None
    peak = exit_row = mode = None
    for i in indices:
        out, _, _ = C.sell_fill(tape[i]["vsol"], tape[i]["vtok"], tok, fee)
        if peak is None or out > peak:
            peak = out
        elif out <= (1 - TRAIL) * peak:
            exit_row = A.exit_after(
                tape, i, tape[i]["slot"], EXIT_LATENCY_SLOTS)
            mode = "trail_exit_found" if exit_row else "trail_exit_missing"
            break
    if exit_row is None:
        if mode is None:
            mode = "horizon_end"
        exit_row = tape[indices[-1]]
    out, _, _ = C.sell_fill(exit_row["vsol"], exit_row["vtok"], tok, fee)
    return {
        "roi": (out - clip - txc) / C.LAMPORTS / clip_sol,
        "mode": mode,
        "exit_at_completion_ceiling": exit_row["vsol"] >= CEILING_VSOL,
        "exit_is_tape_terminus": indices[-1] == len(tape) - 1,
        "exit_seconds_after_entry": O.ts_of(exit_row, birth) - entry_ts,
    }


def strict_outcome(value, fee, txc, clip_sol, entry):
    """The same rule, but an exit must exist, respect the latency, and be
    a state the curve can still serve. Returns (status, roi)."""
    entry_i, ent = entry
    if ent is None or not A.covered(value, ent, HORIZON):
        return "uncovered", None
    if ent["vsol"] >= CEILING_VSOL:
        return "entry_at_ceiling", None
    tape, birth = value["tape"], value["tape"][0]
    clip = clip_sol * C.LAMPORTS
    tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
    if tok <= 0:
        return "no_fill", None
    entry_ts = O.ts_of(ent, birth)
    indices = [i for i in range(entry_i, len(tape))
               if O.ts_of(tape[i], birth) - entry_ts <= HORIZON]
    if not indices:
        return "no_states", None
    peak = exit_row = None
    for i in indices:
        row = tape[i]
        if row["vsol"] >= CEILING_VSOL:
            return "graduated_before_exit", None
        out, _, _ = C.sell_fill(row["vsol"], row["vtok"], tok, fee)
        if peak is None or out > peak:
            peak = out
        elif out <= (1 - TRAIL) * peak:
            exit_row = A.exit_after(
                tape, i, row["slot"], EXIT_LATENCY_SLOTS)
            if exit_row is None:
                return "exit_state_absent", None
            if exit_row["vsol"] >= CEILING_VSOL:
                return "graduated_before_exit", None
            break
    if exit_row is None:
        last = indices[-1]
        exit_row = A.exit_after(
            tape, last, tape[last]["slot"], EXIT_LATENCY_SLOTS)
        if exit_row is None:
            return "exit_state_absent", None
        if exit_row["vsol"] >= CEILING_VSOL:
            return "graduated_before_exit", None
    out, _, _ = C.sell_fill(exit_row["vsol"], exit_row["vtok"], tok, fee)
    return "ok", (out - clip - txc) / C.LAMPORTS / clip_sol


# --------------------------------------------------------------- statistics

def clustered_ci(pairs, draws=4000, seed=20261004):
    """Window-clustered bootstrap over (window, roi) pairs."""
    by_window = collections.defaultdict(list)
    for window, roi in pairs:
        by_window[window].append(roi)
    windows = sorted(by_window)
    if not windows:
        return None
    rnd = random.Random(seed)
    values = []
    for _ in range(draws):
        sample = []
        for _ in windows:
            sample.extend(by_window[rnd.choice(windows)])
        values.append(sum(sample) / len(sample))
    values.sort()
    return values[int(0.025 * draws)], values[int(0.975 * draws)]


# ------------------------------------------------------------------- reports

def exit_audit(args, data):
    virgin, audit, funding, fee, txc, a_windows, b_windows = data
    top, _bottom = frozen_top(
        virgin, audit, funding, fee, txc, a_windows, args)
    selected = top_funder_mints(virgin, audit, funding, top, b_windows)
    rows = []
    for value in selected.values():
        got = audit_archived_exit(
            value, fee, txc, args.clip,
            R.entry_after_up_to_same_slot_trades(value, SEAT_AHEAD))
        if got:
            rows.append(got)
    total = sum(r["roi"] for r in rows)
    modes = collections.Counter(r["mode"] for r in rows)
    horizon = [r for r in rows if r["mode"] == "horizon_end"]
    ceiling = [r for r in rows if r["exit_at_completion_ceiling"]]
    rest = [r for r in rows if not r["exit_at_completion_ceiling"]]
    seconds = sorted(r["exit_seconds_after_entry"] for r in horizon)
    return {
        "n": len(rows),
        "mean_roi": total / len(rows),
        "mode_counts": dict(modes),
        "horizon_end_at_tape_terminus_share": (
            sum(r["exit_is_tape_terminus"] for r in horizon) / len(horizon)),
        "horizon_end_exit_seconds_median": seconds[len(seconds) // 2],
        "completion_ceiling_rows": len(ceiling),
        "completion_ceiling_profit_share": (
            sum(r["roi"] for r in ceiling) / total),
        "completion_ceiling_max_seconds_after_entry": max(
            r["exit_seconds_after_entry"] for r in ceiling),
        "mean_without_ceiling_rows": sum(r["roi"] for r in rest) / len(rest),
        "mean_with_ceiling_rows_as_total_loss": (
            (sum(r["roi"] for r in rest) - len(ceiling)) / len(rows)),
        "trail_exit_missing_mean": (
            sum(r["roi"] for r in rows if r["mode"] == "trail_exit_missing")
            / max(1, modes["trail_exit_missing"])),
    }


def bundle_report(args, data):
    virgin, audit, funding, fee, txc, a_windows, b_windows = data
    top, _bottom = frozen_top(
        virgin, audit, funding, fee, txc, a_windows, args)
    selected = top_funder_mints(virgin, audit, funding, top, b_windows)
    rows = []
    for key, value in selected.items():
        roi = R.simulate_from_entry(
            value, fee, txc, args.clip,
            R.entry_after_up_to_same_slot_trades(value, SEAT_AHEAD))
        if roi is None:
            continue
        size, _ = fee_cluster(value)
        rows.append({"window": key[0], "mint": key[1], "roi": roi,
                     "cluster": size, "interleaving": interleaving(value)})
    total = sum(r["roi"] for r in rows)
    concentration = {}
    for thresh in CLUSTER_THRESHOLDS:
        sel = [r for r in rows if r["cluster"] >= thresh]
        rest = [r for r in rows if r["cluster"] < thresh]
        concentration[str(thresh)] = {
            "n": len(sel),
            "profit_share": sum(r["roi"] for r in sel) / total,
            "mean": sum(r["roi"] for r in sel) / len(sel) if sel else None,
            "mean_without": (
                sum(r["roi"] for r in rest) / len(rest) if rest else None),
            "n_without": len(rest),
        }
    marked = [r["interleaving"] for r in rows if r["interleaving"]]
    population = []
    for (window, mint), value in virgin.items():
        if window not in b_windows:
            continue
        birth = audit.get((mint, value["tape"][0]["sig"]))
        if not birth or birth["status"] not in D.VERIFIED_BIRTH:
            continue
        edge = funding.get((mint, birth["create_sig"]))
        if not edge or edge.get("status") != "direct_funder_found":
            continue
        funders = edge.get("funders") or []
        if not funders or not funders[0].get("is_signer"):
            continue
        roi = R.simulate_from_entry(
            value, fee, txc, args.clip,
            R.entry_after_up_to_same_slot_trades(value, SEAT_AHEAD))
        if roi is None:
            continue
        size, _ = fee_cluster(value)
        population.append({"roi": roi, "cluster": size})
    base = {}
    for thresh in CLUSTER_THRESHOLDS:
        sel = [r for r in population if r["cluster"] >= thresh]
        rest = [r for r in population if r["cluster"] < thresh]
        base[str(thresh)] = {
            "population_base_rate": len(sel) / len(population),
            "top_funder_rate": (
                sum(r["cluster"] >= thresh for r in rows) / len(rows)),
            "population_mean_with": (
                sum(r["roi"] for r in sel) / len(sel) if sel else None),
            "population_mean_without": (
                sum(r["roi"] for r in rest) / len(rest) if rest else None),
            "n_with": len(sel), "n_without": len(rest),
        }
    return {
        "seat": "up_to_%d_same_slot_ahead" % SEAT_AHEAD,
        "n": len(rows),
        "mean_roi": total / len(rows),
        "profit_concentration_by_cluster": concentration,
        "population_base_rates": base,
        "marked_launches": len(marked),
        "slots_with_an_outsider_inside_the_cluster_run": sum(
            m["outsiders_inside_span"] > 0 for m in marked),
        "cluster_starts_immediately_after_create": sum(
            m["starts_immediately_after_create"] for m in marked),
    }


def seat_report(args, data):
    virgin, _audit, _funding, fee, txc, a_windows, b_windows = data
    seats = {
        "birth_slot_end": lambda v: A.entry_of(v, 0),
        "plus_1_slot": lambda v: A.entry_of(v, 1),
        "plus_2_slots": lambda v: A.entry_of(v, 2),
    }
    out = {}
    for name, entry_fn in seats.items():
        records, statuses = [], collections.Counter()
        for (window, mint), value in virgin.items():
            size, _ = fee_cluster(value)
            status, roi = strict_outcome(
                value, fee, txc, args.clip, entry_fn(value))
            statuses[status] += 1
            if status == "ok":
                records.append({"window": window, "roi": roi,
                                "cluster": size})
        block = {"statuses": dict(statuses)}
        for thresh in CLUSTER_THRESHOLDS:
            for label, windows in (("a", a_windows), ("b", b_windows),
                                   ("pooled", a_windows | b_windows)):
                sel = [r for r in records
                       if r["window"] in windows and r["cluster"] >= thresh]
                rest = [r for r in records
                        if r["window"] in windows and r["cluster"] < thresh]
                if len(sel) < 10:
                    continue
                block["cluster_%d_%s" % (thresh, label)] = {
                    "n": len(sel),
                    "mean": sum(r["roi"] for r in sel) / len(sel),
                    "clustered_ci": clustered_ci(
                        [(r["window"], r["roi"]) for r in sel],
                        draws=args.draws),
                    "n_without": len(rest),
                    "mean_without": (
                        sum(r["roi"] for r in rest) / len(rest)
                        if rest else None),
                }
        out[name] = block
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    ap.add_argument("--birth-audit", default="data/pumpbirthaudit.jsonl")
    ap.add_argument("--funding", default="data/pumpfunding.jsonl")
    ap.add_argument("--json", default="data/pumpbundleseat.json")
    ap.add_argument("--clip", type=float, default=0.05)
    ap.add_argument("--min-a", type=int, default=3)
    ap.add_argument("--top-share", type=float, default=0.2)
    ap.add_argument("--draws", type=int, default=4000)
    ap.add_argument("--exit-audit", action="store_true")
    ap.add_argument("--bundle", action="store_true")
    ap.add_argument("--seats", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if not (args.exit_audit or args.bundle or args.seats or args.all):
        ap.error("pass --exit-audit, --bundle, --seats, or --all")

    data = load(args)
    out = {"preregistration": (
        "preregistrations/pumpfun_graduation_exit_20261004.md"),
        "cluster_definition": (
            "three or more distinct wallets buying in the birth slot at one "
            "identical priority-fee value"),
        "capital_enabled": False,
        "transaction_build_path": False,
        "transaction_sign_path": False,
        "transaction_submit_path": False}
    if args.exit_audit or args.all:
        out["exit_audit"] = exit_audit(args, data)
    if args.bundle or args.all:
        out["bundle"] = bundle_report(args, data)
    if args.seats or args.all:
        out["reachable_seats"] = seat_report(args, data)
    with open(args.json, "w") as handle:
        json.dump(out, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(json.dumps({k: v for k, v in out.items()
                      if k in ("exit_audit", "bundle")},
                     indent=1, sort_keys=True))
    for name, block in (out.get("reachable_seats") or {}).items():
        print("\n%s  %s" % (name, block["statuses"]))
        for key, cell in block.items():
            if key == "statuses":
                continue
            print("  %-18s n=%4d mean %+.4f ci [%+.4f,%+.4f]  without n=%4d "
                  "mean %+.4f"
                  % (key, cell["n"], cell["mean"], cell["clustered_ci"][0],
                     cell["clustered_ci"][1], cell["n_without"],
                     cell["mean_without"]))


if __name__ == "__main__":
    main()
