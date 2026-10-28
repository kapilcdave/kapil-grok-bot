"""Post-hoc diagnostics for the cohort-exit null. DISCLOSED as post-hoc; run
only after the headline came back negative, so it cannot launder a claim.

Two questions:
  1. Where does the cohort's first sell sit among ALL reachable exits?
  2. Does the arm look better on the subset where a cohort even exists?
"""
import collections, json, sys
import pumpcurve as C
import pumporacle as O
import pumpcohort as K

rows, metas = O.load("data/pumptape/w*.jsonl.gz")
virgin, seen, states = O.build_mints(rows, metas)
virgin = O.restrict_covered(virgin, K.L_SLOTS, K.H_MAX)
txc, fee = O.txcost(virgin), C.FEE_BPS / 1e4
clip = 0.3

ranks = {"excl": [], "incl": []}
dts = {"excl": [], "incl": []}
sub = {"excl": [], "incl": []}
oracle_gap = []
for include_birth in (False, True):
    arm = "incl" if include_birth else "excl"
    for key, v in virgin.items():
        tape, birth = v["tape"], v["tape"][0]
        ei = K.entry_index(tape, K.L_SLOTS)
        if ei < 0:
            continue
        ent = tape[ei]
        t_ent = O.ts_of(ent, birth)
        coh, _ = K.cohort_of(tape, include_birth)
        i, dt = K.trigger_index(tape, ei, t_ent, birth, coh, 0.0)
        if i is None:
            continue
        # every reachable exit inside the horizon
        cand = [j for j in range(ei, len(tape))
                if O.ts_of(tape[j], birth) - t_ent <= K.H_MAX]
        vals = [K.pnl(ent, tape[j], clip, fee, txc) for j in cand]
        vals = [x for x in vals if x is not None]
        mine = K.pnl(ent, tape[i], clip, fee, txc)
        if mine is None or len(vals) < 3:
            continue
        ranks[arm].append(sum(1 for x in vals if x < mine) / len(vals))
        dts[arm].append(dt)
        fixv = K.pnl(ent, tape[K.index_at_elapsed(tape, ei, t_ent, birth,
                                                  K.FIX_HOLD)], clip, fee, txc)
        if fixv is not None:
            sub[arm].append((birth["w"], mine - fixv, mine, fixv))
        if include_birth:
            oracle_gap.append((max(vals), mine, fixv))

out = {}
for arm in ("excl", "incl"):
    r = sorted(ranks[arm]); d = sorted(dts[arm])
    te = [x for w, x, _, _ in sub[arm] if w >= 8]
    a_m = sum(m for w, _, m, _ in sub[arm] if w >= 8) / max(1, len(te))
    f_m = sum(f for w, _, _, f in sub[arm] if w >= 8) / max(1, len(te))
    lo, hi = O.bootstrap_mean_ci(te) if len(te) > 3 else (float("nan"),) * 2
    print("\n== cohort=%s, FIRES-ONLY subset (post-hoc) ==" % arm)
    print("  n fired %d   median percentile rank of the cohort-sell exit: %.1f%%"
          % (len(r), 100 * C.median(r)))
    print("  rank p25 %.1f%%  p75 %.1f%%   (50%% = no information)"
          % (100 * r[len(r)//4], 100 * r[3*len(r)//4]))
    print("  time from entry to first cohort sell: median %.1fs  p10 %.1fs  p90 %.1fs"
          % (C.median(d), d[len(d)//10], d[9*len(d)//10]))
    print("  TEST half, fires-only: n=%d  arm %.2f%%  fix %.2f%%  paired %+.2f%%"
          "  [%+.2f%%, %+.2f%%]"
          % (len(te), 100*a_m, 100*f_m, 100*sum(te)/len(te), 100*lo, 100*hi))
    out[arm] = {"n_fired": len(r), "median_rank": C.median(r),
                "rank_p25": r[len(r)//4], "rank_p75": r[3*len(r)//4],
                "median_dt_s": C.median(d),
                "fires_only_test_paired": sum(te)/len(te),
                "fires_only_ci": [lo, hi], "fires_only_arm_mean": a_m}

o = sorted(x[0] for x in oracle_gap)
print("\n== how much of the oracle is on the table (incl, fires-only) ==")
print("  oracle best exit: mean %.2f%%  median %.2f%%   n=%d"
      % (100*sum(o)/len(o), 100*C.median(o), len(o)))
print("  cohort exit captures %.1f%% of the oracle's mean"
      % (100*(sum(x[1] for x in oracle_gap)/len(oracle_gap))
         / (sum(o)/len(o))))
out["oracle_mean"] = sum(o)/len(o)
out["oracle_median"] = C.median(o)
json.dump(out, open("data/pumpcohort_diag.json", "w"), indent=1)
print("\nwrote data/pumpcohort_diag.json")
