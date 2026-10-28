"""Market-wide regime gate for the earliest reachable pump.fun strategy.

Preregistered in preregistrations/pumpfun_market_regime_20260929.md.
"""

import argparse
import bisect
import collections
import heapq
import json
import random
import sys

import pumpcausal as A
import pumpcurve as C
import pumporacle as O
import pumpselect as S


HISTORY_SLOTS = 750


class WindowIndex:
    def __init__(self, rows, birth_slots):
        ordered = sorted(rows, key=lambda row: row["slot"])
        self.slots = [row["slot"] for row in ordered]
        self.buy = [0.0]
        self.sell = [0.0]
        for row in ordered:
            sol = row["sol"] / C.LAMPORTS
            self.buy.append(self.buy[-1] + (sol if row["buy"] else 0.0))
            self.sell.append(self.sell[-1] + (sol if not row["buy"] else 0.0))
        self.birth_slots = sorted(birth_slots)

    def snapshot(self, slot, width):
        lo = bisect.bisect_left(self.slots, slot - width)
        hi = bisect.bisect_left(self.slots, slot)
        blo = bisect.bisect_left(self.birth_slots, slot - width)
        bhi = bisect.bisect_left(self.birth_slots, slot)
        buy = self.buy[hi] - self.buy[lo]
        sell = self.sell[hi] - self.sell[lo]
        return {
            "trades": hi - lo,
            "births": bhi - blo,
            "buy": buy,
            "sell": sell,
        }


def reference_outcome(v, fee, txc, clip_sol=1.0, L_in=2, L_out=2,
                      horizon=300, trail=0.15):
    tape, birth = v["tape"], v["tape"][0]
    ei, ent = A.entry_of(v, L_in)
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
    pnl = (out - clip - txc) / C.LAMPORTS / clip_sol
    return {"pnl": pnl, "exit_slot": ex["slot"], "birth_slot": birth["slot"]}


def regime_features(index, slot, resolved):
    ten = index.snapshot(slot, 25)
    minute = index.snapshot(slot, 150)
    five = index.snapshot(slot, 750)
    recent5 = resolved[-5:]
    recent20 = resolved[-20:]
    if len(recent20) < 20:
        return None
    minute_volume = minute["buy"] + minute["sell"]
    return {
        "mint_rate_10s": ten["births"] / 10.0,
        "mint_rate_60s": minute["births"] / 60.0,
        "mint_rate_300s": five["births"] / 300.0,
        "trade_rate_10s": ten["trades"] / 10.0,
        "trade_rate_60s": minute["trades"] / 60.0,
        "buy_sol_10s": ten["buy"],
        "buy_sol_60s": minute["buy"],
        "net_sol_10s": ten["buy"] - ten["sell"],
        "net_sol_60s": minute["buy"] - minute["sell"],
        "buy_share_60s": minute["buy"] / minute_volume if minute_volume else 0.5,
        "recent_mean_5": sum(row["pnl"] for row in recent5) / len(recent5),
        "recent_mean_20": sum(row["pnl"] for row in recent20) / len(recent20),
        "recent_win_20": sum(row["pnl"] > 0 for row in recent20) / len(recent20),
    }


def build_rows(rows, metas, virgin, outcomes):
    by_window_rows = collections.defaultdict(list)
    by_window_births = collections.defaultdict(list)
    by_window_outcomes = collections.defaultdict(list)
    for row in rows:
        by_window_rows[row["w"]].append(row)
    for key, v in virgin.items():
        window, _ = key
        by_window_births[window].append(v["tape"][0]["slot"])
    for key, outcome in outcomes.items():
        window, _ = key
        by_window_outcomes[window].append((outcome["birth_slot"], key, outcome))

    meta_by_window = {meta["window"]: meta for meta in metas}
    result = []
    for window, records in by_window_outcomes.items():
        records.sort()
        index = WindowIndex(by_window_rows[window], by_window_births[window])
        lo_slot = meta_by_window[window]["lo_slot"]
        pending = []
        resolved = []
        for birth_slot, key, outcome in records:
            while pending and pending[0][0] < birth_slot:
                resolved.append(heapq.heappop(pending)[3])
            if birth_slot - lo_slot < HISTORY_SLOTS:
                heapq.heappush(pending, (outcome["exit_slot"], birth_slot,
                                         key[1], outcome))
                continue
            features = regime_features(index, birth_slot, resolved)
            if features is None:
                heapq.heappush(pending, (outcome["exit_slot"], birth_slot,
                                         key[1], outcome))
                continue
            result.append({"window": window, "key": key, "features": features,
                           "pnl": outcome["pnl"]})
            heapq.heappush(pending, (outcome["exit_slot"], birth_slot,
                                     key[1], outcome))
    return result


def shuffled_null(train, test, keys, draws=500, seed=20260929):
    rnd = random.Random(seed)
    train_features = [row[0] for row in train]
    test_features = [row[0] for row in test]
    train_pnl = [row[1] for row in train]
    test_pnl = [row[1] for row in test]
    values = []
    for _ in range(draws):
        shuffled_train = train_pnl[:]
        shuffled_test = test_pnl[:]
        rnd.shuffle(shuffled_train)
        rnd.shuffle(shuffled_test)
        result = S.wf_screen(list(zip(train_features, shuffled_train)),
                             list(zip(test_features, shuffled_test)), keys)
        if result is not None:
            values.append(result["test_mean"])
    values.sort()
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--json", default="data/pumpregime.json")
    parser.add_argument("--draws", type=int, default=500)
    args = parser.parse_args()

    rows, metas = O.load(args.tape)
    if not rows:
        sys.exit("no completed windows")
    states = C.discover_initial_states(rows)
    virgin, _, _ = O.build_mints(rows, metas, states)
    fee = C.FEE_BPS / 1e4
    txc = O.txcost(virgin) * 2
    outcomes = {}
    for key, v in virgin.items():
        outcome = reference_outcome(v, fee, txc)
        if outcome is not None:
            outcomes[key] = outcome

    dataset = build_rows(rows, metas, virgin, outcomes)
    ordered_windows = [meta["window"] for meta in sorted(metas, key=lambda m: m["lo_slot"])]
    split = len(ordered_windows) // 2
    train_windows = set(ordered_windows[:split])
    test_windows = set(ordered_windows[split:])
    train = [(row["features"], row["pnl"]) for row in dataset
             if row["window"] in train_windows]
    test = [(row["features"], row["pnl"]) for row in dataset
            if row["window"] in test_windows]
    if not train or not test:
        sys.exit("empty chronological split")
    keys = sorted(train[0][0])
    selected = S.wf_screen(train, test, keys)
    if selected is None:
        sys.exit("no selectable cell")
    null = shuffled_null(train, test, keys, args.draws)
    observed = selected["test_mean"]
    p_null = sum(value >= observed for value in null) / len(null)
    ci_lo, ci_hi = selected["test_ci"]
    passed = (selected["n_test"] >= 100 and observed > 0 and ci_lo > 0
              and p_null <= 0.05)
    output = {
        "n_total": len(dataset),
        "n_train": len(train),
        "n_test": len(test),
        "train_windows": sorted(train_windows),
        "test_windows": sorted(test_windows),
        "pooled_train": sum(p for _, p in train) / len(train),
        "pooled_test": sum(p for _, p in test) / len(test),
        "selected": selected,
        "null_draws": len(null),
        "null_p95": null[int(0.95 * len(null))],
        "null_p_value": p_null,
        "passed": passed,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.json, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
