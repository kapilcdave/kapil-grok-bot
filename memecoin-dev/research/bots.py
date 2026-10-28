"""EXPLORATORY: what brings BOT volume to a fresh pump.fun coin, and what the dev nets from it.

Bots are ~94% of a coin's volume, so they are most of the creator-fee base. Same tapes and the same
returning-wallet human definition as levers.py (Amendment A); a bot is any other non-creator buyer.
Every lever is scored WITHIN creator (creators with 2+ launches in the tape), so "serious creators do
everything" can't explain it. Replication rule, as in the human prereg: same sign and |t| >= 2.5 on
both days.

Dev net (SOL, first hour) = 0.30% creator fee on all volume
    - dev buys * 1.0125 + dev sells * 0.9875 + leftover tokens sold into the final curve * 0.9875
    - launch cost (0.0056, PumpPortal plain create measured 10-14).

    uv run --with orjson --with numpy python bots.py data/tape-A.jsonl.gz data/tape-B.jsonl.gz
"""
from __future__ import annotations

import collections
import sys
from urllib.parse import urlparse

import numpy as np

import levers as L

CREATOR_FEE = 0.003
FEE = 0.0125
LAUNCH_COST = 0.0056
MAYHEM_EXTRA = 0.0027   # rent of InitializeMayhemState, simulated 10-14
# pump.fun's Mayhem agent: pays NO creator fee (6/6 sampled trades, 10-14), so its volume is not fee base,
# and it is not a sniper. A coin it trades in the first hour is a Mayhem coin (same curve reserves as standard).
AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
DEVBUY_EDGES = [(0, 0), (0, 0.1), (0.1, 0.5), (0.5, 1), (1, 2), (2, 1e9)]


def bucket(db: float) -> int:
    if db <= 0:
        return 0
    for i, (lo, hi) in enumerate(DEVBUY_EDGES[1:], 1):
        if lo < db <= hi:
            return i
    return len(DEVBUY_EDGES) - 1


def liquidate(vs: float, vt: float, k: float) -> float:
    return vs - vs * vt / (vt + k) if k > 0 else 0.0


def rows_of(path: str, other: str):
    oc, _om, ot = L.load(other)
    returning = {u for mint, rows in ot.items() if mint in oc for _t, u, side, *_ in rows if side == "buy"}
    del oc, ot
    creates, metas, _ = L.load(path)
    # levers.L.load drops tok/v_sol/v_tok/slot; re-read trades with them
    import gzip
    import orjson
    trades = collections.defaultdict(list)
    with gzip.open(path, "rb") as f:
        for line in f:
            try:
                d = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            if d.get("t") == "trade" and d["mint"] in creates:
                trades[d["mint"]].append((d["ts"], d.get("slot"), d["user"], d["side"], float(d["sol"]),
                                          float(d.get("tok") or 0), d.get("v_sol"), d.get("v_tok"), d["sig"]))
    bought = collections.defaultdict(set)
    for mint, rs in trades.items():
        for r in rs:
            if r[3] == "buy":
                bought[r[2]].add(mint)

    order = sorted(creates.values(), key=lambda c: c["ts"])
    prior, seen = {}, collections.Counter()
    for c in order:
        prior[c["mint"]] = seen[c["creator"]]
        seen[c["creator"]] += 1

    out = []
    for mint, c in creates.items():
        m = metas.get(mint)
        if not m or not m.get("ok") or abs(c["v_sol"] * c["v_tok"] / L.STD - 1) > 0.01:
            continue
        t0, s0, cr = c["ts"], c.get("slot"), c["creator"]
        rs = sorted(trades.get(mint, ()), key=lambda r: (r[0], r[1] or 0))
        devbuy = vbot = vhum = vall = 0.0
        dbuy = dsell = dtok = 0.0
        block, fast, bots = set(), set(), set()
        vsingle = vbotbuy = vagent = 0.0
        agent_block = False
        vs, vt = c["v_sol"], c["v_tok"]
        for ts, slot, user, side, sol, tok, v_sol, v_tok, sig in rs:
            if ts - t0 > L.WINDOW_S:
                break
            vall += sol
            if v_sol and v_tok:
                vs, vt = v_sol, v_tok
            if user == AGENT:
                vagent += sol
                agent_block |= s0 is not None and slot == s0
                continue
            if user == cr:
                if side == "buy":
                    dbuy += sol
                    dtok += tok
                    if sig == c["sig"]:
                        devbuy += sol
                else:
                    dsell += sol
                    dtok -= tok
                continue
            human = ts - t0 >= L.MIN_DELAY_S and len(bought[user]) <= 10 and user in returning
            if human:
                vhum += sol
            else:
                vbot += sol
                if side == "buy":
                    vbotbuy += sol
                    if len(bought[user]) == 1:
                        vsingle += sol
                    bots.add(user)
                    if s0 is not None and slot == s0:
                        block.add(user)
                    if ts - t0 <= 30:
                        fast.add(user)
        mayhem = vagent > 0
        net = (CREATOR_FEE * (vall - vagent) - dbuy * (1 + FEE) + dsell * (1 - FEE)
               + liquidate(vs, vt, max(dtok, 0.0)) * (1 - FEE) - LAUNCH_COST - (MAYHEM_EXTRA if mayhem else 0.0))
        k = L.tw_kind(m.get("twitter"))
        name, sym = c.get("name") or "", c.get("symbol") or ""
        host = urlparse(c.get("uri") or "").netloc
        hour = (t0 // 3600) % 24
        b = bucket(devbuy)
        x = [k == "x_post", k == "x_profile", bool(m.get("website")), bool(m.get("telegram")),
             bool(m.get("description")), name.isupper(), L.norm(name) == L.norm(sym), not name.isascii(),
             13 <= hour <= 22, host != "ipfs.io", prior[mint] == 0] + [b == i for i in range(1, len(DEVBUY_EDGES))]
        out.append(dict(cr=cr, x=x, vbot=vbot, vhum=vhum, vall=vall, block=len(block), fast=len(fast),
                        bots=len(bots), blockset=sorted(block), net=net, mayhem=mayhem, vagent=vagent, agent_block=agent_block, single=vsingle / vbotbuy if vbotbuy else 0.0, fee=CREATOR_FEE * (vall - vagent), b=b, devbuy=devbuy))
    return out


NAMES = ["x_post", "x_profile", "website", "telegram", "description", "all_caps", "ticker_eq_name",
         "non_ascii", "us_hours", "non_ipfs_host", "first_in_tape"] + \
        [f"devbuy_{lo:g}_{hi:g}" if hi < 1e9 else f"devbuy_gt{lo:g}" for lo, hi in DEVBUY_EDGES[1:]]


def fe(rows, y):
    byc = collections.defaultdict(list)
    for r, yy in zip(rows, y):
        byc[r["cr"]].append((yy, r["x"]))
    Y, X, G = [], [], []
    for cr, lst in byc.items():
        if len(lst) < 2:
            continue
        ys = np.array([a for a, _ in lst], float)
        xs = np.array([b for _, b in lst], float)
        Y.extend(ys - ys.mean())
        X.extend(xs - xs.mean(0))
        G.extend([cr] * len(lst))
    Y, X, G = np.array(Y), np.array(X), np.array(G)
    keep = X.std(0) > 0
    beta, se = L.cluster_ols(X[:, keep], Y, G)
    return [n for n, k in zip(NAMES, keep) if k], beta, se, len(set(G)), len(Y)


def robust(rows) -> None:
    """A creator's own side wallets (bundles, holder farms) count as 'bots' here, which would credit
    the dev for selling into themselves. Single-use wallets (buy exactly one coin all day, never
    return) are the farm signature; re-score NET where they carry little of the bot buying."""
    print("single-use-wallet share of bot BUY SOL, by dev-buy bucket (median | mean):")
    for i in range(len(DEVBUY_EDGES)):
        s = [r["single"] for r in rows if r["b"] == i and r["single"] is not None]
        if s:
            print(f"  bucket {i}: {np.median(s):.1%} | {np.mean(s):.1%}")
    for lab, keep in (("no launch-block buyer", lambda r: r["block"] == 0),
                      ("single-use share < 20%", lambda r: r["single"] < 0.2),
                      ("both", lambda r: r["block"] == 0 and r["single"] < 0.2)):
        sub = [r for r in rows if keep(r)]
        print(f"[{lab}] {len(sub):,} launches -- dev-buy bucket: n | median NET | mean NET | P(NET>0)")
        for i in range(len(DEVBUY_EDGES)):
            n = np.array([r["net"] for r in sub if r["b"] == i])
            if len(n):
                print(f"  bucket {i}: {len(n):6,} | {np.median(n):+.5f} | {n.mean():+.5f} | {np.mean(n > 0):.1%}")
        net = np.array([r["net"] for r in sub])
        names, beta, se, nc, nn = fe(sub, np.clip(net, *np.percentile(net, [1, 99])))
        print("  within-creator NET: " + ", ".join(f"{nm} {b:+.3f} (t {b / e:+.1f})" for nm, b, e in zip(names, beta, se)
                                                    if nm.startswith("devbuy") or nm == "first_in_tape") + f"  [{nc:,} creators]")


def main() -> None:
    a, b = sys.argv[1:3]
    for path, other in ((a, b), (b, a)):
        rows = rows_of(path, other)
        V = np.array([r["vall"] for r in rows])
        vb = np.array([r["vbot"] for r in rows])
        net = np.array([r["net"] for r in rows])
        print(f"\n######## {path.split('/')[-1]}: {len(rows):,} launches (standard curve, meta ok)")
        print(f"bot share of non-creator volume {vb.sum() / max(vb.sum() + sum(r['vhum'] for r in rows), 1e-9):.1%}; "
              f"P(any launch-block bot) {np.mean([r['block'] > 0 for r in rows]):.1%}; "
              f"median bots/launch {np.median([r['bots'] for r in rows]):.0f}")
        print(f"creator fee/launch: median {np.median(V) * CREATOR_FEE:.5f}  mean {V.mean() * CREATOR_FEE:.5f} SOL "
              f"vs cost {LAUNCH_COST}; P(fee > cost) {np.mean(V * CREATOR_FEE > LAUNCH_COST):.1%}")
        print("dev-buy bucket: n | median fee | mean fee | median NET | mean NET | P(NET>0)   [NET incl. dev position]")
        for i, (lo, hi) in enumerate(DEVBUY_EDGES):
            s = [r for r in rows if r["b"] == i]
            if not s:
                continue
            f = np.array([r["fee"] for r in s])
            n = np.array([r["net"] for r in s])
            lab = "none" if i == 0 else (f"({lo:g},{hi:g}]" if hi < 1e9 else f">{lo:g}")
            print(f"  {lab:9} {len(s):6,} | {np.median(f):.5f} | {f.mean():.5f} | {np.median(n):+.5f} | {n.mean():+.5f} | {np.mean(n > 0):.1%}")
        robust(rows)
        for label, y in (("log1p(bot SOL)", np.log1p(vb)),
                         ("log1p(launch-block bots)", np.log1p([r["block"] for r in rows])),
                         ("log1p(bots <=30s)", np.log1p([r["fast"] for r in rows])),
                         ("dev NET SOL (winsor 1/99)", np.clip(net, *np.percentile(net, [1, 99])))):
            names, beta, se, nc, n = fe(rows, y)
            print(f"\n== {label}: within creator, {nc:,} creators, {n:,} launches")
            for nm, bb, ss in zip(names, beta, se):
                mult = f"x{np.exp(bb):.2f}" if label.startswith("log1p") else ""
                print(f"  {nm:16} {bb:+.4f} se {ss:.4f} t {bb / ss:+6.1f}  {mult}")


if __name__ == "__main__":
    main()
