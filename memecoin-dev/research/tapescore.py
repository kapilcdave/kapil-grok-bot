"""Score PREREG-big-dev-buy-oos-20261016.md on a snipefeed tape (crypto/dexarb/snipefeed.py --watch-s 130).

    python -m research.tapescore TAPE.jsonl [TAPE2.jsonl ...] [--day 20261017]

The tape has every create, its metadata, and each mint's trades for 130 s, all with slots, so it needs no RPC
(the QuickNode daily cap stopped the chaintrades fetch on 10-16). Rows are converted to devpnl.py's shape and
scored with the SAME replay. P1-P4 are printed per UTC day of create time.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import statistics as st
import time

from research import devpnl as D

SLOT_S = 0.4
K = 30.0 * 1_073_000_000.0
MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
GAP_S = 5.0
WINDOW_S = 130.0
STOP = {"the", "of", "and", "coin", "token", "on", "in", "for", "to", "is", "by", "sol", "official", "daily", "etf",
        "pump", "fun", "meme", "inu"}
EPOCH_MS = 1288834974657


def words(name: str, symbol: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", f"{name} {symbol}".lower()) if len(w) > 2 and w not in STOP}


def load(paths: list[str]):
    """Streams the tape(s): a full-firehose day (10-10/10-11 tapes, ~16M trades) keeps only each mint's
    first WINDOW_S of trades, the same window `snipefeed --watch-s 130` records. .gz is read directly."""
    import gzip
    creates, metas, trades, gaps = {}, {}, collections.defaultdict(list), []
    keep = ("slot", "recv", "user", "side", "sol", "tok", "v_sol", "v_tok")
    last = None
    for p in paths:
        with (gzip.open(p, "rt") if p.endswith(".gz") else open(p)) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue                    # a line cut by a live writer
                recv = r.get("recv")
                if recv is not None:
                    if last is not None and recv - last > GAP_S:
                        gaps.append((last, recv))
                    last = recv if last is None else max(last, recv)
                t = r.get("t")
                if t == "create":
                    creates[r["mint"]] = r
                elif t == "meta":
                    metas[r["mint"]] = {k: r.get(k) for k in ("twitter", "website")}
                elif t == "trade":
                    c = creates.get(r["mint"])
                    if c is not None and recv - c["recv"] <= WINDOW_S:
                        trades[r["mint"]].append({k: r[k] for k in keep})
    return creates, metas, trades, gaps


def convert(creates, metas, trades, gaps):
    """-> (creates in devpnl shape, trades in chaintrades shape, extras) for clean non-Mayhem mints."""
    cr, tr, ex = {}, {}, {}
    for m, c in creates.items():
        if any(a - WINDOW_S <= c["recv"] <= b for a, b in gaps):
            continue
        ts = sorted(trades.get(m, []), key=lambda x: (x["slot"], x["recv"]))
        if any(x["user"] == MAYHEM_AGENT or abs(x["v_sol"] * x["v_tok"] / K - 1) > 0.01 for x in ts):
            continue
        creator = c["creator"]
        dev = [x for x in ts if x["slot"] == c["slot"] and x["user"] == creator and x["side"] == "buy"]
        rest = [x for x in ts if x not in dev]
        sol, tok = sum(x["sol"] for x in dev), sum(x["tok"] for x in dev)
        cr[m] = {"mint": m, "traderPublicKey": creator, "solAmount": sol, "initialBuy": tok, "name": c["name"],
                 "symbol": c["symbol"], "_t": c["recv"], "is_mayhem_mode": False}
        tr[m] = {"create_slot": c["slot"], "trades": [
            {"dt": round((x["slot"] - c["slot"]) * SLOT_S, 2), "side": x["side"], "sol": x["sol"], "tok": x["tok"],
             "signer": x["user"], "slot": x["slot"]} for x in rest]}
        meta = metas.get(m) or {}
        sid = re.search(r"/status/(\d+)", str(meta.get("twitter") or ""))
        ex[m] = {"post_age": c["recv"] - ((int(sid.group(1)) >> 22) + EPOCH_MS) / 1000 if sid else None,
                 "website": bool(meta.get("website")), "slot0": any(x["dt"] == 0 and x["side"] == "buy"
                                                                    for x in tr[m]["trades"])}
    return cr, tr, ex


def hot(cr: dict, ex: dict) -> None:
    order = sorted(cr.values(), key=lambda c: c["_t"])
    for i, c in enumerate(order):
        w, hits, j = words(c["name"], c["symbol"]), collections.Counter(), i - 1
        while j >= 0 and c["_t"] - order[j]["_t"] <= 1800:
            d = order[j]
            if d["traderPublicKey"] != c["traderPublicKey"]:
                for x in words(d["name"], d["symbol"]) & w:
                    hits[x] += 1
            j -= 1
        top = hits.most_common(1)
        ex[c["mint"]]["hot_word"] = top[0][0] if top and top[0][1] >= 4 else ""


def ci(y: list[float]) -> tuple[float, float, float]:
    random.seed(0)
    bs = sorted(st.mean(random.choices(y, k=len(y))) for _ in range(4000))
    return st.mean(y), bs[100], bs[3899]


def diff_ci(a: list[float], b: list[float]) -> tuple[float, float, float]:
    random.seed(0)
    bs = sorted(st.mean(random.choices(a, k=len(a))) - st.mean(random.choices(b, k=len(b))) for _ in range(4000))
    return st.mean(a) - st.mean(b), bs[100], bs[3899]


def line(lab: str, v, passed=None) -> str:
    if len(v) < 3:
        return f"  {lab:44s} n={len(v)} (too few)"
    m, lo, hi = ci(v)
    tag = "" if passed is None else ("  PASS" if passed(m, lo) else "  FAIL")
    return f"  {lab:44s} n={len(v):4d} mean {m:+.3f} CI[{lo:+.3f},{hi:+.3f}] win {sum(x > 0 for x in v) / len(v):.0%}{tag}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tapes", nargs="+")
    ap.add_argument("--day", default="", help="only this UTC create day, YYYYMMDD")
    ap.add_argument("--start", default="", help="only creates at/after this UTC time, YYYYmmddTHHMM (a PREREG block)")
    ap.add_argument("--end", default="", help="only creates before this UTC time, YYYYmmddTHHMM")
    a = ap.parse_args()
    D.FEE = 0.0175
    creates, metas, trades, gaps = load(a.tapes)
    cr, tr, ex = convert(creates, metas, trades, gaps)
    hot(cr, ex)
    D.FOLLOW.clear()
    cs, cn = {}, {}
    for m, t in tr.items():
        for x in t["trades"]:
            if x["side"] == "buy" and x["signer"] != cr[m]["traderPublicKey"]:
                cs.setdefault(x["signer"], set()).add(cr[m]["traderPublicKey"])
                cn[x["signer"]] = cn.get(x["signer"], 0) + 1
    D.FOLLOW.update({w: (len(cs[w]), cn[w]) for w in cs})
    print(f"{len(creates)} creates, {len(gaps)} feed gaps > {GAP_S:g}s, {len(cr)} clean non-Mayhem scored")
    import calendar
    utc = lambda s_: calendar.timegm(time.strptime(s_, "%Y%m%dT%H%M")) if s_ else None
    t0, t1 = utc(a.start), utc(a.end)
    days = collections.defaultdict(list)
    for m, c in cr.items():
        if (t0 and c["_t"] < t0) or (t1 and c["_t"] >= t1):
            continue
        if c["solAmount"] >= 0.3:
            days[time.strftime("%Y%m%d", time.gmtime(c["_t"]))].append(m)
    for day in sorted(days):
        if a.day and day != a.day:
            continue
        rows = []
        for m in days[day]:
            c = cr[m]
            rows.append({**ex[m], "dev": c["solAmount"], "t30": D.rules(c, D.replay(c, tr[m], 2))["t30"]})
        three = [r for r in rows if 2.95 <= r["dev"] < 3.10]
        band = [r for r in rows if 2.5 <= r["dev"] < 5.5]
        pos = lambda m_, lo: m_ > 0 and lo > 0
        print(f"\nDAY {day} (UTC, by create time): {len(rows)} launches with dev buy >= 0.3")
        print(line("P1  ~3 SOL", [r["t30"] for r in three], pos))
        print(line("P2  ~3 SOL, no create-slot buyers (OURS)", [r["t30"] for r in three if not r["slot0"]], pos))
        h = [r["t30"] for r in band if r["hot_word"]]
        n = [r["t30"] for r in band if not r["hot_word"]]
        nw = len({r["hot_word"] for r in band if r["hot_word"]})
        if len(h) >= 3 and len(n) >= 3:
            d, lo, hi = diff_ci(h, n)
            ok = d > 0 and lo > 0 and nw >= 5
            print(f"  P3  hot - not (2.5-5.5): {d:+.3f} CI[{lo:+.3f},{hi:+.3f}] hot n={len(h)} not n={len(n)} "
                  f"words={nw}  {'PASS' if ok else 'FAIL'}")
        mid = [r["t30"] for r in band if r["post_age"] is not None and 60 <= r["post_age"] < 600]
        fresh = [r["t30"] for r in band if r["post_age"] is not None and r["post_age"] < 60]
        if len(mid) >= 3 and len(fresh) >= 3:
            d, lo, hi = diff_ci(mid, fresh)
            print(f"  P4  post 1-10 min - <60 s: {d:+.3f} CI[{lo:+.3f},{hi:+.3f}] n={len(mid)}/{len(fresh)}  "
                  f"{'PASS' if d > 0 and lo > 0 else 'FAIL'}")
        print("  context:")
        for lo_, hi_ in ((0.3, 0.7), (0.7, 1.5), (1.5, 2.5), (2.5, 5.5)):
            print(line(f"band {lo_}-{hi_}", [r["t30"] for r in rows if lo_ <= r["dev"] < hi_]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
