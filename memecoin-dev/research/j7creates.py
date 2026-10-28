"""Do pump.fun coins get created off posts that show up in j7tracker's feed, and could we trade them?

Inputs: j7tape-*.jsonl (research/j7tape.py) and a PumpPortal create+trade tape (JSONL of
{recv_ns, data:{txType: create|buy|sell, mint, name, symbol, traderPublicKey, solAmount, tokenAmount,
vSolInBondingCurve, vTokensInBondingCurve, marketCapSol}}), recorded over the SAME window.

  python -m research.j7creates --j7 DIR_OR_FILE... --creates FILE... [--window 600]

1. MATCH   a create matches a live post if its symbol/name equals one of j7's suggested names
           (ai_suggestion prediction/ticker/ai_ticker) and it was created within --window s after
           the post reached us. Backlog (initialTweets) posts are excluded: only `tweet` events seen live.
2. PLACEBO the same matching with each post's clock shifted by +/- 1 h, so a name match that is just
           common words (PEPE, TRUMP) shows up as a match rate that doesn't care about the post.
3. ENTRY   for matched coins with trades on the tape: enter at the first trade >= create_recv + L
           (L = our reachable lag, from the PumpPortal stream), exit H seconds later at the last trade
           price <= that time, price = vSol/vTokens. Gross, then minus a round-trip cost.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

ROUND_TRIP_COST = 0.035    # pump.fun fee 1.25%/side + gas + impact, per research 2026-10-11
LAGS = (0.5, 1, 2, 5, 15)
HOLDS = (5, 15, 30, 60, 120)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"[⁠​]", "", s or "").lower())


def ts(t) -> float:
    if isinstance(t, (int, float)):
        return t / 1000 if t > 1e12 else float(t)
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()


def lines(paths):
    for p in paths:
        for f in sorted(Path(p).glob("j7tape-*.jsonl")) if Path(p).is_dir() else [Path(p)]:
            for line in open(f):
                try:
                    yield json.loads(line)
                except ValueError:
                    pass


def load_posts(paths):
    posts, names = {}, collections.defaultdict(set)
    for r in lines(paths):
        d = r.get("data")
        if r["event"] == "tweet" and isinstance(d, dict) and d.get("id") and str(d["id"]) not in posts:
            a = d.get("author") or {}
            posts[str(d["id"])] = dict(recv=r["recv_ns"] / 1e9, handle=a.get("handle"), fol=a.get("followersCount") or 0,
                                       text=(d.get("text") or "")[:80].replace("\n", " "), cashtag=bool(d.get("contractAddress")))
        elif r["event"] == "ai_suggestion" and isinstance(d, dict):
            tid = d.get("tweet_id") or (d.get("tweet_url") or "").rstrip("/").rsplit("/", 1)[-1]
            for k in ("prediction", "ticker", "ai_ticker"):
                if tid and norm(d.get(k)):
                    names[str(tid)].add(norm(d[k]))
    return {k: dict(v, names=names.get(k, set())) for k, v in posts.items() if names.get(k)}


def load_tape(paths):
    creates, trades = {}, collections.defaultdict(list)
    for p in paths:
        for line in open(p):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            d = r.get("data")
            if not isinstance(d, dict) or not d.get("mint"):
                continue
            t = r["recv_ns"] / 1e9
            if d.get("txType") == "create":
                creates[d["mint"]] = dict(recv=t, sym=norm(d.get("symbol")), name=norm(d.get("name")), creator=d.get("traderPublicKey"),
                                          pool=d.get("pool"), dev_buy=d.get("solAmount") or 0)
            elif d.get("txType") in ("buy", "sell") and d.get("vTokensInBondingCurve"):
                trades[d["mint"]].append((t, d["vSolInBondingCurve"] / d["vTokensInBondingCurve"], d["txType"], d["solAmount"]))
    return creates, trades


def matches(posts, creates, window, shift=0.0):
    out = []
    for pid, p in posts.items():
        t0 = p["recv"] + shift
        for m, c in creates.items():
            if t0 <= c["recv"] <= t0 + window and (c["sym"] in p["names"] or c["name"] in p["names"]):
                out.append((pid, m, c["recv"] - t0))
    return out


def price_at(tr, t):
    last = None
    for x in tr:
        if x[0] > t:
            break
        last = x[1]
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--j7", nargs="+", required=True)
    ap.add_argument("--creates", nargs="+", required=True)
    ap.add_argument("--window", type=float, default=600)
    a = ap.parse_args()
    posts = load_posts(a.j7)
    creates, trades = load_tape(a.creates)
    lo = min(c["recv"] for c in creates.values())
    hi = max(c["recv"] for c in creates.values())
    live = {k: p for k, p in posts.items() if lo <= p["recv"] <= hi - a.window}
    print(f"live posts with a j7 suggestion: {len(posts)}; inside the create tape with a full {a.window:.0f}s window: {len(live)}")
    print(f"creates on tape: {len(creates)} over {(hi - lo) / 60:.0f} min; with trades: {sum(1 for m in creates if trades[m])}")
    real = matches(live, creates, a.window)
    plc = [len(matches(live, creates, a.window, s)) for s in (-3600, 3600, -7200, 7200)]
    print(f"\nname matches within {a.window:.0f}s after the post: {len(real)} on {len({r[0] for r in real})} posts;"
          f" placebo (clock +/-1h,2h): {plc}")
    for pid, m, lag in sorted(real, key=lambda r: r[2]):
        c, p = creates[m], live[pid]
        print(f"  +{lag:6.1f}s  {c['sym'] or c['name']:14s} <- @{p['handle']} ({p['fol']:,}) {p['text'][:50]!r}  dev_buy={c['dev_buy']:.2f} pool={c['pool']}")

    print("\nENTRY model (gross % of entry price; net = gross - 3.5% round trip), matched coins with trades")
    rows = collections.defaultdict(list)
    for pid, m, _ in real:
        tr = sorted(trades.get(m, []))
        if not tr:
            continue
        for L in LAGS:
            te = creates[m]["recv"] + L
            ent = next((x for x in tr if x[0] >= te), None)
            if not ent:
                continue
            for H in HOLDS:
                px = price_at(tr, ent[0] + H)
                if px and px > 0 and tr[-1][0] >= ent[0] + H:
                    rows[(L, H)].append(px / ent[1] - 1)
    print("lag_s hold_s    n   mean%  median%  net_mean%  win%")
    for (L, H), v in sorted(rows.items()):
        print(f"{L:5} {H:6} {len(v):4} {100 * st.mean(v):7.1f} {100 * st.median(v):8.1f} {100 * (st.mean(v) - ROUND_TRIP_COST):10.1f} {100 * sum(x > 0 for x in v) / len(v):6.0f}")
    base = collections.defaultdict(list)
    for m, c in creates.items():
        tr = sorted(trades.get(m, []))
        for L, H in ((1, 30), (2, 60), (5, 60)):
            ent = next((x for x in tr if x[0] >= c["recv"] + L), None)
            if ent and tr[-1][0] >= ent[0] + H:
                px = price_at(tr, ent[0] + H)
                base[(L, H)].append(px / ent[1] - 1)
    print("\nbaseline: ALL creates with trades, same rules")
    for k, v in sorted(base.items()):
        print(f"lag {k[0]} hold {k[1]}: n={len(v)} mean {100 * st.mean(v):.1f}% median {100 * st.median(v):.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
