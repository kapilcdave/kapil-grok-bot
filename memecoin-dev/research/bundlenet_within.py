"""Within-creator control + the decision probability, and the prereg's classifier-validation arm.

  python -m research.bundlenet_within research/data/bn_launches.jsonl [--validate]

The prereg declared that a RISING dose-response licenses one further test rather than a spend: a
within-creator comparison, which holds the operator's narrative pipeline, wallet hygiene and skill
fixed and asks whether the SAME creator gets more outside money on its bigger-dev-buy launches.
`--validate` runs the committed falsification arm on the `single`-mint class against the chain.
"""
from __future__ import annotations

import collections
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

MIN_PAIR, MIN_SHARE = 3, 0.5
BUCKETS = [(0, 0.1), (0.1, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 10), (10, 1e9)]
LABELS = ["0-0.1", "0.1-0.5", "0.5-1", "1-2", "2-5", "5-10", ">10"]


def load(path: str) -> list[dict]:
    rows = [r for r in (json.loads(l) for l in open(path)) if r["n_bad"] == 0]
    pair, tot = collections.Counter(), collections.Counter()
    for r in rows:
        for u, sol, c, after in r["early"]:
            if c == 3:
                pair[(r["creator"], u)] += 1
                tot[u] += 1
    insider = {(c, u) for (c, u), n in pair.items() if n >= MIN_PAIR and n / tot[u] >= MIN_SHARE}
    for r in rows:
        r["outside_x_after0"] = sum(
            sol for u, sol, c, after in r["early"]
            if c == 3 and after and (r["creator"], u) not in insider)
    return rows


def main() -> int:
    rows = load(sys.argv[1])
    x = np.array([r["dev_sol_slot0"] for r in rows])
    y = np.array([r["outside_x_after0"] for r in rows])

    print("=== did the launch get back more outside buying than the dev put in? ===")
    print(f"{'bucket':>8} {'n':>7} {'P(out>dev)':>11} {'P(out>2*dev)':>13} {'mean out-dev':>13} {'median out-dev':>15}")
    for (lo, hi), lab in zip(BUCKETS, LABELS):
        m = (x > lo) & (x <= hi)
        if m.sum() < 30:
            continue
        d = y[m] - x[m]
        print(f"{lab:>8} {m.sum():>7,} {(y[m] > x[m]).mean():>11.3f} {(y[m] > 2 * x[m]).mean():>13.3f} "
              f"{d.mean():>+13.3f} {np.median(d):>+15.3f}")

    print("\n=== within-creator control: creators with >= 3 eligible launches ===")
    byc = collections.defaultdict(list)
    for r in rows:
        byc[r["creator"]].append(r)
    multi = {c: v for c, v in byc.items() if len(v) >= 3}
    print(f"{len(multi):,} creators, {sum(len(v) for v in multi.values()):,} launches")
    num = den = 0.0
    slopes, used = [], 0
    for c, v in multi.items():
        xi = np.array([r["dev_sol_slot0"] for r in v])
        yi = np.array([r["outside_x_after0"] for r in v])
        if xi.std() == 0:
            continue
        used += 1
        xd, yd = xi - xi.mean(), yi - yi.mean()
        num += float((xd * yd).sum())
        den += float((xd * xd).sum())
        if len(v) >= 5:
            slopes.append(float((xd * yd).sum() / (xd * xd).sum()))
    pooled_slope = float(np.polyfit(x, y, 1)[0])
    print(f"creators with dev-buy variation: {used:,}")
    print(f"POOLED slope (outside SOL per SOL of create-block dev buy): {pooled_slope:+.4f}")
    print(f"WITHIN-CREATOR slope (creator fixed effects):               {num / den:+.4f}")
    if slopes:
        s = np.array(slopes)
        boot = [np.median(s[np.random.default_rng(i).integers(0, len(s), len(s))]) for i in range(2000)]
        print(f"median per-creator slope (>=5 launches, n={len(s):,}): {np.median(s):+.4f} "
              f"[{np.percentile(boot, 2.5):+.4f},{np.percentile(boot, 97.5):+.4f}]  "
              f"share positive {100 * (s > 0).mean():.1f}%")

    if "--validate" in sys.argv:
        validate(rows)
    return 0


def validate(rows: list[dict]) -> None:
    """Prereg falsification arm: are `single`-mint wallets really ephemeral bundle-shaped wallets?"""
    url = [l.split("=", 1)[1].strip() for l in open(Path.home() / ".config/solana-rpc/quicknode.env")
           if l.startswith("QUICKNODE_SOLANA_HTTP=")][0]

    def rpc(method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for a in range(5):
            try:
                r = json.load(urllib.request.urlopen(
                    urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=40))
                return r.get("result")
            except Exception:
                time.sleep(1.2 * (a + 1))
        return None

    rng = np.random.default_rng(20261017)
    pools = {1: [], 3: []}
    for r in rows:
        for u, sol, c, after in r["early"]:
            if c in pools and sol > 0.05:
                pools[c].append(u)
    print("\n=== classifier validation (prereg arm) ===")
    now = int(time.time())
    for c, name in [(1, "single"), (3, "broad")]:
        samp = list(rng.choice(sorted(set(pools[c])), size=40, replace=False))
        sigs, ages = [], []
        for w in samp:
            s = rpc("getSignaturesForAddress", [w, {"limit": 1000}]) or []
            sigs.append(len(s))
            bt = [e["blockTime"] for e in s if e.get("blockTime")]
            ages.append((now - min(bt)) / 3600 if bt else -1)
            time.sleep(0.07)
        sigs, ages = np.array(sigs), np.array(ages)
        print(f"{name:>7}: median sigs {np.median(sigs):>7.0f}  mean {sigs.mean():>7.0f}  "
              f"share >=400 sigs {100 * (sigs >= 400).mean():>5.1f}%  median age {np.median(ages):>8.1f} h")


if __name__ == "__main__":
    sys.exit(main())
