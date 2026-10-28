"""Self-tests for pumpcohort.py. Synthetic tape built forward through the real
curve arithmetic, so reserves are internally consistent by construction."""
import math
import random
import sys

import pumpcurve as C
import pumpcohort as K

FEE = C.FEE_BPS / 1e4
FAILS = []


def chk(name, cond, extra=""):
    print("%-58s %s %s" % (name, "ok" if cond else "FAIL", extra))
    if not cond:
        FAILS.append(name)


def mk(trades, mint="M", w=0, slot0=1000, bt0=1_700_000_000):
    """trades: list of (slot_offset, user, is_buy, sol_lamports_or_tok).

    Buys spend `amt` lamports of curve-moving SOL; sells sell `amt` tokens.
    Emits records with POST-trade reserves, as the real TradeEvent does."""
    vsol, vtok = 30.0 * C.LAMPORTS, float(C.INITIAL_VTOK)
    tape = []
    for off, user, buy, amt in trades:
        if buy:
            tok, vsol, vtok = C.buy_fill(vsol, vtok, amt * (1 + FEE), FEE)
            sol = amt
        else:
            tok = amt
            k = vsol * vtok
            vtok2 = vtok + tok
            vsol2 = k / vtok2
            sol = vsol - vsol2
            vsol, vtok = vsol2, vtok2
        tape.append({"mint": mint, "user": user, "buy": 1 if buy else 0,
                     "sol": sol, "tok": tok, "vsol": vsol, "vtok": vtok,
                     "slot": slot0 + off, "bt": bt0 + int(off * 0.4),
                     "ts": bt0 + int(off * 0.4), "w": w, "fee": 60000})
    return tape


# ---------------------------------------------------------------- cohort build

tape = mk([(0, "DEV", True, 1e9), (0, "A", True, 5e8), (0, "B", True, 5e8),
           (3, "C", True, 5e8), (5, "A", False, 1e12)])
coh, fb = K.cohort_of(tape, include_birth=False)
chk("cohort excludes the birth wallet", "DEV" not in coh, sorted(coh))
chk("cohort = birth-slot first buyers", coh == {"A", "B"}, sorted(coh))
chk("later buyer is not in the cohort", "C" not in coh)
coh2, _ = K.cohort_of(tape, include_birth=True)
chk("include_birth adds the birth wallet", coh2 == {"DEV", "A", "B"})

# a wallet whose first appearance in the birth slot is a SELL cannot be cohort
t2 = mk([(0, "DEV", True, 1e9), (0, "S", False, 1e11), (0, "A", True, 5e8)])
coh3, _ = K.cohort_of(t2, include_birth=False)
chk("a birth-slot SELLER is not a cohort member", coh3 == {"A"}, sorted(coh3))

# ------------------------------------------------------------- entry / horizon

chk("entry index is the last trade at slot <= birth+L",
    K.entry_index(tape, 2) == 2, K.entry_index(tape, 2))
chk("entry index at L=0 still lands behind the birth slot",
    K.entry_index(tape, 0) == 2)
birth = tape[0]
ei = K.entry_index(tape, 2)
t_ent = K.O.ts_of(tape[ei], birth)
chk("index_at_elapsed never precedes entry",
    K.index_at_elapsed(tape, ei, t_ent, birth, 0.0) == ei)
chk("index_at_elapsed walks forward with the budget",
    K.index_at_elapsed(tape, ei, t_ent, birth, 300.0) == len(tape) - 1)

# ----------------------------------------------------------------- the trigger

# A bought 5e8 lamports' worth; selling 1e12 tok of it is a partial exit.
i, dt = K.trigger_index(tape, ei, t_ent, birth, {"A", "B"}, 0.0)
chk("theta=0+ fires on the first cohort sell", i == 4, (i, dt))
# the denominator is the COHORT's buys, not the selling wallet's alone
coh_frac = 1e12 / sum(r["tok"] for r in tape
                      if r["user"] in {"A", "B"} and r["buy"])
i2, _ = K.trigger_index(tape, ei, t_ent, birth, {"A", "B"}, coh_frac * 0.99)
chk("theta just under the realised cohort fraction fires", i2 == 4)
i3, _ = K.trigger_index(tape, ei, t_ent, birth, {"A", "B"}, coh_frac * 1.01)
chk("theta above the realised fraction never fires", i3 is None,
    "coh_frac=%.3f" % coh_frac)
i4, _ = K.trigger_index(tape, ei, t_ent, birth, {"B"}, 0.0)
chk("a cohort that never sells never fires", i4 is None)
i5, _ = K.trigger_index(tape, ei, t_ent, birth, set(), 0.0)
chk("an empty cohort never fires", i5 is None)
chk("theta=0 requires a strictly positive sell, not just a buy",
    K.trigger_index(tape, 0, K.O.ts_of(tape[0], birth), birth,
                    {"A", "B"}, 0.0)[0] == 4)

# the trigger must never fire at or before the entry index
t3 = mk([(0, "DEV", True, 1e9), (0, "A", True, 5e8), (1, "A", False, 1e12),
         (9, "A", False, 1e12)])
b3 = t3[0]
e3 = K.entry_index(t3, 2)
i6, _ = K.trigger_index(t3, e3, K.O.ts_of(t3[e3], b3), b3, {"A"}, 0.0)
chk("trigger is strictly after the entry index", i6 is not None and i6 > e3,
    (e3, i6))

# H_MAX truncation
t4 = mk([(0, "DEV", True, 1e9), (0, "A", True, 5e8), (3000, "A", False, 1e12)])
b4 = t4[0]
e4 = K.entry_index(t4, 2)
i7, _ = K.trigger_index(t4, e4, K.O.ts_of(t4[e4], b4), b4, {"A"}, 0.0)
chk("a cohort sell beyond H_MAX does not fire", i7 is None)

# ---------------------------------------------------------------- the P&L leg

vs, vt = tape[ei]["vsol"], tape[ei]["vtok"]
clip = 0.3
tok, vs2, vt2 = C.buy_fill(vs, vt, clip * C.LAMPORTS, FEE)
post = {"vsol": vs2, "vtok": vt2}
got = K.pnl(tape[ei], post, clip, FEE, 0.0)
want = (1 - FEE) / (1 + FEE) - 1
chk("flat round trip costs exactly the two fees (path independence)",
    abs(got - want) < 1e-9, "%.6f vs %.6f" % (got, want))

_, bvs, bvt = C.buy_fill(vs, vt, 3.0 * C.LAMPORTS, FEE)
big = K.pnl(tape[ei], {"vsol": bvs, "vtok": bvt}, 3.0, FEE, 0.0)
chk("the two-fee cost does not depend on clip size",
    abs(big - want) < 1e-9, "%.6f" % big)

# own-impact penalty: being second for the same token count costs ~2s/X
# Being second for the same token count: the ratio is EXACTLY 1 + 2x/X,
# where x is the CURVE-MOVING amount clip/(1+fee), not the gross clip.
tok1, _, _ = C.buy_fill(vs, vt, clip * C.LAMPORTS, FEE)
tok2, _, _ = C.buy_fill(vs2, vt2, clip * C.LAMPORTS, FEE)
ratio = tok1 / tok2
x = clip * C.LAMPORTS / (1 + FEE)
chk("second buyer pays exactly 1+2x/X for the same tokens",
    abs(ratio - (1 + 2 * x / vs)) < 1e-9,
    "ratio %.6f vs 1+2x/X %.6f" % (ratio, 1 + 2 * x / vs))
chk("the gross-clip form 1+2s/X overstates it by the fee",
    1 + 2 * (clip * C.LAMPORTS) / vs > ratio)

chk("txcost lands in the P&L as a fraction of clip",
    abs(K.pnl(tape[ei], post, clip, FEE, 1e6) - got
        + 1e6 / (clip * C.LAMPORTS)) < 1e-12)

# ------------------------------------------------------------------- pairing

recs = [{"a": 0.1, "fix": 0.1}, {"a": 0.2, "fix": 0.0}, {"a": -0.1, "fix": 0.1},
        {"a": 0.0, "fix": 0.0}]
chk("paired difference of an arm with itself is exactly zero",
    abs(K.paired(recs, "fix")["mean"]) < 1e-15)
chk("paired difference is the mean of the differences",
    abs(K.paired(recs, "a")["mean"] - (0.0 + 0.2 - 0.2 + 0.0) / 4) < 1e-15)

# --------------------------------------------------- nulls preserve their hold

virg = {("w", "M"): {"tape": tape, "end_slot": tape[0]["slot"] + 4500,
                     "end_ts": tape[0]["bt"] + 1800}}
nt = K.null_time_matched(virg, [("w", "M")], 0.0, FEE, 0.0, 0.3, False, draws=5)
chk("time-matched null on one mint is degenerate (its own time)",
    len(set(round(x, 12) for x in nt)) == 1, "%d draws" % len(nt))

t5 = mk([(0, "DEV", True, 1e9), (0, "A", True, 5e8), (0, "B", True, 5e8),
         (2, "Z", True, 5e8), (40, "A", False, 1e12), (60, "Z", False, 1e12)])
v5 = {("w", "N"): {"tape": t5, "end_slot": t5[0]["slot"] + 4500,
                   "end_ts": t5[0]["bt"] + 1800}}
nl = K.null_labels(v5, [("w", "N")], 0.0, FEE, 0.0, 0.3, False, draws=40)
chk("label-shuffle null actually varies the exit", len(set(
    round(x, 10) for x in nl)) > 1, "%d distinct" % len(set(round(x, 10)
                                                            for x in nl)))

print()
if FAILS:
    print("FAILED: %s" % ", ".join(FAILS))
    sys.exit(1)
print("all self-tests pass")
