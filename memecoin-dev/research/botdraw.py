"""Score PREREG-bot-draw-20261015.md on one snipefeed tape: what makes MORE bots buy in the
first seconds (dev-buy structure, launch lull, gap gradient, last coin's outcome, copy-trade pull).

    uv run --with orjson --with numpy python botdraw.py data/tape-<day>.jsonl.gz
"""
from __future__ import annotations

import argparse
import array
import collections
import gzip
import sys
from pathlib import Path

import numpy as np
import orjson

sys.path.insert(0, str(Path(__file__).resolve().parent))
import levers as L  # noqa: E402  (STD, cluster_ols)

AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
CREATOR_FEE, FEE, LAUNCH_COST, MAYHEM_EXTRA = 0.003, 0.0125, 0.0056, 0.0027
WIN_S = 3600          # fee / NET window, as in bots.py
EARLY_S = 60          # trades kept per coin
FAST_SLOTS = 8        # ~3.2 s: a "fast" separate-tx dev buy
W30_SLOTS = 75        # ~30 s at 400 ms slots
PULL_S = 3            # copy-trade reaction window
PULL_MIN_EV = 30      # events needed to score a wallet's pull
PULL_MAX_SLICE = 500  # skip an event whose +-3 s window is bigger than this (frenzy regime)


def liquidate(vs: float, vt: float, k: float) -> float:
    return vs - vs * vt / (vt + k) if k > 0 else 0.0


def load(path: str):
    """One streaming pass: creates, meta-ok flags, 1 h aggregates, and every trade <= EARLY_S."""
    creates: dict[str, dict] = {}
    ok: dict[str, bool] = {}
    idx: dict[str, int] = {}
    # agg: [vall, vagent, cbuy, csell, ctok, vs, vt, dev_first_slot_off, dev_first_is_create_tx,
    #       devbuy_60s, sbuy, ssell, stok, dev_first_is_signer]
    agg: list[list] = []
    wid: dict[str, int] = {}
    ev_c, ev_dt, ev_sl = array.array("i"), array.array("h"), array.array("h")
    ev_w, ev_sol, ev_side = array.array("i"), array.array("f"), array.array("b")
    n_signer_ne_creator = n_create = 0

    def w_of(u: str) -> int:
        i = wid.get(u)
        if i is None:
            i = wid[u] = len(wid)
        return i

    with gzip.open(path, "rb") as f:
        for line in f:
            try:
                d = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            t = d.get("t")
            if t == "create":
                creates[d["mint"]] = d
                n_create += 1
                n_signer_ne_creator += d.get("user") != d.get("creator")
                if abs(d["v_sol"] * d["v_tok"] / L.STD - 1) <= 0.01:
                    idx[d["mint"]] = len(agg)
                    agg.append([0.0, 0.0, 0.0, 0.0, 0.0, d["v_sol"], d["v_tok"], None, False, 0.0,
                                0.0, 0.0, 0.0, False])
            elif t == "meta":
                ok[d["mint"]] = bool(d.get("ok"))
            elif t == "trade":
                i = idx.get(d["mint"])
                if i is None:
                    continue
                c = creates[d["mint"]]
                dt = d["ts"] - c["ts"]
                if dt > WIN_S or dt < -2:
                    continue
                a = agg[i]
                sol, user, side = float(d["sol"]), d["user"], d["side"]
                a[0] += sol
                if d.get("v_sol") and d.get("v_tok"):
                    a[5], a[6] = d["v_sol"], d["v_tok"]
                if user == AGENT:
                    a[1] += sol
                elif user == c["creator"] or user == c.get("user"):
                    # the dev side is the creator wallet AND the create signer: on 14% of launches the
                    # bundled create-tx buy comes from the signer, which bots.py booked as bot volume
                    is_sig = user != c["creator"]
                    ib, isl, itk = (10, 11, 12) if is_sig else (2, 3, 4)
                    if side == "buy":
                        a[ib] += sol
                        a[itk] += float(d.get("tok") or 0)
                        if dt <= EARLY_S:
                            a[9] += sol
                            if a[7] is None:
                                a[7] = (d.get("slot") or 0) - (c.get("slot") or 0)
                                a[8] = d["sig"] == c["sig"]
                                a[13] = is_sig
                    else:
                        a[isl] += sol
                        a[itk] -= float(d.get("tok") or 0)
                if dt <= EARLY_S:
                    ev_c.append(i)
                    ev_dt.append(int(dt))
                    ev_sl.append(max(-300, min(300, (d.get("slot") or 0) - (c.get("slot") or 0))))
                    ev_w.append(w_of(user))
                    ev_sol.append(sol)
                    ev_side.append(1 if side == "buy" else 0)
    print(f"# {Path(path).name}: {n_create:,} creates ({len(idx):,} standard curve), "
          f"{len(ev_c):,} trades <= {EARLY_S}s, {len(wid):,} wallets; "
          f"create signer != creator on {n_signer_ne_creator / max(n_create, 1):.1%}")
    return creates, ok, idx, agg, wid, (ev_c, ev_dt, ev_sl, ev_w, ev_sol, ev_side)


def pull_stats(coins, wid_n: int):
    """Per-wallet copy-trade PULL: SOL other wallets buy in the 3 s AFTER this wallet's buy minus
    the 3 s before it. A wallet bots copy should pull flow; a wallet nobody watches should not."""
    tot = np.zeros(wid_n)
    n = np.zeros(wid_n)
    sq = np.zeros(wid_n)
    pooled = []
    for dt, _sl, w, sol, side in coins:
        if len(dt) < 2:
            continue
        lo = np.searchsorted(dt, dt - PULL_S, "left")
        hi = np.searchsorted(dt, dt + PULL_S, "right")
        q = np.flatnonzero((side == 1) & (dt >= PULL_S) & (dt <= EARLY_S - PULL_S)
                           & (sol >= 0.05) & (sol <= 10.0) & (hi - lo <= PULL_MAX_SLICE))
        for j in q:
            a, b = lo[j], hi[j]
            bef = float(sol[a:j][(w[a:j] != w[j]) & (side[a:j] == 1)].sum())
            aft = float(sol[j + 1:b][(w[j + 1:b] != w[j]) & (side[j + 1:b] == 1)].sum())
            d = aft - bef
            tot[w[j]] += d
            sq[w[j]] += d * d
            n[w[j]] += 1
            pooled.append(d)
    have = n >= PULL_MIN_EV
    mean = np.where(have, tot / np.maximum(n, 1), np.nan)
    var = np.where(have, sq / np.maximum(n, 1) - np.nan_to_num(mean) ** 2, np.nan)
    se = np.sqrt(np.maximum(var, 0) / np.maximum(n, 1))
    return mean, se, n, have, np.array(pooled)


def build(path: str):
    creates, ok, idx, agg, wid, ev = load(path)
    arrs = [np.frombuffer(a, dtype=np.dtype(a.typecode)) for a in ev]
    order = np.argsort(arrs[0], kind="stable")
    ev_c, ev_dt, ev_sl, ev_w, ev_sol, ev_side = (x[order] for x in arrs)
    bounds = np.searchsorted(ev_c, np.arange(len(agg) + 1))
    agent_id = wid.get(AGENT, -1)
    mints = [None] * len(agg)
    for m, i in idx.items():
        mints[i] = m
    creator_id = np.array([wid.get(creates[m]["creator"], -2) for m in mints])
    signer_id = np.array([wid.get(creates[m].get("user"), -3) for m in mints])

    # per coin, time-sorted views (ts is integer seconds; slot breaks ties)
    coins = []
    for i in range(len(agg)):
        s, e = bounds[i], bounds[i + 1]
        o = np.lexsort((ev_sl[s:e], ev_dt[s:e]))
        coins.append((ev_dt[s:e][o], ev_sl[s:e][o], ev_w[s:e][o], ev_sol[s:e][o], ev_side[s:e][o]))

    # --- broad snipers: wallets in the launch block of many distinct creators ------------------
    # the coin's own create signer is dev-side, not a sniper we drew: a relayer that signs 1,500
    # creates would otherwise be classified broad and inflate every arrival number.
    blockw, blockw_naive = [], []
    cr_of = collections.defaultdict(set)
    cr_of_naive = collections.defaultdict(set)
    for i, (dt, sl, w, sol, side) in enumerate(coins):
        blk = (sl == 0) & (side == 1) & (w != agent_id)
        naive = set(w[blk & (w != creator_id[i])].tolist())
        ws = set(w[blk & (w != creator_id[i]) & (w != signer_id[i])].tolist())
        blockw.append(ws)
        blockw_naive.append(naive)
        cr = creates[mints[i]]["creator"]
        for x in ws:
            cr_of[x].add(cr)
        for x in naive:
            cr_of_naive[x].add(cr)
    broad = {th: {x for x, c in cr_of.items() if len(c) >= th} for th in (20, 50)}
    broad_naive = {x for x, c in cr_of_naive.items() if len(c) >= 50}
    n_sig_slots = sum(len(a) - len(b) for a, b in zip(blockw_naive, blockw))
    n_slots = sum(len(a) for a in blockw_naive)
    print(f"# launch-block buyer slots {n_slots:,}; the coin's own create signer was "
          f"{n_sig_slots / max(n_slots, 1):.1%} of them (dropped)")

    # --- copy-trade pull ----------------------------------------------------------------------
    pmean, pse, pn, phave, pooled = pull_stats(coins, len(wid))
    pullers = set(np.flatnonzero(phave & (pmean > 0) & (pmean > 3 * pse)).tolist())

    # --- launch-rate context (all creates in the tape, not just the standard-curve sample) -----
    all_ts = np.sort(np.array([c["ts"] for c in creates.values()], float))
    slot_count = collections.Counter(c.get("slot") for c in creates.values())

    # --- creator launch order / gaps ----------------------------------------------------------
    prev_ts, prev_mint = {}, {}
    gap, prevm = {}, {}
    for c in sorted(creates.values(), key=lambda c: c["ts"]):
        cr = c["creator"]
        gap[c["mint"]] = c["ts"] - prev_ts[cr] if cr in prev_ts else None
        prevm[c["mint"]] = prev_mint.get(cr)
        prev_ts[cr], prev_mint[cr] = c["ts"], c["mint"]

    # --- per-launch rows ----------------------------------------------------------------------
    rows = []
    for i, (dt, sl, w, sol, side) in enumerate(coins):
        m = mints[i]
        if not ok.get(m):
            continue
        c, a = creates[m], agg[i]
        out = (side == 1) & (w != creator_id[i]) & (w != signer_id[i]) & (w != agent_id)
        mayhem = a[1] > 0
        cost = LAUNCH_COST + (MAYHEM_EXTRA if mayhem else 0.0)
        fee = CREATOR_FEE * (a[0] - a[1])
        net = (fee - (a[2] + a[10]) * (1 + FEE) + (a[3] + a[11]) * (1 - FEE)
               + liquidate(a[5], a[6], max(a[4] + a[12], 0.0)) * (1 - FEE) - cost)
        net_cr = (fee - a[2] * (1 + FEE) + a[3] * (1 - FEE)
                  + liquidate(a[5], a[6], max(a[4], 0.0)) * (1 - FEE) - cost)
        blk = out & (sl == 0)
        w30 = out & (sl > 0) & (sl <= W30_SLOTS)
        late = out & (sl > FAST_SLOTS) & (sl <= W30_SLOTS)   # strictly after any fast dev buy
        early8 = out & (sl >= 0) & (sl <= FAST_SLOTS)
        ws30 = set(w[w30].tolist()) | blockw[i]
        rows.append(dict(
            cr=c["creator"], mint=m, ts=c["ts"], mayhem=mayhem, net=net, net_cr=net_cr,
            fee=fee, devbuy=a[9], dev_slot=a[7], dev_bundled=bool(a[8]), dev_is_signer=bool(a[13]),
            n_block=len(blockw[i]), sol_block=float(sol[blk].sum()),
            n_30s=len(ws30), sol_30s=float(sol[w30 | blk].sum()),
            n_late=len(set(w[late].tolist())), sol_late=float(sol[late].sum()),
            n_early8=len(set(w[early8].tolist())), sol_early8=float(sol[early8].sum()),
            broad50=any(x in broad[50] for x in blockw[i]),
            broad20=any(x in broad[20] for x in blockw[i]),
            broad50_naive=any(x in broad_naive for x in blockw_naive[i]),
            broad50_late=any(x in broad[50] for x in set(w[late].tolist())),
            n_pull_30s=len(ws30 & pullers),
            prev60=float(np.searchsorted(all_ts, c["ts"], "right")
                         - np.searchsorted(all_ts, c["ts"] - 60.0, "left") - 1),
            same_slot=float(slot_count[c.get("slot")] - 1),
            gap=gap[m], prev_mint=prevm[m], hour=int((c["ts"] // 3600) % 24),
            us_hours=13 <= int((c["ts"] // 3600) % 24) <= 22,
        ))
    by_mint = {r["mint"]: r for r in rows}
    for r in rows:
        p = by_mint.get(r["prev_mint"])
        r["prev_broad"] = None if p is None else p["broad50"]
        r["prev_sol30"] = None if p is None else p["sol_30s"]
        r["prev_fee"] = None if p is None else p["fee"]
    return rows, broad, (pmean, pse, pn, phave, pooled, pullers, len(wid))


# ----------------------------------------------------------------------------------------------
def fe(rows, y, cols, names, gkey):
    """Within-group OLS, group-clustered SE; groups with < 2 rows dropped."""
    g = collections.defaultdict(list)
    for r, yy in zip(rows, y):
        g[gkey(r)].append((yy, [float(f(r)) for f in cols]))
    Y, X, G = [], [], []
    for k, lst in g.items():
        if len(lst) < 2:
            continue
        ys = np.array([a for a, _ in lst], float)
        xs = np.array([b for _, b in lst], float)
        Y.extend(ys - ys.mean())
        X.extend(xs - xs.mean(0))
        G.extend([k] * len(lst))
    if not Y:
        return [], np.array([]), np.array([]), 0, 0
    code = {k: i for i, k in enumerate(dict.fromkeys(G))}   # group keys may be tuples
    Y, X, G = np.array(Y), np.array(X), np.array([code[k] for k in G])
    keep = X.std(0) > 1e-12
    beta, se = L.cluster_ols(X[:, keep], Y, G)
    return [n for n, k in zip(names, keep) if k], beta, se, len(set(G)), len(Y)


def report(rows, label, cols, names, outcomes, gkey=lambda r: r["cr"]):
    if len(rows) < 30:
        print(f"\n== {label}: only {len(rows)} launches, not read")
        return
    print(f"\n== {label}  [{len(rows):,} launches]")
    for oname, f in outcomes:
        y = np.array([f(r) for r in rows], float)
        nm, beta, se, nc, n = fe(rows, y, cols, names, gkey)
        if nc < 30:
            print(f"  {oname:22} only {nc} groups, not read")
            continue
        print(f"  {oname:22} (within {nc:,} groups, {n:,} obs, mean y {y.mean():+.4f}): " + ", ".join(
            f"{a} {b:+.4f} (t {b / c:+.1f})" for a, b, c in zip(nm, beta, se)))


def cells(rows, keyf, keys, label):
    print(f"  {label}: n | P(broad blk) | n_block p50 | n_30s p50 | median NET | mean NET")
    for k in keys:
        s = [r for r in rows if keyf(r) == k]
        if len(s) < 30:
            print(f"  {k:14} n={len(s)} (not read)")
            continue
        net = np.array([r["net"] for r in s])
        print(f"  {k:14} {len(s):6,} | {np.mean([r['broad50'] for r in s]):6.1%} | "
              f"{np.median([r['n_block'] for r in s]):5.0f} | {np.median([r['n_30s'] for r in s]):5.0f} | "
              f"{np.median(net):+.4f} | {net.mean():+.4f}")


OUTCOMES = [("P(broad sniper blk)", lambda r: r["broad50"]),
            ("log1p(n_block)", lambda r: np.log1p(r["n_block"])),
            ("log1p(n_30s)", lambda r: np.log1p(r["n_30s"])),
            ("log1p(bot SOL 30s)", lambda r: np.log1p(r["sol_30s"])),
            ("dev NET", lambda r: r["net"])]
OUT_LATE = [("log1p(n_late 3-30s)", lambda r: np.log1p(r["n_late"])),
            ("log1p(SOL late)", lambda r: np.log1p(r["sol_late"])),
            ("P(broad sniper late)", lambda r: r["broad50_late"]),
            ("dev NET", lambda r: r["net"])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tape")
    a = ap.parse_args()
    rows, broad, P = build(a.tape)
    pmean, pse, pn, phave, pooled, pullers, nwal = P
    std = [r for r in rows if not r["mayhem"]]
    print(f"\n######## {Path(a.tape).name}: {len(rows):,} launches in sample, {len(std):,} non-Mayhem")
    print(f"broad snipers: {len(broad[50]):,} wallets (TH=50), {len(broad[20]):,} (TH=20)")
    nb = np.array([r["n_block"] for r in std], float)
    n30 = np.array([r["n_30s"] for r in std], float)
    print(f"dev side: NET mean {np.mean([r['net'] for r in std]):+.4f} vs creator-wallet-only "
          f"{np.mean([r['net_cr'] for r in std]):+.4f} SOL; the create-tx dev buy came from the signer "
          f"on {np.mean([r['dev_is_signer'] for r in std if r['dev_slot'] is not None]):.1%} of launches with one")
    print(f"headroom: P(broad sniper in blk) {np.mean([r['broad50'] for r in std]):.1%} "
          f"(TH20 {np.mean([r['broad20'] for r in std]):.1%}; counting the coin's own signer as a sniper "
          f"it would read {np.mean([r['broad50_naive'] for r in std]):.1%}); n_block p50 {np.median(nb):.0f} "
          f"p90 {np.percentile(nb, 90):.0f} p99 {np.percentile(nb, 99):.0f}; "
          f"n_30s p50 {np.median(n30):.0f} p90 {np.percentile(n30, 90):.0f} p99 {np.percentile(n30, 99):.0f}")

    # ---- H0 reference: dev-buy SIZE with the corrected dev side (exploratory, not prereg) ----
    print("\n#### H0 reference: dev-buy SIZE -> arrivals (dev = creator + create signer, non-Mayhem)")
    db = lambda r: ("none" if r["devbuy"] <= 0 else "le0.1" if r["devbuy"] <= 0.1 else
                    "0.1_0.5" if r["devbuy"] <= 0.5 else "0.5_1" if r["devbuy"] <= 1 else
                    "1_2" if r["devbuy"] <= 2 else "gt2")
    cells(std, db, ["none", "le0.1", "0.1_0.5", "0.5_1", "1_2", "gt2"], "dev buy (60 s)")

    # ---- H1 dev-buy structure ---------------------------------------------------------------
    print("\n#### H1 dev-buy STRUCTURE (dev buy 0.1-2 SOL in the first 60 s, non-Mayhem)")
    h1 = [r for r in std if 0.1 < r["devbuy"] <= 2 and r["dev_slot"] is not None]
    kind = lambda r: ("bundled" if r["dev_bundled"] else
                      "sep_same_slot" if r["dev_slot"] == 0 else
                      "sep_fast_1_8" if r["dev_slot"] <= FAST_SLOTS else "sep_late_gt8")
    cells(h1, kind, ["bundled", "sep_same_slot", "sep_fast_1_8", "sep_late_gt8"], "arm")
    h1f = [r for r in h1 if kind(r) != "sep_late_gt8"]
    report(h1f, "H1 (base = bundled same-tx dev buy)",
           [lambda r: kind(r) == "sep_same_slot", lambda r: kind(r) == "sep_fast_1_8",
            lambda r: np.log1p(r["devbuy"]), lambda r: np.log1p(r["n_early8"]),
            lambda r: r["gap"] is None],
           ["sep_same_slot", "sep_fast_1_8", "log_devbuy", "log_n_early8", "first_in_tape"], OUT_LATE)

    # ---- H2 launch lull ---------------------------------------------------------------------
    print("\n#### H2 launch LULL")
    pv = np.array([r["prev60"] for r in std], float)
    print(f"  creates in prior 60 s: p10 {np.percentile(pv, 10):.0f} p50 {np.median(pv):.0f} "
          f"p90 {np.percentile(pv, 90):.0f}; same-slot creates p50 "
          f"{np.median([r['same_slot'] for r in std]):.0f} p90 {np.percentile([r['same_slot'] for r in std], 90):.0f}")
    q10, q90 = np.percentile(pv, [10, 90])
    cells(std, lambda r: ("quiet_decile" if r["prev60"] <= q10 else
                          "busy_decile" if r["prev60"] >= q90 else "mid"),
          ["quiet_decile", "mid", "busy_decile"], "prior-60 s decile")
    cols2 = [lambda r: np.log1p(r["prev60"]), lambda r: np.log1p(r["same_slot"]),
             lambda r: r["gap"] is None, lambda r: np.log1p(r["devbuy"]), lambda r: r["us_hours"]]
    names2 = ["log_creates_prev60", "log_same_slot", "first_in_tape", "log_devbuy", "us_hours"]
    report(std, "H2 within creator", cols2, names2, OUTCOMES)
    report(std, "H2 within creator x hour (same-window control)", cols2[:4], names2[:4], OUTCOMES,
           gkey=lambda r: (r["cr"], r["hour"]))

    # ---- H3 gap gradient --------------------------------------------------------------------
    print("\n#### H3 GAP gradient (non-Mayhem)")
    gb = lambda r: ("first_in_tape" if r["gap"] is None else
                    "lt1h" if r["gap"] < 3600 else "1_3h" if r["gap"] < 3 * 3600 else
                    "3_8h" if r["gap"] < 8 * 3600 else "gt8h")
    cells(std, gb, ["lt1h", "1_3h", "3_8h", "gt8h", "first_in_tape"], "gap bucket")
    report(std, "H3 within creator (base = gap < 1 h)",
           [lambda r: gb(r) == "1_3h", lambda r: gb(r) == "3_8h", lambda r: gb(r) == "gt8h",
            lambda r: gb(r) == "first_in_tape", lambda r: np.log1p(r["devbuy"]), lambda r: r["us_hours"]],
           ["gap_1_3h", "gap_3_8h", "gap_gt8h", "first_in_tape", "log_devbuy", "us_hours"], OUTCOMES)

    # ---- H4 last coin's visible outcome -----------------------------------------------------
    print("\n#### H4 LAST COIN's outcome -> next launch (gap >= 1 h, so the prior hour is complete)")
    h4 = [r for r in std if r["gap"] is not None and r["gap"] >= 3600 and r["prev_broad"] is not None]
    if len(h4) >= 30:
        qq = np.percentile([r["prev_sol30"] for r in h4], [50, 90])
        print(f"  prev coin's first-30 s SOL: p50 {qq[0]:.3f} p90 {qq[1]:.3f}; prev drew a broad sniper on "
              f"{np.mean([r['prev_broad'] for r in h4]):.1%}")
        cells(h4, lambda r: "prev_sniper" if r["prev_broad"] else "prev_none",
              ["prev_sniper", "prev_none"], "prev launch")
    report(h4, "H4 within creator",
           [lambda r: r["prev_broad"], lambda r: np.log1p(r["prev_sol30"]),
            lambda r: np.log1p(r["devbuy"]), lambda r: np.log1p(r["gap"] / 3600), lambda r: r["us_hours"]],
           ["prev_got_sniper", "log_prev_sol30", "log_devbuy", "log_gap_h", "us_hours"], OUTCOMES)

    # ---- combined cell: what arrivals do we buy by setting every lever we control? ----------
    print("\n#### COMBINED (non-Mayhem): fresh wallet = no launch by this creator for >= 3 h; "
          "quiet = 0 other creates in our slot")
    fresh = lambda r: r["gap"] is None or r["gap"] >= 3 * 3600
    print("  cell: n | P(broad blk) | n_30s p50 | n_30s mean | bot SOL 30s p50 | mean | median NET")
    for flab, ff in (("fresh+quiet", lambda r: fresh(r) and r["same_slot"] == 0),
                     ("stale/busy", lambda r: not (fresh(r) and r["same_slot"] == 0))):
        for dlab, dsel in (("devbuy 0", lambda r: r["devbuy"] <= 0),
                           ("devbuy 0.5-2", lambda r: 0.5 < r["devbuy"] <= 2),
                           ("devbuy >2", lambda r: r["devbuy"] > 2)):
            s = [r for r in std if ff(r) and dsel(r)]
            if len(s) < 30:
                print(f"  {flab:12} {dlab:13} n={len(s)} (not read)")
                continue
            n30 = np.array([r["n_30s"] for r in s], float)
            sol = np.array([r["sol_30s"] for r in s], float)
            print(f"  {flab:12} {dlab:13} {len(s):6,} | {np.mean([r['broad50'] for r in s]):6.1%} | "
                  f"{np.median(n30):5.0f} | {n30.mean():6.1f} | {np.median(sol):7.3f} | {sol.mean():7.3f} | "
                  f"{np.median([r['net'] for r in s]):+.4f}")

    # ---- H5 copy-trade pull -----------------------------------------------------------------
    print("\n#### H5 COPY-TRADE pull (descriptive): SOL others buy 3 s after a wallet's buy minus 3 s before")
    print(f"  pooled over {len(pooled):,} buys: mean {pooled.mean():+.4f} median {np.median(pooled):+.4f} SOL")
    idx = np.flatnonzero(phave)
    if len(idx):
        mu, t = pmean[idx], pmean[idx] / np.maximum(pse[idx], 1e-12)
        print(f"  {len(idx):,} wallets with >= {PULL_MIN_EV} scored buys: mean pull p50 {np.median(mu):+.4f} "
              f"p90 {np.percentile(mu, 90):+.4f} p99 {np.percentile(mu, 99):+.4f} max {mu.max():+.4f} SOL")
        print(f"  pull > 0 at t >= 3: {np.mean(t >= 3):.1%} of them ({len(pullers):,} wallets); "
              f"their mean pull {pmean[list(pullers)].mean() if pullers else float('nan'):+.4f} SOL, "
              f"median events {np.median(pn[list(pullers)]) if pullers else float('nan'):.0f}")
        top = idx[np.argsort(-mu)][:5]
        print("  top-5 pull wallets: " + ", ".join(f"{pmean[i]:+.3f} SOL (n {pn[i]:.0f}, t {pmean[i]/max(pse[i],1e-12):+.1f})" for i in top))
    nf = np.array([r["n_pull_30s"] for r in std], float)
    with_p = [r["broad50"] for r in std if r["n_pull_30s"] > 0]
    without = [r["broad50"] for r in std if r["n_pull_30s"] == 0]
    if with_p and without:
        print(f"  a pull wallet buys in our first 30 s on {np.mean(nf > 0):.1%} of launches; "
              f"P(broad blk) {np.mean(with_p):.1%} with vs {np.mean(without):.1%} without")


if __name__ == "__main__":
    main()
