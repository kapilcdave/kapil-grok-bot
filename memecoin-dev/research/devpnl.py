"""What does the CREATOR's bag make, by dev-buy size, on other people's launches? (10-15 night tape)

    python -m research.devpnl research/data/devpnl_creates.json research/data/devpnl_trades*.json

Why. The swarm wallets we were chasing buy almost only coins with a dev buy (0% of 620 zero-dev-buy launches,
13-20% above 0.3 SOL). Before we spend on a big dev buy, measure what that bag is worth when sold into the
flow it actually drew. For each launch we replay the curve from its trades (curve SOL/tokens are net of fee)
and value the creator's create-tx tokens under fixed sell rules, net of FEE per side. "outside" = buys by
signers that are neither the creator nor in the create slot (create-slot buyers are bundles/snipers).
Rows from chaintrades.py stop at 60 txs or 120 s, so a busy coin's later flow is cut off (a lower bound).

10-15 tape (21:22-22:26Z, 743 launches): FOLLOWERS 2.5-5.5 t30 +0.171 SOL/launch CI [+0.100,+0.246], median
-0.089; 0.3-0.7 +0.005 CI [-0.019,+0.034]. 86% of single-coin buyers' SOL is from wallets with 1000+ txs (real).
PREREG replication on a fresh hour: FOLLOWERS 2.5-5.5 t30 passes iff mean > 0 AND bootstrap 2.5% bound > 0.
"""
from __future__ import annotations

import json
import statistics as st
import sys

V_SOL, V_TOK = 30.0, 1_073_000_000.0
FEE = 0.0125
COST = 0.02            # create rent + priority, SOL, charged once per launch
BANDS = [(0.0, 0.001), (0.001, 0.3), (0.3, 0.7), (0.7, 1.5), (1.5, 2.5), (2.5, 3.5), (3.5, 5.5), (5.5, 100)]


FOLLOW: dict = {}      # buyer -> (creators it bought, coins it bought), over the whole tape; set by table()


def insiders(c: dict, t: dict, strict) -> set:
    """The creator always (we ARE the creator: its own trades are our decision, not flow). With strict, also
    every create-slot buyer, buys AND later sells: mostly the creator's own bundle, which we would not have."""
    out = {c["traderPublicKey"]}
    if strict:
        out |= {x["signer"] for x in t["trades"] if x["side"] == "buy" and x["dt"] == 0}
        held: dict = {}
        for x in t["trades"]:          # sold more than it bought on the curve: the tokens came from the creator
            held[x["signer"]] = held.get(x["signer"], 0) + (x["tok"] if x["side"] == "buy" else -x["tok"])
            if held[x["signer"]] < -1:
                out.add(x["signer"])
    if strict == 2:                    # bought 2+ coins of ONE creator and nobody else's: a creator-follower
        out |= {x["signer"] for x in t["trades"]    # (copy bot or the creator's wallet), not flow a new dev gets
                if FOLLOW.get(x["signer"], (0, 0))[0] == 1 and FOLLOW[x["signer"]][1] >= 2}
    return out


def replay(c: dict, t: dict, strict=0):
    """[(dt, vsol, vtok, outside_buy_cum)] after each trade, starting just after the create."""
    vs, vt = V_SOL + c["solAmount"], V_TOK - c["initialBuy"]
    out, cum = [(0.0, vs, vt, 0.0)], 0.0
    skip = insiders(c, t, strict)
    for x in t["trades"]:
        if x["signer"] in skip:
            continue
        if x["side"] == "buy":
            vs, vt = vs + x["sol"], vt - x["tok"]
            if x["dt"] > 0:
                cum += x["sol"]
        else:
            vs, vt = vs - x["sol"], vt + x["tok"]
        out.append((x["dt"], vs, vt, cum))
    return out


def bag(vs: float, vt: float, tok: float) -> float:
    return (vs - vs * vt / (vt + tok)) * (1 - FEE)


def rules(c: dict, path) -> dict:
    tok, cost = c["initialBuy"], c["solAmount"] * (1 + FEE) + COST
    res = {}
    for T in (2, 10, 30, 120):
        p = [s for s in path if s[0] <= T][-1]
        res[f"t{T}"] = bag(p[1], p[2], tok) - cost
    for X in (0.5, 1.0, 2.0):
        hit = next((s for s in path if s[3] >= X), path[-1])
        res[f"out{X:g}"] = bag(hit[1], hit[2], tok) - cost
    res["oracle"] = max(bag(s[1], s[2], tok) for s in path) - cost
    return res


def main() -> int:
    creates = json.load(open(sys.argv[1]))
    trades = {}
    for f in sys.argv[2:]:             # chaintrades shards
        trades.update(json.load(open(f)))
    creators, coins = {}, {}
    for m, t in trades.items():
        if t and m in creates:
            for x in t["trades"]:
                if x["side"] == "buy" and x["signer"] != creates[m]["traderPublicKey"]:
                    creators.setdefault(x["signer"], set()).add(creates[m]["traderPublicKey"])
                    coins[x["signer"]] = coins.get(x["signer"], 0) + 1
    FOLLOW.update({w: (len(creators[w]), coins[w]) for w in creators})
    for strict in (0, 1, 2):
        table(creates, trades, strict)
    return 0


def table(creates: dict, trades: dict, strict: int) -> None:
    rows = []
    for m, t in trades.items():
        if not t or m not in creates:
            continue
        c = creates[m]
        path = rules(c, replay(c, t, strict)) if c["solAmount"] > 0 else {}
        outside = sum(x["sol"] for x in t["trades"] if x["side"] == "buy" and x["dt"] > 0 and x["signer"] != c["traderPublicKey"])
        slot0 = sum(x["sol"] for x in t["trades"] if x["side"] == "buy" and x["dt"] == 0)
        rows.append({"mint": m, "dev": c["solAmount"], "outside": outside, "slot0": slot0, **path})
    label = ["RAW: only the creator removed", "STRICT: + create-slot buyers + wallets selling tokens they never bought",
             "FOLLOWERS: + wallets that bought 2+ coins of one creator only (the PREREG cell is 2.5-5.5 t30 here)"][strict]
    print(f"\n{label}"
          f" -- {len(rows)} launches scored (FEE {FEE:.2%}/side, COST {COST} SOL)")
    keys = ["t2", "t10", "t30", "t120", "out0.5", "out1", "out2", "oracle"]
    print("dev band      n  outside med/mean  P(out>=1)  slot0 mean | net SOL mean per launch: " + " ".join(f"{k:>7s}" for k in keys))
    for a, b in BANDS:
        v = [r for r in rows if a <= r["dev"] < b]
        if not v:
            continue
        o = [r["outside"] for r in v]
        line = f"{a:5.2f}-{b:<6.2f} {len(v):4d}  {st.median(o):5.2f}/{st.mean(o):5.2f}     {sum(x >= 1 for x in o) / len(v):5.1%}    {st.mean(r['slot0'] for r in v):5.2f}  |"
        priced = [r for r in v if "t2" in r]      # a zero dev buy has no bag to price
        if priced:
            line += "                         " + " ".join(f"{st.mean(r[k] for r in priced):+7.3f}" for k in keys)
        print(line)


if __name__ == "__main__":
    sys.exit(main())
