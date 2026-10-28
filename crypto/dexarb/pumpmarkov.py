"""A Markov chain over pump.fun curve states, and the optimal stopping policy it implies.

Pre-registered in preregistrations/pumpfun_markov_stopping_20260918.md.

FINDINGS_pumpfun.md closed the fresh-curve scalp on a 32-cell menu of exit rules
(fixed hold / tp-sl / trailing) and an 8-feature entry screen, searched separately.
Neither searched the space of STATE-DEPENDENT stopping rules, and neither coupled
entry to the exit value. That matters because the perfect-foresight exit bound on
the same tape is +38.26%, so there is a 48 pp realisation gap and the question is
whether it is unreachable information or unsearched policy space.

This module answers that. It discretises the observable curve state, estimates a
time-inhomogeneous transition kernel on the earlier half of the windows, and solves
the Snell envelope by backward induction. Backward induction returns the EXACTLY
OPTIMAL stopping policy for that state space -- the supremum of the whole policy
family, not one more member of it -- so a negative out-of-sample result closes the
family rather than one rule (`oracle-bound-a-selection-closes-a-model-family`).

Everything is read-only against archived blocks already on disk. No key, no signing.

    python3 pumpmarkovtest.py                     # run FIRST, closed-form checks
    python3 pumpmarkov.py --json data/pumpmarkov.json
"""
import argparse
import collections
import json
import math
import random
import sys

import pumpcurve as C
import pumporacle as O
import pumpcausal as A

# ---------------------------------------------------------------- frozen config
L_IN = 2                 # slots after birth we can first act in (Geyser-class)
L_OUT = 2                # slots between deciding to leave and our sell landing
DSLOT = 12               # decision-epoch spacing, slots (4.8 s)
N_EPOCH = 63             # k = 0..62, spanning 744 slots ~= 297.6 s
H_SEC = 300              # coverage requirement, seconds
CLIP_SOL = 0.0406        # measured minimiser of the cost floor
CLIP_BIG = 1.0           # capacity row, reported not headline
TXC_SOL = 0.000110       # 2x median observed on-chain fee
ALPHA = 0.5              # Laplace smoothing on every kernel
SEED = 20260918
N_PLACEBO = 20
MINTS_PER_DAY = 21000    # measured base rate, FINDINGS_pumpfun.md
SOL_USD = 150.0          # only enters the $/day gate; reported so it can be rescaled
BAR_USD_DAY = 333.0      # $10k/month

# mark bins: log(proceeds now / clip). Upper edges; last bin is open.
MARK_EDGES = (-0.30, -0.15, -0.05, 0.05, 0.15, 0.30)
N_MARK = len(MARK_EDGES) + 1                      # 7
VSOL_EDGES = (35.0, 45.0, 70.0)                   # virtual SOL reserve, SOL
N_VSOL = len(VSOL_EDGES) + 1                      # 4
FLOW_EDGES = (0.4, 0.7)                           # buy share of the last 5 trades
N_FLOW = len(FLOW_EDGES) + 1                      # 3
STALE_SLOTS = 5                                   # q: <=5 fresh, >5 stale
N_STALE = 2
N_STATE = N_MARK * N_VSOL * N_FLOW * N_STALE      # 168
AGE_EDGES = (30.0, 90.0, 180.0)                   # seconds; 4 age groups
N_AGE = len(AGE_EDGES) + 1
FLOW_LOOKBACK = 5


def _bin(x, edges):
    for i, e in enumerate(edges):
        if x <= e:
            return i
    return len(edges)


def state_index(m, v, f, q):
    return ((m * N_VSOL + v) * N_FLOW + f) * N_STALE + q


def unpack_state(s):
    q = s % N_STALE
    s //= N_STALE
    f = s % N_FLOW
    s //= N_FLOW
    v = s % N_VSOL
    return s // N_VSOL, v, f, q


def age_group(k):
    return _bin(k * DSLOT * O.SLOT_SEC, AGE_EDGES)


AGE_OF = [age_group(k) for k in range(N_EPOCH)]


# ------------------------------------------------------------ per-curve episode
def episode(v, fee, clip_sol, txc_sol):
    """Discretise one curve into the epoch series the chain runs on.

    Returns None if the curve is not enterable or not covered, else a dict with,
    for every epoch k = 0..62:

      st[k]    state index, built ONLY from trades at slot <= slot(k)
      pobs[k]  sell proceeds at the observable state, as a multiple of the clip
      pfill[k] sell proceeds at the state left by the last trade at
               slot <= slot(k) + L_OUT -- the state our sell actually lands in,
               which we do not get to see when we decide
               (`an-exit-price-must-not-know-the-exit-decision`)

    The realised net fraction of stopping at k is then
    pfill[k] - 1 - txc/clip - impact, assembled by `net_of`.
    """
    tape, birth = v["tape"], v["tape"][0]
    ei, ent = A.entry_of(v, L_IN)
    if ent is None or not A.covered(v, ent, H_SEC):
        return None
    clip = clip_sol * C.LAMPORTS
    tok, _, _ = C.buy_fill(ent["vsol"], ent["vtok"], clip, fee)
    if tok <= 0:
        return None

    s0 = ent["slot"]
    # own round-trip impact on a curve holding S virtual SOL, as a clip fraction
    S = ent["vsol"] / C.LAMPORTS
    impact = 2.0 * clip_sol / (S + 2.0 * clip_sol)

    st, pobs, pfill = [], [], []
    i_obs, i_fill = ei, ei
    for k in range(N_EPOCH):
        slot_k = s0 + k * DSLOT
        # last trade we can SEE at the decision. Monotone pointer, never rewinds.
        while i_obs + 1 < len(tape) and tape[i_obs + 1]["slot"] <= slot_k:
            i_obs += 1
        # last trade in the ledger when our sell lands, L_OUT slots later. Any
        # trade in between is ahead of us and moves the state against us.
        while i_fill + 1 < len(tape) and tape[i_fill + 1]["slot"] <= slot_k + L_OUT:
            i_fill += 1

        ro, rf = tape[i_obs], tape[i_fill]
        out_o, _, _ = C.sell_fill(ro["vsol"], ro["vtok"], tok, fee)
        out_f, _, _ = C.sell_fill(rf["vsol"], rf["vtok"], tok, fee)
        po, pf = out_o / clip, out_f / clip
        if po <= 0.0:
            return None

        m = _bin(math.log(po), MARK_EDGES)
        vb = _bin(ro["vsol"] / C.LAMPORTS, VSOL_EDGES)
        lo = max(0, i_obs - FLOW_LOOKBACK + 1)
        recent = tape[lo:i_obs + 1]
        fb = _bin(sum(1 for r in recent if r["buy"]) / len(recent), FLOW_EDGES)
        qb = 0 if slot_k - ro["slot"] <= STALE_SLOTS else 1

        st.append(state_index(m, vb, fb, qb))
        pobs.append(po)
        pfill.append(pf)

    return {"st": st, "pobs": pobs, "pfill": pfill, "impact": impact,
            "fixed": txc_sol / clip_sol, "w": v["tape"][0]["w"]}


def net_of(ep, k):
    """Net fraction of the clip from stopping at epoch k. Both fee legs are
    already inside pfill (buy fee via the clip, sell fee via sell_fill)."""
    return ep["pfill"][k] - 1.0 - ep["fixed"] - ep["impact"]


# ---------------------------------------------------------------- kernel + Snell
def fit(eps):
    """Transition counts by age group, and the mean realised stop payoff per
    state. The payoff is the MEASURED bin mean, not the bin centre: a centre
    would inject a discretisation bias of unknown sign, and the top mark bin is
    unbounded so it has no centre at all."""
    trans = [collections.defaultdict(collections.Counter) for _ in range(N_AGE)]
    pay_sum = [0.0] * N_STATE
    pay_n = [0] * N_STATE
    for ep in eps:
        st = ep["st"]
        for k in range(N_EPOCH):
            s = st[k]
            pay_sum[s] += net_of(ep, k)
            pay_n[s] += 1
            if k + 1 < N_EPOCH:
                trans[AGE_OF[k]][s][st[k + 1]] += 1
    pooled = (sum(pay_sum) / sum(pay_n)) if sum(pay_n) else 0.0
    payoff = [pay_sum[s] / pay_n[s] if pay_n[s] else pooled for s in range(N_STATE)]
    return {"trans": trans, "payoff": payoff, "pay_n": pay_n, "pooled": pooled}


def kernel_rows(trans_g, full=False):
    """Smoothed rows as (states, probs) pairs.

    Two conventions, and they are not interchangeable, so both are run:

    * `full=False` (primary) -- Laplace over the OBSERVED support of the row, so
      an unvisited row degenerates to 'stay put' rather than to a uniform over
      168 states the process never reached.
    * `full=True` -- Laplace over all N_STATE. This is the convention `loglik`
      must use, because a predictive score has to assign mass to a test
      transition it never saw in training. It shrinks every thin row toward
      uniform, which shrinks edges.

    The policy is solved under both and both are reported. If the verdict moves
    between them the result is a smoothing artifact, which is worth knowing; the
    pre-registered gate is on the primary.
    """
    rows = {}
    for s, cnt in trans_g.items():
        if full:
            tot = sum(cnt.values()) + ALPHA * N_STATE
            rows[s] = (list(range(N_STATE)),
                       [(cnt.get(t, 0) + ALPHA) / tot for t in range(N_STATE)])
        else:
            keys = list(cnt)
            tot = sum(cnt.values()) + ALPHA * len(keys)
            rows[s] = (keys, [(cnt[k] + ALPHA) / tot for k in keys])
    return rows


def snell(model, full_support=False):
    """V[k][s] = max(payoff[s], E[V[k+1][s']]). Backward induction over the
    fitted kernel, so V is the value of the OPTIMAL stopping rule on this state
    space -- nothing in the family beats it by construction."""
    payoff = model["payoff"]
    rows = [kernel_rows(model["trans"][g], full_support) for g in range(N_AGE)]
    V = [None] * N_EPOCH
    V[N_EPOCH - 1] = list(payoff)                 # forced exit at the horizon
    for k in range(N_EPOCH - 2, -1, -1):
        nxt, r = V[k + 1], rows[AGE_OF[k]]
        cur = [0.0] * N_STATE
        for s in range(N_STATE):
            row = r.get(s)
            cont = sum(p * nxt[t] for t, p in zip(*row)) if row else nxt[s]
            cur[s] = payoff[s] if payoff[s] >= cont else cont
        V[k] = cur
    # stop iff the frozen payoff is at least the frozen continuation value
    stop = [[False] * N_STATE for _ in range(N_EPOCH)]
    for k in range(N_EPOCH - 1):
        nxt, r = V[k + 1], rows[AGE_OF[k]]
        for s in range(N_STATE):
            row = r.get(s)
            cont = sum(p * nxt[t] for t, p in zip(*row)) if row else nxt[s]
            stop[k][s] = payoff[s] >= cont
    for s in range(N_STATE):
        stop[N_EPOCH - 1][s] = True
    return {"V": V, "stop": stop}


def score(eps, pol, entry_gate=True):
    """Run the frozen policy. Entry couples to the exit value: we take the trade
    only where V_0 says the optimal stopping problem is worth entering."""
    nets, ks, v0s, skipped = [], [], [], 0
    for ep in eps:
        s0 = ep["st"][0]
        if entry_gate and pol["V"][0][s0] <= 0.0:
            skipped += 1
            continue
        k = N_EPOCH - 1
        for j in range(N_EPOCH):
            if pol["stop"][j][ep["st"][j]]:
                k = j
                break
        nets.append(net_of(ep, k))
        ks.append(k)
        v0s.append(pol["V"][0][s0])
    if not nets:
        return {"n": 0, "skipped": skipped, "entry_rate": 0.0}
    n = len(nets)
    mean = sum(nets) / n
    lo, hi = O.bootstrap_mean_ci(nets)
    # The optimism gap. V_0 is a max over estimated continuation values, so it
    # inherits the upward bias of a max over noise; the realised net is what the
    # same policy actually collects. The gap is what backward induction
    # manufactures from estimation error, and it is the quantity that decides
    # whether this family can ever work, not the sign of V_0.
    vmean = sum(v0s) / n
    return {"n": n, "skipped": skipped, "entry_rate": n / (n + skipped),
            "mean_v0": vmean, "optimism_gap": vmean - mean,
            "mean_frac": mean, "median_frac": C.median(sorted(nets)),
            "ci_lo_frac": lo, "ci_hi_frac": hi,
            "p_win": sum(1 for x in nets if x > 0) / n,
            "mean_stop_epoch": sum(ks) / n,
            "stop_at_0": sum(1 for x in ks if x == 0) / n,
            "stop_at_H": sum(1 for x in ks if x == N_EPOCH - 1) / n}


def grid_oracle(eps):
    """G2. Perfect foresight over the stopping EPOCH, still filling L_OUT slots
    late. Must trade -- the k=0 state is a candidate so the floor is the cost of
    an instant round trip. Declining to trade would be selection on the outcome.
    This bounds every policy on this grid, so it is priced before any kernel is
    fitted (`price-the-required-contrast-before-fitting`)."""
    best = [max(net_of(ep, k) for k in range(N_EPOCH)) for ep in eps]
    worst = [min(net_of(ep, k) for k in range(N_EPOCH)) for ep in eps]
    n = len(best)
    lo, hi = O.bootstrap_mean_ci(best)
    return {"n": n, "mean_frac": sum(best) / n, "ci_lo_frac": lo,
            "ci_hi_frac": hi, "median_frac": C.median(sorted(best)),
            "antioracle_frac": sum(worst) / n,
            "hold_H_frac": sum(net_of(ep, N_EPOCH - 1) for ep in eps) / n,
            "instant_frac": sum(net_of(ep, 0) for ep in eps) / n}


# ------------------------------------------------------- is it Markov at all? G1
def loglik(train, test):
    """Mean OOS log-likelihood per transition of three nested models fitted on
    train and scored on test: independence, order 1, order 2 (previous mark bin
    appended). A claim about the venue, reported whatever the P&L does."""
    marg = [collections.Counter() for _ in range(N_AGE)]
    o1 = [collections.defaultdict(collections.Counter) for _ in range(N_AGE)]
    o2 = [collections.defaultdict(collections.Counter) for _ in range(N_AGE)]
    for ep in train:
        st = ep["st"]
        for k in range(N_EPOCH - 1):
            g, s, t = AGE_OF[k], st[k], st[k + 1]
            marg[g][t] += 1
            o1[g][s][t] += 1
            prev = unpack_state(st[k - 1])[0] if k > 0 else N_MARK
            o2[g][(prev, s)][t] += 1

    def lp(cnt, t, support):
        tot = sum(cnt.values()) + ALPHA * support
        return math.log((cnt[t] + ALPHA) / tot) if tot > 0 else -math.log(N_STATE)

    acc = {"independence": 0.0, "order1": 0.0, "order2": 0.0}
    n = 0
    for ep in test:
        st = ep["st"]
        for k in range(N_EPOCH - 1):
            g, s, t = AGE_OF[k], st[k], st[k + 1]
            prev = unpack_state(st[k - 1])[0] if k > 0 else N_MARK
            acc["independence"] += lp(marg[g], t, N_STATE)
            acc["order1"] += lp(o1[g][s], t, N_STATE)
            acc["order2"] += lp(o2[g][(prev, s)], t, N_STATE)
            n += 1
    out = {k: v / n for k, v in acc.items()}
    out["n_transitions"] = n
    out["gain_order1_over_indep"] = out["order1"] - out["independence"]
    out["gain_order2_over_order1"] = out["order2"] - out["order1"]
    return out


# ------------------------------------------------------------------ placebos, G4
def perturb(eps, mode, rng):
    """Rebuild episodes with the conditional information destroyed and the
    unconditional return distribution preserved, then let the WHOLE pipeline
    refit on them -- kernel, payoff, policy and score. A placebo that reuses the
    real policy would price only the scoring step."""
    if mode == "donor":
        by_w = collections.defaultdict(list)
        for i, ep in enumerate(eps):
            by_w[ep["w"]].append(i)
    out = []
    for i, ep in enumerate(eps):
        if mode == "shuffle":
            # permute the epoch-to-epoch log mark increments. Terminal return,
            # volatility and age structure are preserved EXACTLY; only the time
            # ordering dies. The null for an optimal-stopping claim: if leaving
            # early has value, order has to matter.
            lm = [math.log(p) for p in ep["pobs"]]
            d = [lm[k] - lm[k - 1] for k in range(1, N_EPOCH)]
            rng.shuffle(d)
            new = [lm[0]]
            for x in d:
                new.append(new[-1] + x)
            pobs = [math.exp(x) for x in new]
            # keep each epoch's multiplicative L_OUT slip attached to its epoch
            slip = [ep["pfill"][k] / ep["pobs"][k] for k in range(N_EPOCH)]
            pfill = [pobs[k] * slip[k] for k in range(N_EPOCH)]
            src = ep
        else:
            pool = by_w[ep["w"]]
            j = pool[rng.randrange(len(pool))]
            don = eps[j]
            pobs, pfill, src = don["pobs"], don["pfill"], don
        # rebuild only the mark component; v/f/q keep their original values
        st = []
        for k in range(N_EPOCH):
            _, vb, fb, qb = unpack_state(ep["st"][k])
            st.append(state_index(_bin(math.log(pobs[k]), MARK_EDGES), vb, fb, qb))
        out.append({"st": st, "pobs": pobs, "pfill": pfill,
                    "impact": ep["impact"], "fixed": ep["fixed"], "w": ep["w"]})
    return out


def placebo(tr, te, mode, draws, seed):
    means = []
    for b in range(draws):
        rng = random.Random(seed + 1000 * b + (0 if mode == "shuffle" else 7))
        m = fit(perturb(tr, mode, rng))
        r = score(perturb(te, mode, rng), snell(m))
        means.append(r.get("mean_frac", 0.0) if r["n"] else 0.0)
    means.sort()
    n = len(means)
    return {"draws": n, "p50": C.median(means), "p95": means[min(n - 1, int(.95 * n))],
            "max": means[-1], "mean": sum(means) / n}


# --------------------------------------------------------------------- $/day, G5
def per_day(mean_frac, entry_rate, clip_sol, sol_usd=SOL_USD):
    entries = MINTS_PER_DAY * entry_rate
    return {"entries_per_day": entries,
            "turnover_usd_day": entries * clip_sol * sol_usd,
            "usd_day": entries * clip_sol * mean_frac * sol_usd,
            "sol_usd": sol_usd, "bar_usd_day": BAR_USD_DAY}


# -------------------------------------------------------------------------- main
def build(virgin, fee, clip_sol, label=""):
    eps, dropped = [], 0
    for v in virgin.values():
        ep = episode(v, fee, clip_sol, TXC_SOL)
        if ep is None:
            dropped += 1
        else:
            eps.append(ep)
    print("  %sepisodes %d, dropped %d (unenterable or uncovered)"
          % (label, len(eps), dropped))
    return eps, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="data/pumptape/w*.jsonl.gz")
    ap.add_argument("--json")
    ap.add_argument("--placebo-draws", type=int, default=N_PLACEBO)
    ap.add_argument("--limit-windows", type=int, default=0)
    args = ap.parse_args()

    fee = C.FEE_BPS / 1e4
    print("loading %s" % args.glob)
    rows, metas = O.load(args.glob)
    if args.limit_windows:
        keep = set(sorted(m["window"] for m in metas)[:args.limit_windows])
        metas = [m for m in metas if m["window"] in keep]
        rows = [r for r in rows if r["w"] in keep]
    print("  %d trades, %d windows" % (len(rows), len(metas)))
    virgin, seen, states = O.build_mints(rows, metas)
    print("  %d curves seen, %d born in-window, initial states %s"
          % (seen, len(virgin), states))

    tr_v, te_v = A.split_windows(virgin, metas)
    if tr_v is None:
        print("need >= 4 windows"); return 1
    print("  train %d curves, test %d curves" % (len(tr_v), len(te_v)))

    out = {"config": {"L_in": L_IN, "L_out": L_OUT, "dslot": DSLOT,
                      "n_epoch": N_EPOCH, "h_sec": H_SEC, "clip_sol": CLIP_SOL,
                      "clip_big": CLIP_BIG, "txc_sol": TXC_SOL, "alpha": ALPHA,
                      "n_state": N_STATE, "n_age": N_AGE, "fee_bps": C.FEE_BPS,
                      "seed": SEED, "windows": len(metas), "trades": len(rows),
                      "curves": len(virgin)}}

    for clip in (CLIP_SOL, CLIP_BIG):
        tag = "clip_%g" % clip
        print("\n=== %s ===" % tag)
        tr, _ = build(tr_v, fee, clip, "train ")
        te, _ = build(te_v, fee, clip, "test  ")
        if not tr or not te:
            continue
        res = {}

        print("\n G1  Markov content, OOS log-likelihood per transition")
        ll = loglik(tr, te)
        for k in ("independence", "order1", "order2"):
            print("   %-14s %+.4f nats" % (k, ll[k]))
        print("   order1 - indep  %+.4f   order2 - order1 %+.4f  (n=%d)"
              % (ll["gain_order1_over_indep"], ll["gain_order2_over_order1"],
                 ll["n_transitions"]))
        res["loglik"] = ll
        res["G1_pass"] = ll["gain_order1_over_indep"] > 0

        print("\n G2  oracle over the discretised grid (test windows)")
        orc = grid_oracle(te)
        c_star, floor, fee_share = A.cost_floor(30.0, fee, TXC_SOL)
        print("   cost floor %+.2f%% at c*=%.4f SOL (%.0f%% of it the 95bps fee)"
              % (100 * floor, c_star, 100 * fee_share))
        print("   perfect-foresight epoch  %+.2f%% [%+.2f, %+.2f]"
              % (100 * orc["mean_frac"], 100 * orc["ci_lo_frac"],
                 100 * orc["ci_hi_frac"]))
        print("   instant round trip       %+.2f%%   hold to H %+.2f%%   anti-oracle %+.2f%%"
              % (100 * orc["instant_frac"], 100 * orc["hold_H_frac"],
                 100 * orc["antioracle_frac"]))
        res["grid_oracle"] = orc
        res["cost_floor"] = {"c_star": c_star, "floor": floor,
                             "fee_share": fee_share}
        res["G2_pass"] = orc["mean_frac"] > floor

        print("\n fitting kernel on train, solving backward induction")
        model = fit(tr)
        pol = snell(model)
        visited = sum(1 for n in model["pay_n"] if n > 0)
        print("   states visited in train %d of %d; transitions %d"
              % (visited, N_STATE,
                 sum(sum(c.values()) for g in model["trans"] for c in g.values())))
        res["states_visited"] = visited

        ins = score(tr, pol)
        print("\n     in-sample  net %+.2f%%  entry %.1f%%  n=%d"
              % (100 * ins["mean_frac"], 100 * ins["entry_rate"], ins["n"]))
        oos = score(te, pol)
        res["train"] = ins
        res["test"] = oos
        if not oos["n"]:
            print(" G3  no test entries at all -- V_0 <= 0 everywhere reachable")
            res["G3_pass"] = False
        else:
            print(" G3  TEST       net %+.2f%% [%+.2f, %+.2f]  entry %.1f%%  "
                  "n=%d  p_win %.3f"
                  % (100 * oos["mean_frac"], 100 * oos["ci_lo_frac"],
                     100 * oos["ci_hi_frac"], 100 * oos["entry_rate"],
                     oos["n"], oos["p_win"]))
            print("     mean stop epoch %.1f (%.1f s); stop immediately %.1f%%, "
                  "ride to H %.1f%%"
                  % (oos["mean_stop_epoch"],
                     oos["mean_stop_epoch"] * DSLOT * O.SLOT_SEC,
                     100 * oos["stop_at_0"], 100 * oos["stop_at_H"]))
            print("     fitted V_0 %+.2f%% vs realised %+.2f%%  ->  OPTIMISM GAP "
                  "%+.2f pp  (in-sample gap %+.2f pp)"
                  % (100 * oos["mean_v0"], 100 * oos["mean_frac"],
                     100 * oos["optimism_gap"], 100 * ins["optimism_gap"]))
            res["G3_pass"] = oos["mean_frac"] > 0 and oos["ci_lo_frac"] > 0
            # no-entry-gate variant: does the gate help or hurt?
            res["test_all_entries"] = score(te, pol, entry_gate=False)
            ng = res["test_all_entries"]
            print("     without the entry gate: net %+.2f%% on n=%d"
                  % (100 * ng["mean_frac"], ng["n"]))
            # same kernel, full-support smoothing -- the convention loglik must
            # use. A verdict that moves between the two is a smoothing artifact.
            fs = score(te, snell(model, full_support=True))
            res["test_full_support"] = fs
            print("     full-support smoothing: net %+.2f%% on n=%d (entry %.1f%%)"
                  % (100 * fs["mean_frac"] if fs["n"] else float("nan"),
                     fs["n"], 100 * fs["entry_rate"]))

        if args.placebo_draws:
            gating = bool(res.get("G3_pass"))
            print("\n G4  placebos, %d draws each (full refit)%s"
                  % (args.placebo_draws,
                     "" if gating else "  [INFORMATIONAL: G3 already failed]"))
            for mode in ("shuffle", "donor"):
                pb = placebo(tr, te, mode, args.placebo_draws, SEED)
                print("   %-8s p50 %+.2f%%  p95 %+.2f%%  max %+.2f%%"
                      % (mode, 100 * pb["p50"], 100 * pb["p95"], 100 * pb["max"]))
                res["placebo_" + mode] = pb
            res["placebo_is_gating"] = gating
            # Reported either way. If a null that has destroyed the time
            # ordering scores the same as the real policy, the policy was never
            # using the ordering -- which is the whole claim of a stopping rule.
            res["G4_pass"] = (oos["mean_frac"] > res["placebo_shuffle"]["p95"]
                              if gating else None)
        else:
            res["G4_pass"] = None

        if res.get("G3_pass"):
            pd = per_day(oos["mean_frac"], oos["entry_rate"], clip)
            print("\n G5  $/day  %.0f entries/day, $%.0f turnover, $%.0f/day vs bar $%.0f"
                  % (pd["entries_per_day"], pd["turnover_usd_day"],
                     pd["usd_day"], pd["bar_usd_day"]))
            res["per_day"] = pd
            res["G5_pass"] = pd["usd_day"] >= BAR_USD_DAY
        else:
            res["G5_pass"] = None

        out[tag] = res

    # robustness: the reserve-consistent subset, at c* only
    print("\n=== robustness: reserve-consistent curves only (c*) ===")
    cons = {k: v for k, v in virgin.items() if O.reserve_consistent(v)}
    print("  %d of %d curves (%.1f%%)"
          % (len(cons), len(virgin), 100 * len(cons) / max(len(virgin), 1)))
    ctr_v, cte_v = A.split_windows(cons, metas)
    if ctr_v:
        ctr, _ = build(ctr_v, fee, CLIP_SOL, "train ")
        cte, _ = build(cte_v, fee, CLIP_SOL, "test  ")
        if ctr and cte:
            corc = grid_oracle(cte)
            cpol = snell(fit(ctr))
            cres = score(cte, cpol)
            print("   grid oracle %+.2f%%   policy test %+.2f%% (n=%d, entry %.1f%%)"
                  % (100 * corc["mean_frac"],
                     100 * cres.get("mean_frac", float("nan")), cres["n"],
                     100 * cres["entry_rate"]))
            out["reserve_consistent"] = {
                "n_curves": len(cons), "share": len(cons) / max(len(virgin), 1),
                "grid_oracle": corc, "test": cres}

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1, default=float)
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
