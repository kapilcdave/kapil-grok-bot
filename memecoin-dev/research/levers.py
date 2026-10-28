"""Score PREREG-human-attention-levers-20261013.md on one snipefeed tape.

    uv run --with orjson --with numpy python levers.py data/tape-<day>.jsonl.gz [--cap 10]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import gzip
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import orjson

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devbot.trends import terms  # noqa: E402

STD = 30.0 * 1.073e9          # v_sol * v_tok of a standard fresh curve
WINDOW_S = 3600
MIN_DELAY_S = 2
HOT_LOOKBACK_S = 1800
HOT_MIN = 10


def tw_kind(u: str | None) -> str:
    if not u:
        return "none"
    u = u.lower()
    if "/status/" in u and ("x.com/" in u or "twitter.com/" in u):
        return "x_post"
    if "/communities/" in u:
        return "other"
    if "x.com/" in u or "twitter.com/" in u:
        return "x_profile"
    return "other"


def norm(s: str) -> str:
    return re.sub(r"[\W_]", "", (s or "").lower())


def load(path: str):
    creates, metas, trades = {}, {}, collections.defaultdict(list)
    with gzip.open(path, "rb") as f:
        for line in f:
            try:
                d = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            t = d.get("t")
            if t == "create":
                creates[d["mint"]] = d
            elif t == "meta":
                metas[d["mint"]] = d
            elif t == "trade":
                trades[d["mint"]].append((d["ts"], d["user"], d["side"], float(d["sol"]), d["sig"]))
    return creates, metas, trades


def cluster_ols(X: np.ndarray, y: np.ndarray, groups: np.ndarray):
    n, k = X.shape
    bread = np.linalg.inv(X.T @ X)
    beta = bread @ X.T @ y
    u = y - X @ beta
    _, gi = np.unique(groups, return_inverse=True)
    G = gi.max() + 1
    scores = np.zeros((G, k))
    np.add.at(scores, gi, X * u[:, None])
    meat = scores.T @ scores
    V = bread @ meat @ bread * (G / (G - 1)) * ((n - 1) / (n - k))
    return beta, np.sqrt(np.diag(V))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tape")
    ap.add_argument("--cap", type=int, default=10)
    ap.add_argument("--other", help="adjacent day's tape: a human must also buy there (Amendment A)")
    a = ap.parse_args()
    creates, metas, trades = load(a.tape)
    returning = None
    if a.other:
        oc, _om, ot = load(a.other)
        returning = {u for mint, rows in ot.items() if mint in oc for _t, u, side, *_ in rows if side == "buy"}
        del oc, ot

    # mints bought per wallet over the whole tape (for the human cap)
    bought = collections.defaultdict(set)
    for mint, rows in trades.items():
        if mint in creates:
            for _ts, user, side, _sol, _sig in rows:
                if side == "buy":
                    bought[user].add(mint)

    # per coin: human first-buy times, outcomes
    sample, human_times = [], {}
    tot_buyers = tot_human = 0
    vol_all = vol_human = 0.0
    for mint, c in creates.items():
        t0, creator = c["ts"], c["creator"]
        first: dict[str, int] = {}
        V = hsol = 0.0
        devbuy = 0.0
        for ts, user, side, sol, sig in trades.get(mint, ()):
            if sig == c["sig"] and user == creator and side == "buy":
                devbuy += sol
            if ts - t0 > WINDOW_S:
                continue
            if user != creator:
                V += sol
            if side == "buy" and user != creator:
                first.setdefault(user, ts)
        humans = {u: ts for u, ts in first.items()
                  if ts - t0 >= MIN_DELAY_S and len(bought[u]) <= a.cap
                  and (returning is None or u in returning)}
        for ts, user, side, sol, _sig in trades.get(mint, ()):
            if side == "buy" and user in humans and ts - t0 <= WINDOW_S:
                hsol += sol
        human_times[mint] = sorted(humans.values())
        std = abs(c["v_sol"] * c["v_tok"] / STD - 1) <= 0.01
        m = metas.get(mint)
        if not std or not m or not m.get("ok"):
            continue
        tot_buyers += len(first)
        tot_human += len(humans)
        vol_all += V
        vol_human += hsol
        sample.append((mint, c, m, len(humans), V, hsol, devbuy))

    # creator's earlier launches in this tape, and hot-copy flag
    order = sorted(creates.values(), key=lambda c: c["ts"])
    prior_launches, seen = {}, collections.Counter()
    by_term: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    hot_copy = {}
    for c in order:
        prior_launches[c["mint"]] = seen[c["creator"]]
        seen[c["creator"]] += 1
        ts_terms = terms(c.get("name") or "", c.get("symbol") or "")
        hot = False
        for term in ts_terms:
            for t_prev, m_prev in reversed(by_term[term]):
                if c["ts"] - t_prev > HOT_LOOKBACK_S:
                    break
                if bisect.bisect_left(human_times.get(m_prev, []), c["ts"]) >= HOT_MIN:
                    hot = True
                    break
            if hot:
                break
        hot_copy[c["mint"]] = hot
        for term in ts_terms:
            by_term[term].append((c["ts"], c["mint"]))

    names = ["const", "x_post", "x_profile", "x_other", "website", "telegram", "description",
             "prior_1_4", "prior_5plus", "devbuy_le1", "devbuy_gt1", "tool_j7", "tool_other",
             "hot_copy", "non_ascii", "all_caps", "ticker_eq_name", "us_hours"]
    X, H, P5, groups, Vs, HS = [], [], [], [], [], []
    for mint, c, m, h, V, hsol, devbuy in sample:
        kind = tw_kind(m.get("twitter"))
        host = urlparse(c.get("uri") or "").netloc
        name, sym = c.get("name") or "", c.get("symbol") or ""
        pl = prior_launches[mint]
        hour = (c["ts"] // 3600) % 24
        X.append([1, kind == "x_post", kind == "x_profile", kind == "other", bool(m.get("website")),
                  bool(m.get("telegram")), bool(m.get("description")), 1 <= pl <= 4, pl >= 5,
                  0 < devbuy <= 1, devbuy > 1, "j7tracker" in host, host not in ("ipfs.io",) and "j7tracker" not in host,
                  hot_copy[mint], not name.isascii(), name.isupper(), norm(name) == norm(sym), 13 <= hour <= 22])
        H.append(h)
        P5.append(h >= 5)
        groups.append(c["creator"])
        Vs.append(V)
        HS.append(hsol)
    X = np.asarray(X, float)
    H = np.asarray(H, float)
    groups = np.asarray(groups)

    print(f"tape {Path(a.tape).name}: {len(creates):,} creates, {len(sample):,} in sample (standard curve + meta ok)")
    print(f"buyers (non-creator, all): {tot_buyers:,}; human-like (cap {a.cap}): {tot_human:,} ({tot_human / max(tot_buyers, 1):.1%})")
    print(f"non-creator volume {vol_all:,.0f} SOL; human buy SOL {vol_human:,.0f} ({vol_human / max(vol_all, 1e-9):.1%})")
    print(f"H per coin: median {np.median(H):.0f}, mean {H.mean():.2f}, P(H=0) {np.mean(H == 0):.1%}, "
          f"P(H>=5) {np.mean(H >= 5):.1%}, P(H>=20) {np.mean(H >= 20):.1%}")
    print(f"lever prevalence: " + ", ".join(f"{n} {X[:, i].mean():.1%}" for i, n in enumerate(names) if i))
    for label, y in (("log1p(H)", np.log1p(H)), ("P(H>=5)", np.asarray(P5, float)),
                     ("log1p(V SOL)", np.log1p(np.asarray(Vs)))):
        beta, se = cluster_ols(X, y, groups)
        print(f"\n== {label}  (n={len(y):,}, creators={len(set(groups)):,}, mean y {y.mean():.3f})")
        for i, n in enumerate(names):
            mult = f"x{np.exp(beta[i]):.2f}" if label.startswith("log1p") and i else ""
            print(f"  {n:15} {beta[i]:+.4f}  se {se[i]:.4f}  t {beta[i] / se[i]:+6.1f}  {mult}")


if __name__ == "__main__":
    main()
