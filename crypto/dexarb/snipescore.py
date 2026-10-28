"""Score a `snipepaper` ledger against the gates in the preregistration.

The headline is NOT the level of any arm. Section 1 of
`preregistrations/pumpfun_gliner_decide_paper_20261005.md` says the level is
expected to be negative, because two closures in this tree are bounds (Doob caps
any path exit on the curve at break-even; the paying birth slot is authorship).
The headline is G4: `gate_decide` minus its own accept vector ROLLED, which is
selection at matched turnover. The simpler paired difference `gate_decide - gate`
is NOT the headline and this file labels it as confounded, because an arm that
declines a row contributes 0.0 on it, so against a negative-level arm any
constant "skip" wins. Running this scorer on a dummy ledger with no decider
attached scored that artefact at +27.67% before a single real row existed.

Three things this file refuses to do, each because it has cost this branch a
verdict before:

* It will not report a headline when the decider was unavailable on a material
  share of rows. An absent instrument is void, not conservative.
* It will not treat the roll null as "random": the null is the decider's own
  accept vector ROLLED, so the accept count -- and therefore the fee bill -- is
  identical to the real arm's (`a-null-must-hold-turnover-fixed`).
* It will not bootstrap rows independently. Launches share a regime, so the
  resample unit is the UTC hour block (`a-null-bar-scales-with-the-masks-block`).

    python3 snipescore.py --ledger ledger.jsonl
    python3 snipescore.py --ledger ledger.jsonl --json out.json
    python3 snipescore.py --ledger ledger.jsonl --arm gate_wallet_decide \
        --econ marks_reach          # the wallet-history arm, at its real seat

`--econ marks_reach` scores every arm (gate included) at the entry the wallet
verdict could actually reach, so the comparison stays paired on one seat.
"""
import argparse
import collections
import datetime as dt
import json
import os
import random
import statistics
import sys

import snipewallet as W

PRIMARY_HORIZON = "60"
HORIZONS = ("15", "30", "60", "120", "300", "600")
# Below this share of rows carrying a live decider verdict, there is no headline.
MIN_AVAILABLE_SHARE = 0.80
# D2: if a one-line rule reproduces the decider's accept set at or above this
# agreement, the readout is that rule and not the model.
D2_AGREEMENT = 0.90


def load(paths):
    """Rows from one or many ledgers.

    Many, because the feed rotates its tape every 24 h and the gate passes only
    **0.57%** of launches (6 of 1,045, measured 2026-10-05 on the live run), so at
    ~33 launches/min the preregistered 300 rows takes ~27 h and necessarily spans
    a rotation. Scoring one ledger would silently score half the run -- the
    screen's pass rate caps the sample before throughput does
    (`a-screen-pass-rate-caps-the-sample-before-throughput-does`).
    """
    if isinstance(paths, str):
        paths = [paths]
    rows = []
    for path in paths:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # One row per mint: a finalised row supersedes the gate-reject row written
    # earlier for the same candidate.
    best = {}
    for row in rows:
        key = row.get("mint")
        if key is None:
            continue
        prior = best.get(key)
        if prior is None or ("marks" in row and "marks" not in prior):
            best[key] = row
    return sorted(best.values(), key=lambda r: r.get("created_ts") or 0)


def hour_block(row):
    ts = row.get("created_ts") or 0
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H")


ARMS = {
    # arm -> the row field saying whether its decider actually answered
    "gate_decide": "decider_available",
    "gate_wallet_decide": "wallet_decider_available",
}
ECON = {"marks": "entry", "marks_reach": "entry_reach"}


def ret_at(row, horizon, econ="marks"):
    mark = (row.get(econ) or {}).get(horizon)
    return None if mark is None else mark["ret"]


def arm_returns(rows, accepts, horizon, econ="marks"):
    """Per-candidate return for an arm. A row the arm declines contributes 0.0 --
    it is a candidate that cost nothing, not a row that does not exist.

    Including declines is what makes the arms comparable: two arms that trade
    different numbers of rows cannot be compared on per-TRADE return without the
    comparison becoming a turnover choice.
    """
    out = []
    for row, accept in zip(rows, accepts):
        if not accept:
            out.append(0.0)
            continue
        r = ret_at(row, horizon, econ)
        out.append(0.0 if r is None else r)
    return out


def block_bootstrap(values, blocks, n=10000, seed=20261005):
    """Mean with a 95% CI, resampling whole UTC-hour blocks with replacement."""
    if not values:
        return None
    grouped = collections.defaultdict(list)
    for value, block in zip(values, blocks):
        grouped[block].append(value)
    keys = sorted(grouped)
    if len(keys) < 2:
        # One block is one observation. A CI over it would be a fiction.
        return {"mean": statistics.fmean(values), "lo": None, "hi": None,
                "n": len(values), "blocks": len(keys),
                "note": "one block; no CI is computable"}
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        pool = []
        for _ in keys:
            pool.extend(grouped[keys[rng.randrange(len(keys))]])
        if pool:
            means.append(statistics.fmean(pool))
    means.sort()
    return {"mean": statistics.fmean(values),
            "lo": means[int(0.025 * len(means))],
            "hi": means[int(0.975 * len(means))],
            "n": len(values), "blocks": len(keys)}


def roll(accepts, k):
    """The accept vector rotated by k. Turnover is preserved EXACTLY: the same
    number of accepts, so the same fee bill. Never a fresh random draw."""
    if not accepts:
        return []
    k %= len(accepts)
    return accepts[-k:] + accepts[:-k] if k else list(accepts)


def agreement(a, b):
    if not a:
        return None
    return sum(1 for x, y in zip(a, b) if bool(x) == bool(y)) / len(a)


def d2_one_line_rules(rows, arm="gate_decide"):
    """D2. Can a single number reproduce the decider's accept set?

    This is the check that voided the last benchmark on this model family: a
    14-feature arm turned out to be `spread_ticks >= 4` and nothing else
    (`the-gliner-gates-benchmark-is-a-one-line-width-gate`). Thresholds are swept
    and the BEST agreement is reported, because the question is whether such a
    rule exists at all, not whether a particular one was guessed.
    """
    truth = [bool((r.get("arms") or {}).get(arm)) for r in rows]
    # A degenerate accept set makes D2 meaningless: if nothing is accepted, the
    # all-reject one-liner agrees 100% and the check "passes" on a void arm.
    if len(set(truth)) < 2:
        return {"per_feature": {}, "reducible": None, "best": None,
                "note": f"accept set is degenerate ({sum(truth)}/{len(truth)} "
                        "accepted); D2 is not computable"}
    features = {
        "desc_chars": [r.get("desc_chars") or 0 for r in rows],
        "mcap_sol": [(r.get("metrics") or {}).get("mcap_sol") or 0 for r in rows],
        "net_sol": [(r.get("metrics") or {}).get("net_sol") or 0 for r in rows],
        "trades": [(r.get("metrics") or {}).get("trades") or 0 for r in rows],
        "uniq_wallets": [(r.get("metrics") or {}).get("uniq_wallets") or 0
                         for r in rows],
        "text_thin": [1 if r.get("text_thin") else 0 for r in rows],
    }
    if arm == "gate_wallet_decide":
        # A text model on rendered numbers has tied a logistic before; if one
        # wallet number reproduces the verdicts, the readout is that number.
        for name in W.D2_FEATURES:
            features[name] = [(r.get("wallet") or {}).get(name) or 0
                              for r in rows]
    out = {}
    for name, values in features.items():
        best = None
        for threshold in sorted(set(values)):
            for sense in (">=", "<"):
                pred = [(v >= threshold) if sense == ">=" else (v < threshold)
                        for v in values]
                score = agreement(truth, pred)
                if score is not None and (best is None or score > best["agreement"]):
                    best = {"rule": f"{name} {sense} {threshold}",
                            "agreement": score}
        out[name] = best
    reducible = [v for v in out.values() if v and v["agreement"] >= D2_AGREEMENT]
    return {"per_feature": out, "reducible": bool(reducible),
            "best": max((v for v in out.values() if v),
                        key=lambda v: v["agreement"], default=None)}


def summarise(rows, horizon, label, seed=20261005, arm="gate_decide",
              econ="marks"):
    traded = [r for r in rows if r.get(ARMS[arm])]
    accepts_gate = [True] * len(rows)
    accepts_dec = [bool(r.get("arms", {}).get(arm)) for r in rows]
    blocks = [hour_block(r) for r in rows]
    gate = arm_returns(rows, accepts_gate, horizon, econ)
    dec = arm_returns(rows, accepts_dec, horizon, econ)
    paired = [d - g for d, g in zip(dec, gate)]
    out = {
        "label": label, "arm": arm, "econ": econ,
        "horizon_s": int(horizon), "n_candidates": len(rows),
        "n_decider_available": len(traded),
        "available_share": (len(traded) / len(rows)) if rows else None,
        "n_accept_gate": sum(accepts_gate), "n_accept_decide": sum(accepts_dec),
        "gate": block_bootstrap(gate, blocks, seed=seed),
        "gate_decide": block_bootstrap(dec, blocks, seed=seed),
        "paired_decide_minus_gate": block_bootstrap(paired, blocks, seed=seed),
    }
    # G4: the roll null, at several offsets so one lucky rotation cannot pass it.
    nulls = []
    for k in (1, 3, 7, 11, 23, 37):
        if len(rows) <= k:
            continue
        rolled = roll(accepts_dec, k)
        if sum(rolled) != sum(accepts_dec):
            continue                        # turnover must be identical
        rolled_ret = arm_returns(rows, rolled, horizon, econ)
        nulls.append({"k": k,
                      "arm": block_bootstrap(rolled_ret, blocks, seed=seed),
                      "vs_real": block_bootstrap(
                          [d - n for d, n in zip(dec, rolled_ret)], blocks,
                          seed=seed)})
    out["roll_nulls"] = nulls
    oracle = [r["oracle"]["ret"] for r in rows
              if r.get("oracle") and r["oracle"].get("ret") is not None]
    out["oracle_bound"] = block_bootstrap(oracle, [hour_block(r) for r in rows
                                                   if r.get("oracle")],
                                          seed=seed)
    return out


def fmt_ci(d, pct=True):
    if d is None:
        return "n/a"
    scale = 100.0 if pct else 1.0
    unit = "%" if pct else ""
    if d.get("lo") is None:
        return f"{d['mean']*scale:+.3f}{unit} (n={d['n']}, {d.get('note','')})"
    return (f"{d['mean']*scale:+.3f}{unit} "
            f"[{d['lo']*scale:+.3f}, {d['hi']*scale:+.3f}] "
            f"n={d['n']} blocks={d['blocks']}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", required=True, nargs="+",
                    help="one or more ledgers; a 24 h tape rotation splits a run")
    ap.add_argument("--horizon", default=PRIMARY_HORIZON, choices=HORIZONS)
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--arm", default="gate_decide", choices=sorted(ARMS))
    ap.add_argument("--econ", default="marks", choices=sorted(ECON),
                    help="marks = window-close entry; marks_reach = entry when "
                         "the wallet verdict existed")
    args = ap.parse_args(argv)
    kw = {"arm": args.arm, "econ": args.econ}

    rows = load(args.ledger)
    ledgers = args.ledger if isinstance(args.ledger, list) else [args.ledger]
    passed = [r for r in rows if r.get("gate_passed") and r.get(ECON[args.econ])]
    report = {"arm": args.arm, "econ": args.econ,
        "ledgers": ledgers, "primary_horizon_s": int(args.horizon),
        "n_rows": len(rows), "n_gate_passed": len(passed),
        "gate_pass_rate": (len(passed) / len(rows)) if rows else None,
        "gate_fail_reasons": dict(collections.Counter(
            reason for r in rows for reason in r.get("gate_fail_reasons") or [])),
    }
    print(f"ledgers               {len(ledgers)}: "
          + ", ".join(os.path.basename(p) for p in ledgers))
    print(f"candidates            {len(rows)}")
    print(f"gate passed           {len(passed)}"
          f"  ({100*len(passed)/max(len(rows),1):.2f}%)")
    if not passed:
        print("\nNo gate-passing row with an entry. Nothing to score.")
        if args.json:
            json.dump(report, open(args.json, "w"), indent=2, sort_keys=True)
        return 0

    # The instrument first. A degenerate or absent decider voids the headline
    # before any arithmetic is worth printing.
    reasons = collections.Counter(
        (r.get("wallet_decider" if args.arm == "gate_wallet_decide"
               else "decider") or {}).get("reason") for r in passed
        if not r.get(ARMS[args.arm]))
    available = sum(1 for r in passed if r.get(ARMS[args.arm]))
    if args.arm == "gate_wallet_decide":
        # Research that was cut short (budget, rate limit) handed the model a
        # paragraph of "unknown"s. That row is void, like an absent decider.
        incomplete = sum(1 for r in passed if r.get(ARMS[args.arm])
                         and not (r.get("wallet") or {}).get("complete"))
        if incomplete:
            reasons["wallet_research_incomplete"] += incomplete
            available -= incomplete
    share = available / len(passed)
    report["decider"] = {"available": available, "share": share,
                         "unavailable_reasons": dict(reasons)}
    print(f"decider available     {available}/{len(passed)}  ({100*share:.1f}%)")
    if reasons:
        print(f"  unavailable because  {dict(reasons)}")

    d2 = d2_one_line_rules(passed, args.arm)
    report["d2"] = d2
    if d2["best"]:
        print(f"D2 best one-liner     {d2['best']['rule']}"
              f"  agreement={d2['best']['agreement']:.3f}")
    else:
        print(f"D2 best one-liner     not computable -- {d2.get('note','')}")

    strata = {
        "all": passed,
        "standard_curve": [r for r in passed if not r.get("nonstandard_curve")],
        "has_text": [r for r in passed if not r.get("text_thin")],
        "text_thin": [r for r in passed if r.get("text_thin")],
    }
    report["strata"] = {}
    for name, subset in strata.items():
        if not subset:
            continue
        s = summarise(subset, args.horizon, name, seed=args.seed, **kw)
        report["strata"][name] = s
        print(f"\n-- {name}  (n={len(subset)}) --")
        print(f"  arm gate            {fmt_ci(s['gate'])}")
        print(f"  arm {args.arm:<15} {fmt_ci(s['gate_decide'])}"
              f"   accepts={s['n_accept_decide']}")
        print(f"  G3 decide-gate      {fmt_ci(s['paired_decide_minus_gate'])}"
              "   (turnover-confounded; NOT the headline)")
        for null in s["roll_nulls"]:
            print(f"  G4 real-minus-roll  k={null['k']:<3d} "
                  f"{fmt_ci(null['vs_real'])}"
                  + ("   <-- HEADLINE" if null is s["roll_nulls"][0] else ""))
        print(f"  ORACLE bound        {fmt_ci(s['oracle_bound'])}"
              "   (upper bound over every exit rule)")

    # Half-split reproduction, G5.
    half = len(passed) // 2
    for name, subset in (("first_half", passed[:half]), ("second_half", passed[half:])):
        if not subset:
            continue
        s = summarise(subset, args.horizon, name, seed=args.seed, **kw)
        report["strata"][name] = s
        print(f"\n-- {name}  (n={len(subset)}) --")
        print(f"  PAIRED decide-gate  {fmt_ci(s['paired_decide_minus_gate'])}")

    print("\n-- horizon shape (NOT the headline; the prereg fixes 60 s) --")
    for h in HORIZONS:
        s = summarise(passed, h, "shape", seed=args.seed, **kw)
        print(f"  {h:>4}s  gate {fmt_ci(s['gate'])}")

    # Gates, stated as verdicts rather than left for the reader to infer.
    main_s = report["strata"].get("standard_curve") or report["strata"]["all"]
    g = {}
    if d2.get("reducible") is None:
        d2_note = "FAIL: D2 not computable (degenerate accept set)"
    elif d2["reducible"]:
        d2_note = f"FAIL: reducible to a one-liner ({d2['best']['rule']})"
    else:
        d2_note = "pass"
    g["G1_instrument"] = ("FAIL: decider unavailable on "
                          f"{100*(1-share):.1f}% of rows"
                          if share < MIN_AVAILABLE_SHARE else d2_note)
    g["G2_sample"] = ("pass" if len(passed) >= 300 else
                      f"FAIL: {len(passed)} gate-passing rows, need 300")
    pd = main_s["paired_decide_minus_gate"]
    g["G3_turnover"] = ("reported only, turnover-confounded: " + fmt_ci(pd))
    # G4 is the headline: selection at matched turnover, and it must hold at
    # EVERY roll offset, not at a best case.
    nulls = main_s["roll_nulls"]
    if not nulls:
        g["G4_selection"] = "FAIL: no roll null computable"
    else:
        verdicts = []
        for null in nulls:
            d = null["vs_real"]
            if d is None or d.get("lo") is None:
                verdicts.append((False, f"k={null['k']} no CI"))
            elif d["lo"] > 0 or d["hi"] < 0:
                verdicts.append((True, f"k={null['k']} excludes 0"))
            else:
                verdicts.append((False, f"k={null['k']} spans 0"))
        if all(ok for ok, _ in verdicts):
            g["G4_selection"] = ("pass: every roll offset excludes 0 ("
                                 + "; ".join(t for _, t in verdicts) + ")")
        else:
            g["G4_selection"] = ("FAIL: " + "; ".join(
                t for ok, t in verdicts if not ok))
    report["gates"] = g
    print("\n-- preregistered gates --")
    for name, verdict in g.items():
        print(f"  {name:<16} {verdict}")

    if args.json:
        json.dump(report, open(args.json, "w"), indent=2, sort_keys=True,
                  default=str)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
