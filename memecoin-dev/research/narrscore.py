"""EXPLORATORY: score the overnight narrative collection (narrposts.py + snipefeed) and write the training table.

One row per non-Mayhem pump.fun launch in the clean window, with what was VISIBLE AT LAUNCH (name, ticker,
description, image, links, the linked X post's text/author/age, creator and post history up to that moment)
and what HAPPENED in its first hour (did its term catch on, did its post get coined by others, peak market cap,
creator fee, outside buyers). Definitions follow narrative.py: a term is NEW if no coin used it in the previous
6 h; a LEADER is the first coin on a new term that 3+ other creators used within 1 h.

The warm-up and term history come from the VM's tape creates (--history), so the whole laptop window is usable.
Launches whose first hour runs into a feed outage (a trade gap > GAP_S) are cut.

    uv run --with orjson --with numpy --with scikit-learn python narrscore.py \
        --tape ~/tapes/narr-tape-X.jsonl --posts ~/tapes/narr-posts-X.jsonl --history data/vmcreates-X.jsonl.gz \
        --table data/narr-table-X.jsonl.gz [--blind data/narr-blind-X.csv] [--picks picks.csv]
--picks: a CSV of mint[,score] (a gut sheet or a model's output); scored against the base rate of its rows.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import gzip
import re
import sys

import numpy as np
import orjson

sys.path.insert(0, "..")
from devbot.trends import terms  # noqa: E402

AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
NEW_S, HOUR, GAP_S = 6 * 3600, 3600, 120
SELFGRAD_SOL = 50          # creator SOL bought in the first hour; a full pump.fun curve is ~85
FEE = 0.003
SNOWFLAKE_EPOCH_MS = 1288834974657
PROMO_RE = re.compile(r"\b(ca|contract|pump\.?fun|pumpfun|launch(?:ed|ing)?|narrative|dev|send(?:ing)?|cto|"
                      r"\d+x|moon|ape|chart|bonded|bonding|fees?)\b", re.I)


def opener(path: str):
    return gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")


def rows(path: str):
    with opener(path) as f:
        for line in f:
            try:
                yield orjson.loads(line)
            except orjson.JSONDecodeError:
                continue


def post_ts(pid: str) -> float:
    return ((int(pid) >> 22) + SNOWFLAKE_EPOCH_MS) / 1000


def load(a):
    hist = {}                                   # mint -> (ts, creator, name, symbol): every launch either feed saw
    for d in rows(a.history):
        if d.get("t") == "create":
            hist[d["mint"]] = (d["ts"], d["creator"], d.get("name") or "", d.get("symbol") or "")
    creates, meta, trade_ts = {}, {}, []
    agg = collections.defaultdict(lambda: {"peak": 0.0, "vol": 0.0, "buyers": set(), "mayhem": False,
                                           "creator_buy": 0.0})
    for d in rows(a.tape):
        t = d.get("t")
        if t == "create":
            creates[d["mint"]] = d
            hist[d["mint"]] = (d["ts"], d["creator"], d.get("name") or "", d.get("symbol") or "")
        elif t == "meta" and d.get("ok"):
            meta[d["mint"]] = d
        elif t == "trade":
            trade_ts.append(d.get("recv") or d["ts"])
            c = creates.get(d["mint"])
            if c is None or d["ts"] - c["ts"] > HOUR:
                continue
            g = agg[d["mint"]]
            if d["user"] == AGENT:
                g["mayhem"] = True
                continue
            if d.get("v_sol") and d.get("v_tok"):
                g["peak"] = max(g["peak"], d["v_sol"] / d["v_tok"] * 1e9)
            if d["user"] == c["creator"]:
                if d["side"] == "buy":
                    g["creator_buy"] += float(d["sol"])
                continue
            g["vol"] += float(d["sol"])
            if d["side"] == "buy":
                g["buyers"].add(d["user"])
    posts, refs = {}, []
    for d in rows(a.posts):
        if d.get("t") == "post":
            posts[d["id"]] = d
        elif d.get("t") == "post_fail":
            posts.setdefault(d["id"], {"id": d["id"], "fail": d.get("err")})
        elif d.get("t") == "ref":
            refs.append((d["mint"], d["id"]))
    return hist, creates, meta, trade_ts, agg, posts, refs


def clean_window(creates: dict, trade_ts: list[float]) -> tuple[float, float]:
    """[start, end) of launches whose whole first hour the laptop feed saw: end = first gap > GAP_S, minus 1 h."""
    trade_ts.sort()
    start = min(c["ts"] for c in creates.values()) + 60
    stop = trade_ts[-1]
    for x, y in zip(trade_ts, trade_ts[1:]):
        if y - x > GAP_S:
            stop = x
            break
    return start, stop - HOUR


def build(a):
    hist, creates, meta, trade_ts, agg, posts, refs = load(a)
    start, end = clean_window(creates, trade_ts)
    # Mayhem coins out of the term history too, as in narrative.py (only the laptop feed's trades can show it).
    # So are SELF-GRADUATED coins: the creator buys most of the curve itself (brand names, ~85 SOL, 0 outside
    # buyers). They reach $40k mechanically and come in fresh-wallet batches that fake a term "catching on".
    selfgrad = {m for m, g in agg.items() if g["creator_buy"] >= SELFGRAD_SOL}
    drop = selfgrad | {m for m, g in agg.items() if g["mayhem"]}
    order = sorted(((m, v) for m, v in hist.items() if m not in drop), key=lambda kv: kv[1][0])
    by_term = collections.defaultdict(list)        # term -> [(ts, creator, mint)] over the full history
    by_creator = collections.defaultdict(list)
    for m, (ts, cr, name, sym) in order:
        for term in terms(name, sym):
            by_term[term].append((ts, cr, m))
        by_creator[cr].append(ts)
    term_ts = {k: [x[0] for x in v] for k, v in by_term.items()}

    # leaders/followers exactly as narrative.py, over the full history
    leaders, followers, firsts = set(), set(), set()
    t0 = order[0][1][0]
    for term, cs in by_term.items():
        for i, (ts, cr, m) in enumerate(cs):
            if ts - t0 < NEW_S or (i and ts - cs[i - 1][0] <= NEW_S):
                continue
            firsts.add(m)
            j = bisect.bisect_right(term_ts[term], ts + HOUR)
            wave = cs[i + 1:j]
            if len({x[1] for x in wave} - {cr}) >= 3:
                leaders.add(m)
                followers.update(x[2] for x in wave if x[1] != cr)
    followers -= leaders

    post_of = {}
    for m, pid in refs:
        post_of.setdefault(m, pid)
    post_users = collections.defaultdict(list)     # post id -> [(ts, creator, mint)]
    handle_users = collections.defaultdict(list)   # X handle -> [(ts, mint)]
    for m, pid in post_of.items():
        if m in hist:
            post_users[pid].append((hist[m][0], hist[m][1], m))
            h = (posts.get(pid) or {}).get("handle")
            if h:
                handle_users[h.lower()].append((hist[m][0], m))
    for v in list(post_users.values()) + list(handle_users.values()):
        v.sort()

    table, n_selfgrad = [], [0, 0]
    for m, c in creates.items():
        g = agg.get(m)
        if not (start <= c["ts"] < end) or m in drop:
            if start <= c["ts"] < end and m in selfgrad and not g["mayhem"]:
                n_selfgrad[0] += 1
                n_selfgrad[1] += g["peak"] * a.sol_usd >= 40000
            continue
        ts, cr = c["ts"], c["creator"]
        md = meta.get(m, {})
        tset = terms(c.get("name") or "", c.get("symbol") or "")
        wave, prior = 0, 0
        for term in tset:
            tt, cs = term_ts[term], by_term[term]
            i = bisect.bisect_left(tt, ts)
            lo = bisect.bisect_left(tt, ts - NEW_S)
            j = bisect.bisect_right(tt, ts + HOUR)
            prior = max(prior, len({x[1] for x in cs[lo:i]} - {cr}))
            wave = max(wave, len({x[1] for x in cs[i:j] if x[2] != m} - {cr}))
        ctimes = by_creator[cr]
        pid = post_of.get(m)
        p = posts.get(pid) or {}
        row = {
            "mint": m, "ts": ts, "creator": cr, "name": c.get("name"), "symbol": c.get("symbol"),
            "description": md.get("description"), "image": md.get("image"), "twitter": md.get("twitter"),
            "website": md.get("website"), "telegram": md.get("telegram"), "meta_ok": m in meta,
            "terms": sorted(tset),
            # history up to launch (no look-ahead)
            "creator_prior_launches": bisect.bisect_left(ctimes, ts),
            "creator_prior_6h": bisect.bisect_left(ctimes, ts) - bisect.bisect_left(ctimes, ts - NEW_S),
            "term_prior_creators_6h": prior,
            "first_on_new_term": m in firsts,
            "post_id": pid,
        }
        if pid:
            pu = post_users[pid]
            k = bisect.bisect_left([x[0] for x in pu], ts)
            hu = handle_users.get((p.get("handle") or "").lower(), [])
            row.update({
                "post_ok": "text" in p, "post_fail": p.get("fail"), "post_handle": p.get("handle"),
                "post_author": p.get("author"), "post_text": p.get("text"),
                "post_age_s": ts - post_ts(pid),
                "post_prior_coins": len({x[1] for x in pu[:k]} - {cr}),
                "handle_prior_coins": bisect.bisect_left([x[0] for x in hu], ts),
            })
            text = p.get("text") or ""
            sym = (c.get("symbol") or "").lower()
            row["post_promo"] = bool(text) and (
                PROMO_RE.search(text) is not None or (len(sym) >= 2 and f"${sym}" in text.lower())
                or m[:6] in text or "pump" in text.lower())
            # labels
            row["post_wave_1h"] = len({x[1] for x in pu if ts < x[0] <= ts + HOUR} - {cr})
        row.update({
            "term_wave_1h": wave, "leader": m in leaders, "follower": m in followers,
            "peak_sol": g["peak"] if g else 0.0, "fee_sol": FEE * g["vol"] if g else 0.0,
            "outside_buyers": len(g["buyers"]) if g else 0, "creator_buy_sol": g["creator_buy"] if g else 0.0,
        })
        row["hit40k"] = row["peak_sol"] * a.sol_usd >= 40000
        table.append(row)
    table.sort(key=lambda r: r["ts"])
    return table, (start, end), len(creates), n_selfgrad


def cell(lab: str, rs: list[dict], out) -> None:
    if not rs:
        out(f"{lab:44} n      0")
        return
    hit = np.array([r["hit40k"] for r in rs]); fee = np.array([r["fee_sol"] for r in rs])
    lead = np.array([r["leader"] for r in rs])
    out(f"{lab:44} n {len(rs):6,} | P(>=$40k) {hit.mean():6.2%} | leader {lead.mean():6.2%} | "
        f"mean fee {fee.mean():.4f} | median fee {np.median(fee):.5f}")


def boot_ci(x: np.ndarray, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    if len(x) == 0:
        return float("nan"), float("nan")
    ms = x[rng.integers(0, len(x), (n, len(x)))].mean(1)
    return float(np.quantile(ms, 0.025)), float(np.quantile(ms, 0.975))


def text_of(r: dict) -> str:
    parts = [f"n:{r['name'] or ''}", f"s:{r['symbol'] or ''}", f"d:{(r.get('description') or '')[:300]}",
             f"p:{r.get('post_text') or ''}", f"h:{r.get('post_handle') or ''}"]
    return " ".join(parts)


def numeric(r: dict) -> list[float]:
    age = r.get("post_age_s")
    return [np.log1p(r["creator_prior_launches"]), np.log1p(r["creator_prior_6h"]),
            np.log1p(r["term_prior_creators_6h"]), float(bool(r.get("twitter"))), float(bool(r.get("website"))),
            float(bool(r.get("post_id"))), float(bool(r.get("post_ok"))), float(bool(r.get("post_promo"))),
            np.log1p(max(age, 0)) if age is not None else -1.0, np.log1p(r.get("post_prior_coins") or 0),
            np.log1p(r.get("handle_prior_coins") or 0), float(len(r["terms"])),
            float(bool(r.get("image")))]


def model(table: list[dict], target: str, subset, lab: str, out) -> None:
    """Time split 60/40: fit on the earlier launches, score the later ones. Visible-at-launch features only."""
    from scipy import sparse
    from sklearn.feature_extraction.text import HashingVectorizer
    from sklearn.linear_model import LogisticRegression
    rs = [r for r in table if subset(r)]
    if len(rs) < 200:
        out(f"{lab}: n {len(rs)} too few")
        return
    y = np.array([bool(r[target]) for r in rs])
    cut = int(len(rs) * 0.6)
    hv = HashingVectorizer(n_features=2 ** 18, ngram_range=(1, 2), alternate_sign=False, norm="l2")
    X = sparse.hstack([hv.transform([text_of(r) for r in rs]),
                       sparse.csr_matrix(np.array([numeric(r) for r in rs]))]).tocsr()
    if y[:cut].sum() < 5 or y[cut:].sum() < 5:
        out(f"{lab}: too few positives (train {y[:cut].sum()}, test {y[cut:].sum()})")
        return
    fee = np.array([r["fee_sol"] for r in rs]); hit = np.array([r["hit40k"] for r in rs])
    for fold, (tr, te) in enumerate(((slice(0, cut), slice(cut, None)),
                                     (slice(len(rs) - cut, None), slice(0, len(rs) - cut)))):
        clf = LogisticRegression(C=0.5, class_weight="balanced", max_iter=2000).fit(X[tr], y[tr])
        _score(clf.decision_function(X[te]), y[tr], y[te], fee[te], hit[te],
               lab + ("" if fold == 0 else "  [REVERSED: fit later 60%, score earlier 40%]"), out)


def _score(s, ytr, yt, fee, hit, lab, out) -> None:
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(yt, s)
    rng = np.random.default_rng(1)
    aucs = []
    for _ in range(500):
        idx = rng.integers(0, len(yt), len(yt))
        if yt[idx].any() and not yt[idx].all():
            aucs.append(roc_auc_score(yt[idx], s[idx]))
    out(f"{lab}: train n {len(ytr):,} (+{ytr.sum()}), test n {len(yt):,} (+{yt.sum()}), base {yt.mean():.2%} | "
        f"AUC {auc:.3f} [{np.quantile(aucs, .025):.3f}, {np.quantile(aucs, .975):.3f}] | test mean fee "
        f"{fee.mean():.4f}, P($40k) {hit.mean():.2%}")
    for q in (0.01, 0.05, 0.10):
        k = max(1, int(len(yt) * q))
        idx = np.argsort(-s)[:k]
        top = yt[idx]
        lo, hi = boot_ci(top.astype(float))
        flo, fhi = boot_ci(fee[idx])
        out(f"    top {q:4.0%} (k {k:4}): hit {top.mean():6.2%} [{lo:.2%}, {hi:.2%}] | mean fee {fee[idx].mean():.4f} "
            f"[{flo:.4f}, {fhi:.4f}] median {np.median(fee[idx]):.5f} | P($40k) {hit[idx].mean():.2%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--posts", required=True)
    ap.add_argument("--history", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--blind")
    ap.add_argument("--picks")
    ap.add_argument("--sol-usd", type=float, default=107.0)   # same as narrative.py: $40k ~ graduation
    a = ap.parse_args()
    out = print
    table, (start, end), n_creates, n_selfgrad = build(a)
    with gzip.open(a.table, "wb") as f:
        for r in table:
            f.write(orjson.dumps(r) + b"\n")
    import datetime as dt
    iso = lambda x: dt.datetime.fromtimestamp(x, dt.timezone.utc).strftime("%m-%d %H:%MZ")
    out(f"#### {a.tape.split('/')[-1]}: {n_creates:,} creates on the laptop tape; clean launch window "
        f"{iso(start)} -> {iso(end)}; {len(table):,} non-Mayhem rows -> {a.table}")
    out(f"(SOL ${a.sol_usd:.0f}; outcomes are the first hour; leader = first on a new term + 3 other creators in 1 h)")
    out(f"dropped {n_selfgrad[0]:,} SELF-GRADUATED launches (creator bought >= {SELFGRAD_SOL} SOL in the first hour); "
        f"{n_selfgrad[1]:,} of them reached $40k, which would be "
        f"{n_selfgrad[1] / (n_selfgrad[1] + sum(r['hit40k'] for r in table)):.0%} of all $40k 'hits'")
    out("\n## 1. Day-3 replication of the 10-10/10-11 narrative base rates (were 2.1/2.0%, 5.9/5.8%, 0.9/0.9%, 2.0/2.1%)")
    cell("all launches", table, out)
    cell("LEADER: first on a new term that caught on", [r for r in table if r["leader"]], out)
    cell("FOLLOWER: later coin on that term", [r for r in table if r["follower"]], out)
    firsts = [r for r in table if r["first_on_new_term"]]
    cell("first on a new term, UNCONDITIONAL", firsts, out)
    out(f"P(a first-on-new-term coin's term catches on) = {np.mean([r['leader'] for r in firsts]):.2%} "
        f"(was 6.16% / 6.94%)")

    out("\n## 2. The linked X post (the narrative the collector added)")
    no_post = [r for r in table if not r.get("post_id")]
    with_post = [r for r in table if r.get("post_id")]
    cell("no X post linked", no_post, out)
    cell("links an X post", with_post, out)
    cell("  post fetched, promo/dev-style text", [r for r in with_post if r.get("post_promo")], out)
    cell("  post fetched, not promo", [r for r in with_post if r.get("post_ok") and not r.get("post_promo")], out)
    cell("  post deleted/protected/unfetched", [r for r in with_post if not r.get("post_ok")], out)
    for lo, hi, lab in ((-1e9, 600, "< 10 min old"), (600, 3600, "10-60 min"), (3600, 6 * 3600, "1-6 h"),
                        (6 * 3600, 1e12, "> 6 h")):
        cell(f"  post age at launch {lab}", [r for r in with_post if lo <= r["post_age_s"] < hi], out)
    cell("  FIRST coin on that post", [r for r in with_post if r["post_prior_coins"] == 0], out)
    cell("  a later coin on an already-coined post", [r for r in with_post if r["post_prior_coins"] > 0], out)
    fp = [r for r in with_post if r["post_prior_coins"] == 0]
    pw = np.array([r["post_wave_1h"] >= 3 for r in fp])
    out(f"P(first coin on a post -> 3+ other creators coin that post within 1 h) = {pw.mean():.2%} of {len(fp):,}")
    cell("  first on a post that 3+ others then coined", [r for r, w in zip(fp, pw) if w], out)
    hs = collections.Counter((r.get("post_handle") or "?") for r in with_post if r.get("post_ok"))
    out("most-linked handles (the shill/dev accounts to label): " +
        ", ".join(f"{h} {n}" for h, n in hs.most_common(15)))

    out("\n## 3. Can visible-at-launch features PICK? (hashed text of name/ticker/description/post + history; "
        "time split 60/40)")
    model(table, "leader", lambda r: r["first_on_new_term"], "caught on | first on a new term", out)
    model(table, "hit40k", lambda r: True, "reached $40k | all launches", out)
    model(table, "hit40k", lambda r: bool(r.get("post_id")), "reached $40k | links an X post", out)
    fp_set = {r["mint"] for r in fp}
    for r in table:
        r["post_caught"] = (r.get("post_wave_1h") or 0) >= 3
    model(table, "post_caught", lambda r: r["mint"] in fp_set, "post coined by 3+ others | first on a post", out)

    out("\n## 4. Attainability: how fast is the first coin on a term that caught on?")
    L = [r for r in table if r["leader"]]
    ages = np.array([r["post_age_s"] for r in L if r.get("post_id")])
    out(f"leaders linking an X post: {len(ages)} of {len(L)}; post age at launch p10/p25/p50/p75/p90 = "
        + " / ".join(f"{np.percentile(ages, q):.0f}s" for q in (10, 25, 50, 75, 90)))
    by_term = collections.defaultdict(list)
    for r in table:
        for t in r["terms"]:
            by_term[t].append(r)
    lags = []
    for r in L:
        nxt = [x["ts"] - r["ts"] for t in r["terms"] for x in by_term[t]
               if r["ts"] < x["ts"] <= r["ts"] + HOUR and x["creator"] != r["creator"]]
        if nxt:
            lags.append(min(nxt))
    lags = np.array(lags)
    out(f"lead over the next creator on that term p10/p25/p50/p75/p90 = "
        + " / ".join(f"{np.percentile(lags, q):.0f}s" for q in (10, 25, 50, 75, 90))
        + f"; {np.mean(lags <= 5):.0%} of leaders were ahead by <= 5 s")
    sec = [r for r in table if r["follower"] and r.get("post_id") and r["post_prior_coins"] == 1]
    cell("the SECOND coin on a caught-on post", sec, out)

    if a.blind:
        # the later 40% of first-on-new-term coins that link a fetched post: a gut sheet, outcomes hidden
        test = [r for r in firsts[int(len(firsts) * 0.6):] if r.get("post_ok")]
        rng = np.random.default_rng(7)
        pick = [test[i] for i in sorted(rng.choice(len(test), min(200, len(test)), replace=False))]
        with open(a.blind, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["mint", "score", "name", "symbol", "description", "post_handle", "post_text", "image"])
            for r in pick:
                w.writerow([r["mint"], "", r["name"], r["symbol"], (r.get("description") or "")[:200],
                            r.get("post_handle"), (r.get("post_text") or "").replace("\n", " ")[:400], r.get("image")])
        out(f"\nblind sheet: {len(pick)} first-on-new-term coins with a post, outcomes hidden -> {a.blind} "
            f"(base rate in it: {np.mean([r['leader'] for r in pick]):.1%} caught on, "
            f"{np.mean([r['hit40k'] for r in pick]):.1%} reached $40k)")

    if a.picks:
        by = {r["mint"]: r for r in table}
        got = []
        with open(a.picks) as f:
            for rec in csv.reader(f):
                if not rec or rec[0] == "mint" or rec[0] not in by:
                    continue
                s = rec[1].strip() if len(rec) > 1 else "1"
                got.append((by[rec[0]], float(s) if s else 0.0))
        picked = np.array([r["leader"] for r, s in got if s > 0], float)
        rest = np.array([r["leader"] for r, s in got if s <= 0], float)
        lo, hi = boot_ci(picked)
        out(f"\n## picks ({a.picks}): {len(picked)} picked of {len(got)} | caught on {picked.mean():.2%} "
            f"[{lo:.2%}, {hi:.2%}] vs unpicked {rest.mean() if len(rest) else float('nan'):.2%} | "
            f"value ~ 0.06 + h x 0.36 = {0.06 + picked.mean() * 0.36:.3f} SOL mean fee per launch")


if __name__ == "__main__":
    main()
