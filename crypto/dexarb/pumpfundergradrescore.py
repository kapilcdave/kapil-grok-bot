"""Re-score the one-transaction-ahead funder seat with honest exits and seats.

Two corrections, each applied on its own and then together:

`pool`   substitutes the realized post-migration PumpSwap exit for every row
         whose archived exit was the bonding-curve completion state. That state
         is the last the curve ever has, so the archive's own 2-slot exit
         latency has nothing to land on.

`bundle` drops rows whose birth slot contains a coordinated multi-wallet buy
         cluster -- three or more distinct wallets buying at one identical
         priority fee. The modelled seat sits between the create and that
         cluster's first buy, which is a position inside the launcher's own
         atomic bundle rather than a seat an outsider can race for.

Prereg: preregistrations/pumpfun_graduation_exit_20261004.md

    python3 pumpfundergradrescore.py
"""
import argparse
import collections
import json

import pumpcausal as A
import pumpcurve as C
import pumpdeployer as D
import pumpfundinganalyze as F
import pumpfunderreach as R
import pumporacle as O

SEAT_AHEAD = 1
CLUSTER_WALLETS = 3


def fee_cluster(value):
    """Largest set of distinct wallets sharing one identical priority fee."""
    tape = value["tape"]
    birth = tape[0]
    by_fee = collections.defaultdict(set)
    for row in tape:
        if row["slot"] == birth["slot"] and row["buy"]:
            by_fee[row["fee"]].add(row["user"])
    return max((len(w) for w in by_fee.values()), default=0)


def build(args):
    raw, metas = O.load(args.tape)
    virgin, _, _ = O.build_mints(raw, metas, C.discover_initial_states(raw))
    audit = F.latest(args.birth_audit, ("mint", "first_sig"))
    funding = F.latest(args.funding, ("mint", "create_sig"))
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2
    a_windows, b_windows = D.split_windows(virgin)

    zero = R.funded_rows(
        virgin, audit, funding, fee, txc, args.clip,
        lambda v: R.entry_after_up_to_same_slot_trades(v, 0))
    _state, top, bottom, _scores = R.fixed_groups(zero, a_windows, args)
    top, bottom = set(top), set(bottom)

    rows = R.funded_rows(
        virgin, audit, funding, fee, txc, args.clip,
        lambda v: R.entry_after_up_to_same_slot_trades(v, SEAT_AHEAD))
    clusters = {mint: fee_cluster(v) for (_w, mint), v in virgin.items()}
    for row in rows:
        row["fee_cluster_wallets"] = clusters.get(row["mint"], 0)
    return rows, top, bottom, b_windows


def apply_pool(rows, scored, strict):
    """Replace the archived ROI on completion-exit rows with the pool exit."""
    by_mint = {r["mint"]: r for r in scored}
    out, swapped, dropped = [], 0, 0
    for row in rows:
        hit = by_mint.get(row["mint"])
        if not hit:
            out.append(row)
            continue
        if hit["outcome"] == "fetch_failed":
            dropped += 1
            continue
        roi = -1.0 if (strict and hit["outcome"] == "pool_absent_in_horizon") \
            else hit["roi"]
        if roi is None:
            dropped += 1
            continue
        out.append({**row, "roi": roi})
        swapped += 1
    return out, swapped, dropped


def seat(rows, b_windows, top, bottom, draws):
    population = [r for r in rows if r["window"] in b_windows]
    top_rows = [r for r in population if r["funder"] in top]
    bottom_rows = [r for r in population if r["funder"] in bottom]
    return {
        "top_b": D.describe(top_rows, draws=draws),
        "population_b": D.describe(population, draws=draws),
        "top_vs_population_ci": (
            D.clustered_contrast(top_rows, population, draws=draws)
            if top_rows and population else None),
        "top_vs_bottom_ci": (
            D.clustered_contrast(top_rows, bottom_rows, draws=draws)
            if top_rows and bottom_rows else None),
    }


def passes(block):
    top = block.get("top_b") or {}
    pop = block.get("top_vs_population_ci")
    bot = block.get("top_vs_bottom_ci")
    return bool(
        top.get("n", 0) >= 50 and top.get("windows", 0) >= 6
        and top.get("mean_roi", 0) > 0
        and top.get("clustered_ci") and top["clustered_ci"][0] > 0
        and pop and pop[0] > 0 and bot and bot[0] > 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    ap.add_argument("--birth-audit", default="data/pumpbirthaudit.jsonl")
    ap.add_argument("--funding", default="data/pumpfunding.jsonl")
    ap.add_argument("--gradexit", default="data/pumpgradexit.json")
    ap.add_argument("--json", default="data/pumpfundergradrescore.json")
    ap.add_argument("--clip", type=float, default=0.05)
    ap.add_argument("--min-a", type=int, default=3)
    ap.add_argument("--top-share", type=float, default=0.2)
    ap.add_argument("--draws", type=int, default=4000)
    args = ap.parse_args()

    rows, top, bottom, b_windows = build(args)
    grad = json.load(open(args.gradexit))
    scored = grad["scored"]

    arms = {}
    arms["archive"] = seat(rows, b_windows, top, bottom, args.draws)

    pool_rows, swapped, dropped = apply_pool(rows, scored, strict=False)
    arms["pool_exit"] = seat(pool_rows, b_windows, top, bottom, args.draws)
    arms["pool_exit"]["rows_swapped"] = swapped
    arms["pool_exit"]["rows_dropped_unfetched"] = dropped

    strict_rows, s_swapped, s_dropped = apply_pool(rows, scored, strict=True)
    arms["pool_exit_total_loss_on_absent"] = seat(
        strict_rows, b_windows, top, bottom, args.draws)
    arms["pool_exit_total_loss_on_absent"]["rows_swapped"] = s_swapped

    no_bundle = [r for r in rows
                 if r["fee_cluster_wallets"] < CLUSTER_WALLETS]
    arms["no_bundle_cluster"] = seat(
        no_bundle, b_windows, top, bottom, args.draws)

    both = [r for r in pool_rows
            if r["fee_cluster_wallets"] < CLUSTER_WALLETS]
    arms["pool_exit_and_no_bundle"] = seat(
        both, b_windows, top, bottom, args.draws)

    # How the profit sits across the bundle cut, in the whole B population.
    pop = [r for r in rows if r["window"] in b_windows]
    cut = {}
    for thresh in (2, 3, 4):
        sel = [r for r in pop if r["fee_cluster_wallets"] >= thresh]
        rest = [r for r in pop if r["fee_cluster_wallets"] < thresh]
        cut[str(thresh)] = {
            "population_share": len(sel) / len(pop),
            "population_mean_with": (
                sum(r["roi"] for r in sel) / len(sel) if sel else None),
            "population_mean_without": (
                sum(r["roi"] for r in rest) / len(rest) if rest else None),
        }

    out = {
        "preregistration": (
            "preregistrations/pumpfun_graduation_exit_20261004.md"),
        "seat": "up_to_%d_same_slot_ahead" % SEAT_AHEAD,
        "cluster_wallets_threshold": CLUSTER_WALLETS,
        "arms": arms,
        "bundle_cut_in_population": cut,
        "gates": {name: passes(block) for name, block in arms.items()},
        # The graduation exit turned out to BE realizable: substituting
        # measured pool exits costs ~1.7pp. What fails is the seat, so the
        # verdict is named for the term that actually binds.
        "graduation_exit_realizable": passes(arms["pool_exit"]),
        "exit_correction_cost": (
            arms["archive"]["top_b"]["mean_roi"]
            - arms["pool_exit"]["top_b"]["mean_roi"]),
        "verdict": (
            "ZERO-SLOT-FUNDER-SHADOW-CANDIDATE"
            if passes(arms["pool_exit"]) and passes(arms["no_bundle_cluster"])
            else "BUNDLE-SEAT-UNREACHABLE"),
        "capital_enabled": False,
        "transaction_build_path": False,
        "transaction_sign_path": False,
        "transaction_submit_path": False,
    }
    with open(args.json, "w") as handle:
        json.dump(out, handle, indent=1, sort_keys=True)
        handle.write("\n")
    for name, block in arms.items():
        top_b = block["top_b"] or {}
        print("%-34s n=%3s mean=%8s ci=%s"
              % (name, top_b.get("n"),
                 ("%.4f" % top_b["mean_roi"]) if top_b else "n/a",
                 top_b.get("clustered_ci")))
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
