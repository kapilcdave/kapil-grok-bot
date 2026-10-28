"""How much of a 0.5 SOL bundled dev buy comes back? Values the dev position by liquidating it into
the observed bonding-curve state at several horizons, on coins where the dev did NOT sell (so the
observed path is a genuine hold path).

    uv run --with orjson --with numpy python devrisk.py data/tape-<day>.jsonl.gz [lo hi]
"""
from __future__ import annotations

import argparse
import collections
import gzip
import sys
from pathlib import Path

import numpy as np
import orjson

sys.path.insert(0, str(Path(__file__).resolve().parent))
import levers as L  # noqa: E402

AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
CREATOR_FEE, FEE, LAUNCH_COST = 0.003, 0.0125, 0.0056
HORIZONS = (10, 30, 60, 300, 3600)
GRAD_REAL_SOL = 85.0      # the curve completes near 85 real SOL; past that the exit is PumpSwap, not the curve


def liquidate(vs: float, vt: float, k: float) -> float:
    """SOL out of selling k tokens into a constant-product curve at state (vs, vt)."""
    return vs - vs * vt / (vt + k) if k > 0 else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tape")
    ap.add_argument("lo", nargs="?", type=float, default=0.4)
    ap.add_argument("hi", nargs="?", type=float, default=0.6)
    a = ap.parse_args()

    creates: dict[str, dict] = {}
    st: dict[str, dict] = {}
    with gzip.open(a.tape, "rb") as f:
        for line in f:
            try:
                d = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            t = d.get("t")
            if t == "create":
                if abs(d["v_sol"] * d["v_tok"] / L.STD - 1) <= 0.01:
                    creates[d["mint"]] = d
                continue
            if t != "trade":
                continue
            c = creates.get(d["mint"])
            if c is None:
                continue
            dt = d["ts"] - c["ts"]
            if dt > HORIZONS[-1] or dt < -2:
                continue
            s = st.get(d["mint"])
            if s is None:
                s = st[d["mint"]] = dict(devtok=0.0, devsol=0.0, devsold=False, vol=0.0, agent=0.0,
                                         snap={}, vs=c["v_sol"], vt=c["v_tok"], grad=False, buyers=set())
            sol, user, side = float(d["sol"]), d["user"], d["side"]
            s["vol"] += sol
            if user == AGENT:
                s["agent"] += sol
            isdev = user == c["creator"] or user == c.get("user")
            if d.get("v_sol") and d.get("v_tok"):
                s["vs"], s["vt"] = float(d["v_sol"]), float(d["v_tok"])   # pump.fun trade reserves are POST-trade
                if s["vs"] - 30.0 >= GRAD_REAL_SOL:
                    s["grad"] = True
            if isdev and side == "buy" and d["sig"] == c["sig"]:
                s["devtok"] += float(d.get("tok") or 0)
                s["devsol"] += sol
            elif isdev and side == "sell":
                s["devsold"] = True
            elif not isdev and user != AGENT and side == "buy":
                s["buyers"].add(user)
            for h in HORIZONS:                 # last state at or before each horizon
                if dt <= h:
                    s["snap"][h] = (s["vs"], s["vt"], s["vol"] - s["agent"])

    rows = []
    for mint, s in st.items():
        if not (a.lo <= s["devsol"] <= a.hi) or s["devtok"] <= 0 or s["devsold"]:
            continue
        cost = s["devsol"] * (1 + FEE)
        r = dict(mint=mint, devsol=s["devsol"], cost=cost, grad=s["grad"], buyers=len(s["buyers"]),
                 mayhem=s["agent"] > 0)
        for h in HORIZONS:
            vs, vt, vol = s["snap"].get(h, (s["vs"], s["vt"], 0.0))
            out = liquidate(vs, vt, s["devtok"]) * (1 - FEE)
            r[f"rec{h}"] = out / cost
            r[f"net{h}"] = out - cost + CREATOR_FEE * vol - LAUNCH_COST
        rows.append(r)

    std = [r for r in rows if not r["mayhem"]]
    print(f"\n######## {Path(a.tape).name}: {len(rows):,} launches with a bundled dev buy in "
          f"[{a.lo:g}, {a.hi:g}] SOL that the dev did NOT sell inside the hour ({len(std):,} non-Mayhem)")
    print(f"the curve completed (exit moves to PumpSwap) on {np.mean([r['grad'] for r in std]):.2%}; "
          f"no outside buyer at all on {np.mean([r['buyers'] == 0 for r in std]):.1%}")
    print("\nrecovery = SOL back from selling the whole dev position into the curve / SOL paid (incl. the 1.25% buy fee)")
    print("  horizon |  p1  |  p10 |  p50 |  p90 | P(<100%) | P(<90%) | P(<50%) | P(<10%) | worst")
    for h in HORIZONS:
        v = np.array([r[f"rec{h}"] for r in std])
        q = np.percentile(v, [1, 10, 50, 90])
        print(f"  {h:5}s  | {q[0]:.3f}| {q[1]:.3f}| {q[2]:.3f}| {q[3]:.3f}| {np.mean(v < 1):7.1%} | "
              f"{np.mean(v < 0.9):6.1%} | {np.mean(v < 0.5):6.1%} | {np.mean(v < 0.1):6.1%} | {v.min():.3f}")
    print("\ndev NET at that horizon (position + 0.30% creator fee on volume so far - 0.0056 launch cost), SOL")
    print("  horizon |  p10  |  p50  |  mean | P(NET>0) | P(NET < -0.05) | P(NET < -0.25) | P(NET < -0.49)")
    for h in HORIZONS:
        n = np.array([r[f"net{h}"] for r in std])
        print(f"  {h:5}s  | {np.percentile(n,10):+.4f}| {np.percentile(n,50):+.4f}| {n.mean():+.4f}| "
              f"{np.mean(n > 0):7.1%} | {np.mean(n < -0.05):13.2%} | {np.mean(n < -0.25):13.2%} | {np.mean(n < -0.49):13.2%}")
    for lab, sel in (("no outside buyer", lambda r: r["buyers"] == 0),
                     ("1-4 outside buyers", lambda r: 1 <= r["buyers"] <= 4),
                     (">= 5 outside buyers", lambda r: r["buyers"] >= 5),
                     ("curve completed", lambda r: r["grad"])):
        s = [r for r in std if sel(r)]
        if len(s) < 30:
            print(f"\n[{lab}] n={len(s)} (not read)")
            continue
        v = np.array([r["rec60"] for r in s])
        n = np.array([r["net3600"] for r in s])
        print(f"\n[{lab}] n={len(s):,}: recovery at 60 s p10 {np.percentile(v,10):.3f} p50 {np.median(v):.3f} "
              f"p90 {np.percentile(v,90):.3f}; NET at 1 h p50 {np.median(n):+.4f} mean {n.mean():+.4f} P(>0) {np.mean(n>0):.1%}")


if __name__ == "__main__":
    main()
