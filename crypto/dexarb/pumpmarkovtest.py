"""Closed-form and synthetic checks on the Markov stopping machinery.

Everything here has a hand-derivable answer, so a wrong implementation shows up
as a mismatch rather than as a plausible P&L. Run before trusting pumpmarkov.py.

The two checks that matter most:

  * 5. CAUSALITY -- the state at epoch k is recomputed from a tape TRUNCATED at
    slot(k) and required to be bit-identical. The mark bin contains the P&L by
    design (a stopping problem's state must carry its payoff), which means a
    forward leak inside `mark` would be invisible in the P&L. This is the only
    thing standing between that design and a fake number.
  * 6. OPTIMALITY -- on a synthetic chain whose optimal policy is derivable by
    hand, backward induction must return that policy and its exact value.

    python3 pumpmarkovtest.py
"""
import math
import random
import sys

import pumpcurve as C
import pumpmarkov as M
import pumporacle as O

FAIL = []


def check(name, got, want, tol=1e-9):
    ok = abs(got - want) <= tol * max(1.0, abs(want))
    print("  %-62s %s" % (name, "ok" if ok else "FAIL got %.10g want %.10g"
                          % (got, want)))
    if not ok:
        FAIL.append(name)


def check_true(name, ok):
    print("  %-62s %s" % (name, "ok" if ok else "FAIL"))
    if not ok:
        FAIL.append(name)


# ------------------------------------------------------------------ tape fixture
def synth_tape(w, mint, birth_slot, moves, bt=1_700_000_000):
    """A curve born at birth_slot, then one trade per entry in `moves`.

    moves is a list of (slot_offset, buy, sol_sol). Reserves are advanced by the
    exact constant-product update so the tape is internally consistent and
    `reserve_consistent` holds."""
    vsol, vtok = 30.0 * C.LAMPORTS, float(C.INITIAL_VTOK)
    rows = []
    for off, buy, sol in moves:
        x = sol * C.LAMPORTS
        k = vsol * vtok
        if buy:
            vsol2 = vsol + x
            vtok2 = k / vsol2
            tok = vtok - vtok2
        else:
            vsol2 = vsol - x
            vtok2 = k / vsol2
            tok = vtok2 - vtok
        rows.append({"mint": mint, "user": "u", "ts": bt + int(off * O.SLOT_SEC),
                     "buy": 1 if buy else 0, "sol": x, "tok": tok,
                     "vsol": vsol2, "vtok": vtok2, "slot": birth_slot + off,
                     "bt": bt + int(off * O.SLOT_SEC), "sig": "s", "fee": 55000,
                     "w": w})
        vsol, vtok = vsol2, vtok2
    return rows


def wrap(rows, birth_slot, span=4000):
    return {"tape": rows, "end_slot": birth_slot + span,
            "end_ts": rows[0]["bt"] + span * O.SLOT_SEC}


def main():
    fee = C.FEE_BPS / 1e4

    print("1. binning is half-open on the right and the top bin is open")
    check("mark just below an edge lands in the lower bin",
          M._bin(-0.3001, M.MARK_EDGES), 0)
    check("mark exactly on an edge lands in the lower bin",
          M._bin(-0.30, M.MARK_EDGES), 0)
    check("mark above the last edge lands in the open top bin",
          M._bin(0.3001, M.MARK_EDGES), M.N_MARK - 1)
    check("virgin curve vsol=30 is progress bin 0",
          M._bin(30.0, M.VSOL_EDGES), 0)
    check("graduated-ish vsol=115 is the top progress bin",
          M._bin(115.0, M.VSOL_EDGES), M.N_VSOL - 1)

    print("\n2. state index is a bijection on the whole 168-state space")
    seen = set()
    for m in range(M.N_MARK):
        for v in range(M.N_VSOL):
            for f in range(M.N_FLOW):
                for q in range(M.N_STALE):
                    s = M.state_index(m, v, f, q)
                    seen.add(s)
                    check_true("unpack(index(%d,%d,%d,%d)) round trips"
                               % (m, v, f, q) if s == 0 else
                               "unpack round trip s=%d" % s,
                               M.unpack_state(s) == (m, v, f, q))
    check("index covers exactly N_STATE values", len(seen), M.N_STATE)
    check("index is contiguous from 0", max(seen), M.N_STATE - 1)

    print("\n3. age groups: 4 groups, monotone, boundaries where declared")
    check("epoch 0 is age group 0", M.AGE_OF[0], 0)
    check("k*4.8s crosses 30s at k=7 -> group 1", M.AGE_OF[7], 1)
    check("last epoch is the last age group", M.AGE_OF[M.N_EPOCH - 1],
          M.N_AGE - 1)
    check_true("age group never decreases",
               all(M.AGE_OF[k] <= M.AGE_OF[k + 1]
                   for k in range(M.N_EPOCH - 1)))
    check("grid spans 297.6 s", (M.N_EPOCH - 1) * M.DSLOT * O.SLOT_SEC, 297.6,
          1e-9)

    print("\n4. a FLAT curve: no trade after birth, so every epoch is identical")
    print("   and the only cost is fee + fixed + own impact. Analytic: with the")
    print("   tape fixed, selling into the pre-buy state returns S*c/(S+2c)")
    print("   before fees, so net = (1-r)^2*S/(S+2c) - 1 - t/c - 2c/(S+2c).")
    bs = 1_000_000
    rows = synth_tape(0, "flat", bs, [(0, True, 0.5)])
    v = wrap(rows, bs)
    ep = M.episode(v, fee, M.CLIP_SOL, M.TXC_SOL)
    check_true("episode built", ep is not None)
    if ep:
        c, S = M.CLIP_SOL, rows[0]["vsol"] / C.LAMPORTS
        # buy moves the curve by x=c/(1+r); tape is fixed so we sell tok back
        # into the state as it was. Exact closed form of the fixed-tape leg:
        x = c / (1.0 + fee)
        tok_frac = x / (S + x)                # share of vtok we hold
        # selling tok into the UNPERTURBED state (vsol=S, vtok=T):
        #   y = S - S*T/(T+tok) = S*tok/(T+tok), with tok/T = x/(S+x)
        y = S * tok_frac / (1.0 + tok_frac) * 1.0
        want_p = (y * (1.0 - fee)) / c
        check("pobs is flat across all epochs",
              max(ep["pobs"]) - min(ep["pobs"]), 0.0, 1e-12)
        check("pobs matches the closed-form fixed-tape round trip",
              ep["pobs"][0], want_p, 1e-9)
        check("pfill equals pobs when nothing trades in the L_OUT gap",
              ep["pfill"][0], ep["pobs"][0], 1e-12)
        check("own impact term is 2c/(S+2c)", ep["impact"],
              2 * c / (S + 2 * c), 1e-12)
        check("fixed term is t/c", ep["fixed"], M.TXC_SOL / c, 1e-12)
        net = M.net_of(ep, 0)
        check("net_of = pfill - 1 - t/c - impact", net,
              ep["pfill"][0] - 1 - M.TXC_SOL / c - 2 * c / (S + 2 * c), 1e-12)
        check_true("a flat curve loses roughly the cost floor",
                   -0.05 < net < -0.015)
        check("grid oracle on a flat curve equals the instant round trip",
              M.grid_oracle([ep])["mean_frac"], net, 1e-12)
        check("anti-oracle equals it too (no dispersion to exploit)",
              M.grid_oracle([ep])["antioracle_frac"], net, 1e-12)
        check("staleness is fresh at k=0", M.unpack_state(ep["st"][0])[3], 0)
        check("staleness is stale by the last epoch",
              M.unpack_state(ep["st"][M.N_EPOCH - 1])[3], 1)

    print("\n5. CAUSALITY -- the state at epoch k must be identical when every")
    print("   trade after slot(k) is deleted from the tape. The mark bin holds")
    print("   the P&L by construction, so a forward leak would be invisible in")
    print("   the P&L; this is the check that rules it out.")
    rng = random.Random(4242)
    moves = [(0, True, 0.4)]
    off = 1
    for _ in range(260):
        off += rng.randrange(1, 14)
        moves.append((off, rng.random() < 0.55, 0.02 + rng.random() * 0.8))
    rows = synth_tape(0, "walk", bs, moves)
    full = M.episode(wrap(rows, bs, span=4000), fee, M.CLIP_SOL, M.TXC_SOL)
    check_true("episode built on the random walk", full is not None)
    if full:
        bad_state = bad_obs = 0
        for k in (0, 1, 5, 17, 31, 44, M.N_EPOCH - 1):
            slot_k = rows[0]["slot"] + M.L_IN + k * M.DSLOT
            trunc = [r for r in rows if r["slot"] <= slot_k]
            # entry needs the birth trade plus L_IN slots, always present here
            tep = M.episode(wrap(trunc, bs, span=4000), fee, M.CLIP_SOL,
                            M.TXC_SOL)
            if tep is None:
                bad_state += 1
                continue
            if tep["st"][k] != full["st"][k]:
                bad_state += 1
            if abs(tep["pobs"][k] - full["pobs"][k]) > 1e-12 * full["pobs"][k]:
                bad_obs += 1
        check("state at k is unchanged by deleting everything after slot(k)",
              bad_state, 0)
        check("observable mark at k likewise", bad_obs, 0)
        # and the converse: pfill MAY differ, because it is allowed to see the
        # L_OUT slots we cannot. If it never differs, L_OUT is not being applied.
        diff = sum(1 for k in range(M.N_EPOCH)
                   if abs(full["pfill"][k] - full["pobs"][k]) > 1e-12)
        check_true("pfill differs from pobs on some epochs (L_OUT is real)",
                   diff > 0)
        print("     (pfill != pobs on %d of %d epochs)" % (diff, M.N_EPOCH))

    print("\n6. OPTIMALITY -- a two-state chain whose optimal policy is")
    print("   derivable by hand. State A pays 0 and moves to B with prob p;")
    print("   state B is absorbing and pays +1. Continuing from A is worth")
    print("   1-(1-p)^(T-k), which exceeds A's payoff of 0 for every k<T, so")
    print("   the optimal rule is HOLD in A and STOP in B, value 1-(1-p)^T.")
    p = 0.10
    A_s, B_s = 0, 1
    model = {"trans": [{} for _ in range(M.N_AGE)],
             "payoff": [0.0] * M.N_STATE, "pay_n": [1] * M.N_STATE,
             "pooled": 0.0}
    model["payoff"][A_s] = 0.0
    model["payoff"][B_s] = 1.0
    import collections as _c
    for g in range(M.N_AGE):
        model["trans"][g] = {A_s: _c.Counter({B_s: 0, A_s: 0}),
                             B_s: _c.Counter({B_s: 0})}
    # build counts that give exactly p after Laplace smoothing:
    #   (nB + a) / (nA + nB + 2a) = p  ->  choose nA, nB on a large total
    tot = 100000
    nB = int(round(p * (tot + 2 * M.ALPHA) - M.ALPHA))
    nA = tot - nB
    for g in range(M.N_AGE):
        model["trans"][g][A_s] = _c.Counter({A_s: nA, B_s: nB})
        model["trans"][g][B_s] = _c.Counter({B_s: tot})
    rowsA = M.kernel_rows(model["trans"][0])[A_s]
    p_hat = dict(zip(*rowsA))[B_s]
    check("smoothed P(A->B) is p to 1e-6", p_hat, p, 1e-5)
    pol = M.snell(model)
    T = M.N_EPOCH - 1
    check("V_0(A) = 1-(1-p)^T", pol["V"][0][A_s], 1 - (1 - p_hat) ** T, 1e-9)
    check("V_0(B) = 1 (absorbing, pays now)", pol["V"][0][B_s], 1.0, 1e-12)
    check_true("policy HOLDs in A at every epoch before the horizon",
               all(not pol["stop"][k][A_s] for k in range(T)))
    check_true("policy STOPs in B at every epoch",
               all(pol["stop"][k][B_s] for k in range(M.N_EPOCH)))
    check_true("forced stop in A at the horizon",
               pol["stop"][T][A_s])
    # The continuation from A is 1-(1-p)^62 = 0.99829, which is why 0.99 would
    # NOT flip it -- the mirror test has to clear the actual continuation value.
    cont0 = 1 - (1 - p_hat) ** T
    print("     (continuation from A at k=0 is %.5f, so the flip test must"
          " exceed it)" % cont0)
    check_true("0.99 does NOT flip it, because 0.99 < the continuation",
               not M.snell(dict(model, payoff=[0.99 if s == A_s else
                                               model["payoff"][s]
                                               for s in range(M.N_STATE)]))
               ["stop"][0][A_s])
    model["payoff"][A_s] = 1.5
    pol2 = M.snell(model)
    check_true("a payoff above the continuation flips it to STOP",
               pol2["stop"][0][A_s])
    check("V_0(A) is then A's own payoff", pol2["V"][0][A_s], 1.5, 1e-12)
    model["payoff"][A_s] = 0.0

    print("\n6b. full-support smoothing shifts V but not the ranking. From A it")
    print("    leaks 0.5/tot onto each of the 166 zero-paying states, so V_0(A)")
    print("    must come out BELOW the observed-support value, never above.")
    polf = M.snell(model, full_support=True)
    check_true("V_0(A) under full support is lower than under observed support",
               polf["V"][0][A_s] < pol["V"][0][A_s])
    check_true("but still holds in A and stops in B",
               not polf["stop"][0][A_s] and polf["stop"][0][B_s])
    print("     (observed %.5f vs full %.5f)"
          % (pol["V"][0][A_s], polf["V"][0][A_s]))

    print("\n7. score() honours the entry gate and the forced horizon exit")
    #  A curve parked in A forever: policy holds, so it must exit at k=T.
    ep_hold = {"st": [A_s] * M.N_EPOCH,
               "pobs": [1.0] * M.N_EPOCH, "pfill": [1.0] * M.N_EPOCH,
               "impact": 0.0, "fixed": 0.0, "w": 0}
    model["payoff"][A_s] = 0.0
    pol = M.snell(model)
    r = M.score([ep_hold] * 5, pol)
    check("V_0(A)>0 so all 5 are entered", r["n"], 5)
    check("every one is held to the horizon", r["stop_at_H"], 1.0)
    check("net is pfill-1 = 0", r["mean_frac"], 0.0, 1e-12)
    #  Now make V_0 <= 0 for A: entry gate must reject everything.
    model["payoff"] = [-1.0] * M.N_STATE
    for g in range(M.N_AGE):
        model["trans"][g] = {A_s: _c.Counter({A_s: tot})}
    pol = M.snell(model)
    r = M.score([ep_hold] * 5, pol)
    check("a state with V_0<=0 is never entered", r["n"], 0)
    check("and all 5 are counted as skipped", r["skipped"], 5)

    print("\n8. the placebos preserve what they claim to preserve")
    if full:
        eps = [full] * 3 + [M.episode(wrap(rows, bs, span=4000), fee,
                                      M.CLIP_BIG, M.TXC_SOL)] * 2
        eps = [e for e in eps if e]
        rng = random.Random(1)
        sh = M.perturb(eps, "shuffle", rng)
        check("shuffle keeps the episode count", len(sh), len(eps))
        for a, b in zip(eps, sh):
            check("shuffle preserves pobs[0] exactly", b["pobs"][0],
                  a["pobs"][0], 1e-12)
            check("shuffle preserves the TERMINAL mark exactly",
                  b["pobs"][M.N_EPOCH - 1], a["pobs"][M.N_EPOCH - 1], 1e-9)
            da = sorted(round(math.log(a["pobs"][k]) - math.log(a["pobs"][k - 1]), 12)
                        for k in range(1, M.N_EPOCH))
            db = sorted(round(math.log(b["pobs"][k]) - math.log(b["pobs"][k - 1]), 12)
                        for k in range(1, M.N_EPOCH))
            check("shuffle is a permutation of the same increments",
                  sum(abs(x - y) for x, y in zip(da, db)), 0.0, 1e-9)
            check("shuffle preserves the L_OUT slip at each epoch",
                  b["pfill"][3] / b["pobs"][3], a["pfill"][3] / a["pobs"][3],
                  1e-12)
            check("shuffle preserves v/f/q at each epoch",
                  sum(abs(M.unpack_state(x)[1] - M.unpack_state(y)[1])
                      + abs(M.unpack_state(x)[2] - M.unpack_state(y)[2])
                      + abs(M.unpack_state(x)[3] - M.unpack_state(y)[3])
                      for x, y in zip(a["st"], b["st"])), 0.0, 1e-12)
            break
        dn = M.perturb(eps, "donor", random.Random(2))
        check("donor keeps the episode count", len(dn), len(eps))
        check_true("donor draws only from the same window",
                   all(a["w"] == b["w"] for a, b in zip(eps, dn)))

    print("\n9. loglik: a deterministic chain is perfectly predicted, and")
    print("   independence on a deterministic chain is strictly worse")
    #  loglik smooths over the FULL 168-state support, as a predictive score
    #  must, so a thin row is dragged toward uniform. The cycle therefore needs
    #  enough counts per row to overcome alpha*168 = 84 before order1 gets near
    #  perfect: log((n+.5)/(n+84)) -> 0 only for n >> 84.
    det = [{"st": [(k % 3) for k in range(M.N_EPOCH)], "pobs": [1.0] * M.N_EPOCH,
            "pfill": [1.0] * M.N_EPOCH, "impact": 0.0, "fixed": 0.0, "w": 0}
           for _ in range(2000)]
    ll = M.loglik(det, det)
    check_true("order1 beats independence on a deterministic cycle",
               ll["gain_order1_over_indep"] > 0.5)
    check_true("order1 log-lik is near zero once rows outweigh the prior "
               "(got %+.4f)" % ll["order1"], ll["order1"] > -0.05)
    #  an i.i.d. state sequence: order1 must NOT beat independence out of sample.
    #  The marginal has to be NON-uniform, or heavier smoothing is free -- a
    #  uniform marginal coincides with the Laplace prior, so the order-2 model's
    #  thinner rows would win for the wrong reason.
    r2 = random.Random(9)

    def skewed():
        return min(M.N_STATE - 1, int(r2.expovariate(1.0 / 12.0)))

    iid_tr = [{"st": [skewed() for _ in range(M.N_EPOCH)],
               "pobs": [1.0] * M.N_EPOCH, "pfill": [1.0] * M.N_EPOCH,
               "impact": 0.0, "fixed": 0.0, "w": 0} for _ in range(400)]
    iid_te = [{"st": [skewed() for _ in range(M.N_EPOCH)],
               "pobs": [1.0] * M.N_EPOCH, "pfill": [1.0] * M.N_EPOCH,
               "impact": 0.0, "fixed": 0.0, "w": 0} for _ in range(400)]
    ll2 = M.loglik(iid_tr, iid_te)
    check_true("order1 does NOT beat independence on i.i.d. states "
               "(gain %+.4f <= 0)" % ll2["gain_order1_over_indep"],
               ll2["gain_order1_over_indep"] <= 0)
    check_true("order2 does NOT beat order1 on i.i.d. states either "
               "(gain %+.4f <= 0)" % ll2["gain_order2_over_order1"],
               ll2["gain_order2_over_order1"] <= 0)

    print("\n10. payoff estimation falls back to the pooled mean on unvisited")
    print("    states rather than to zero -- a zero would read as 'break even'")
    print("    and quietly beat a genuinely negative state")
    m = M.fit([ep_hold])
    check("visited state keeps its own mean", m["payoff"][A_s], 0.0, 1e-12)
    check("unvisited state gets the pooled mean, not 0.0",
          m["payoff"][M.N_STATE - 1], m["pooled"], 1e-12)

    print("\n%s" % ("ALL CHECKS PASSED" if not FAIL
                    else "%d FAILURES: %s" % (len(FAIL), FAIL[:6])))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
