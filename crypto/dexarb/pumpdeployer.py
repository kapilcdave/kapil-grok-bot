"""Out-of-sample persistence of pump.fun deployer launch quality.

Prereg: preregistrations/pumpfun_deployer_persistence_20261002.md

The deployer is the first TradeEvent's user.  Its score uses only earlier
windows, while the outcome is the already-frozen reachable same-slot follower
rule from pumpsameentry.py.

    python3 pumpdeployer.py
"""
import argparse
import collections
import json
import random
import sys

import pumpcurve as C
import pumporacle as O
import pumpsameentry as E


def split_windows(virgin):
    first = {}
    for (window, _mint), v in virgin.items():
        slot = v["tape"][0]["slot"]
        first[window] = min(first.get(window, slot), slot)
    order = sorted(first, key=lambda w: first[w])
    half = len(order) // 2
    return set(order[:half]), set(order[half:])


VERIFIED_BIRTH = {"create_in_first_trade_tx", "create_precedes_first_trade"}


def load_birth_audit(path):
    if not path:
        return None
    latest = {}
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            latest[(row["mint"], row["first_sig"])] = row
    return latest


def outcome_rows(virgin, fee, txc, clip_sol, birth_audit=None):
    rows = []
    for (window, mint), v in virgin.items():
        birth = v["tape"][0]
        audit = None
        if birth_audit is not None:
            audit = birth_audit.get((mint, birth["sig"]))
            if audit is None or audit["status"] not in VERIFIED_BIRTH:
                continue
        roi = E.simulate(v, fee, txc, clip_sol=clip_sol)
        if roi is None:
            continue
        rows.append({
            "window": window,
            "mint": mint,
            "slot": birth["slot"],
            "deployer": audit["creator"] if audit else birth["user"],
            "roi": roi,
        })
    return rows


def state_by_deployer(rows, a_windows):
    state = collections.defaultdict(lambda: {"a": [], "b": []})
    for row in rows:
        state[row["deployer"]]["a" if row["window"] in a_windows else "b"].append(row)
    return state


def mean(rows):
    return sum(r["roi"] for r in rows) / len(rows)


def rankdata(values):
    """Average ranks for ties, zero based."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + j - 1) / 2.0
        for k in range(i, j):
            ranks[order[k]] = rank
        i = j
    return ranks


def pearson(xs, ys):
    if len(xs) < 3:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (vx * vy) ** 0.5


def spearman(xs, ys):
    return pearson(rankdata(xs), rankdata(ys))


def select(state, min_a, top_share):
    eligible = [(u, mean(v["a"])) for u, v in state.items() if len(v["a"]) >= min_a]
    eligible.sort(key=lambda x: x[1])
    take = max(1, round(top_share * len(eligible)))
    bottom = {u for u, _ in eligible[:take]}
    top = {u for u, _ in eligible[-take:]}
    return top, bottom, dict(eligible)


def b_rows(state, users):
    out = []
    for user in users:
        out.extend(state[user]["b"])
    return out


def grouped_means(rows):
    by_window = collections.defaultdict(list)
    for row in rows:
        by_window[row["window"]].append(row["roi"])
    return by_window


def clustered_mean_ci(rows, draws=2000, seed=20261002):
    by_window = grouped_means(rows)
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


def clustered_contrast(left, right, draws=2000, seed=20261002):
    lwin = grouped_means(left)
    rwin = grouped_means(right)
    windows = sorted(set(lwin) | set(rwin))
    usable = [w for w in windows if lwin.get(w) and rwin.get(w)]
    if not usable:
        return None
    rnd = random.Random(seed)
    values = []
    for _ in range(draws):
        ls, rs = [], []
        for _ in usable:
            w = rnd.choice(usable)
            ls.extend(lwin[w])
            rs.extend(rwin[w])
        values.append(sum(ls) / len(ls) - sum(rs) / len(rs))
    values.sort()
    return values[int(0.025 * draws)], values[int(0.975 * draws)]


def describe(rows, draws=2000):
    if not rows:
        return None
    values = sorted(r["roi"] for r in rows)
    return {
        "n": len(rows),
        "windows": len({r["window"] for r in rows}),
        "deployers": len({r["deployer"] for r in rows}),
        "mean_roi": sum(values) / len(values),
        "median_roi": C.median(values),
        "share_positive": sum(v > 0 for v in values) / len(values),
        "clustered_ci": clustered_mean_ci(rows, draws=draws),
    }


def rank_persistence(state, scores, draws, seed):
    users = [u for u in scores if state[u]["b"]]
    av = [scores[u] for u in users]
    bv = [mean(state[u]["b"]) for u in users]
    observed = spearman(av, bv)
    rnd = random.Random(seed)
    null = []
    work = list(av)
    for _ in range(draws):
        rnd.shuffle(work)
        rho = spearman(work, bv)
        if rho is not None:
            null.append(rho)
    p = ((sum(x >= observed for x in null) + 1) / (len(null) + 1)
         if observed is not None else None)
    return {
        "n_deployers": len(users),
        "rho": observed,
        "one_sided_permutation_p": p,
        "null_draws": len(null),
    }


def selection_null(state, scores, population_b, top_share, draws, seed,
                   observed=None):
    users = list(scores)
    labels = [scores[u] for u in users]
    take = max(1, round(top_share * len(users)))
    pop_mean = mean(population_b)
    rnd = random.Random(seed)
    values = []
    work = list(labels)
    for _ in range(draws):
        rnd.shuffle(work)
        ranked = sorted(zip(users, work), key=lambda x: x[1])
        chosen = {u for u, _ in ranked[-take:]}
        selected = b_rows(state, chosen)
        if selected:
            values.append(mean(selected) - pop_mean)
    values.sort()
    if not values:
        return {
            "draws": 0,
            "p50": None,
            "p95": None,
            "max": None,
            "one_sided_p": None,
        }
    return {
        "draws": len(values),
        "p50": values[len(values) // 2],
        "p95": values[int(0.95 * len(values))],
        "max": values[-1],
        "one_sided_p": (
            (sum(x >= observed for x in values) + 1) / (len(values) + 1)
            if observed is not None else None
        ),
    }


def run_stratum(state, population_b, min_a, top_share, ci_draws, null_draws, seed):
    top, bottom, scores = select(state, min_a, top_share)
    top_b = b_rows(state, top)
    bottom_b = b_rows(state, bottom)
    observed = mean(top_b) - mean(population_b) if top_b else None
    return {
        "min_a": min_a,
        "top_share": top_share,
        "n_eligible_deployers": len(scores),
        "n_selected_deployers": len(top),
        "selected_top_users": sorted(top),
        "selected_bottom_users": sorted(bottom),
        "top_b": describe(top_b, draws=ci_draws),
        "bottom_b": describe(bottom_b, draws=ci_draws),
        "top_vs_population_ci": (
            clustered_contrast(top_b, population_b, draws=ci_draws, seed=seed)
            if top_b else None
        ),
        "top_vs_bottom_ci": (
            clustered_contrast(top_b, bottom_b, draws=ci_draws, seed=seed)
            if top_b and bottom_b else None
        ),
        "rank_persistence": rank_persistence(state, scores, null_draws, seed),
        "observed_top_vs_population": observed,
        "selection_null": selection_null(
            state, scores, population_b, top_share, null_draws, seed, observed
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--json", default="data/pumpdeployer.json")
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
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2
    birth_audit = load_birth_audit(args.birth_audit)
    rows = outcome_rows(virgin, fee, txc, args.clip, birth_audit)
    a_windows, b_windows = split_windows(virgin)
    state = state_by_deployer(rows, a_windows)
    population_b = [r for r in rows if r["window"] in b_windows]

    primary = run_stratum(
        state, population_b, args.min_a, args.top_share,
        args.draws, args.null_draws, args.seed,
    )
    robustness = {}
    for min_a in (1, 2, 5):
        robustness[str(min_a)] = run_stratum(
            state, population_b, min_a, args.top_share,
            args.draws, args.null_draws, args.seed + min_a,
        )

    top = primary["top_b"]
    top_pop = primary["top_vs_population_ci"]
    top_bottom = primary["top_vs_bottom_ci"]
    rank = primary["rank_persistence"]
    null = primary["selection_null"]
    gates = {
        "G1_power": bool(top and top["n"] >= 50 and top["windows"] >= 6),
        "G2_sign": bool(
            top and top["mean_roi"] > 0 and top["clustered_ci"][0] > 0
        ),
        "G3_attribution": bool(
            top_pop and top_bottom and top_pop[0] > 0 and top_bottom[0] > 0
        ),
        "G4_rank_persistence": bool(
            rank["rho"] is not None and rank["rho"] > 0
            and rank["one_sided_permutation_p"] < 0.05
        ),
        "G5_selection_null": bool(
            primary["observed_top_vs_population"] is not None
            and null["p95"] is not None
            and primary["observed_top_vs_population"] > null["p95"]
        ),
    }
    verdict = (
        "DEPLOYER-HISTORY-PERSISTS"
        if all(gates.values()) else "NO-DIRECT-DEPLOYER-PERSISTENCE"
    )
    output = {
        "parameters": {
            "clip_sol": args.clip,
            "fee_bps": C.FEE_BPS,
            "trail": 0.15,
            "horizon_seconds": 300,
            "exit_latency_slots": 2,
            "min_a": args.min_a,
            "top_share": args.top_share,
            "birth_audit": args.birth_audit,
        },
        "birth_filter": {
            "enabled": birth_audit is not None,
            "audited_candidates": len(birth_audit) if birth_audit else None,
            "verified_eligible_rows": len(rows),
        },
        "a_windows": sorted(a_windows),
        "b_windows": sorted(b_windows),
        "n_rows": len(rows),
        "n_deployers": len(state),
        "population_b": describe(population_b, draws=args.draws),
        "primary": primary,
        "robustness": robustness,
        "gates": gates,
        "verdict": verdict,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
