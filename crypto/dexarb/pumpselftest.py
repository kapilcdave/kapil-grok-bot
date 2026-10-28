"""Closed-form checks on the curve math and the oracle's entry/exit selection.

Everything here has a hand-derivable answer, so a wrong implementation shows up
as a mismatch rather than as a plausible P&L. Run before trusting pumporacle.py.

    python3 pumpselftest.py
"""
import sys

import pumpcurve as C
import pumporacle as O

FAIL = []


def check(name, got, want, tol=1e-9):
    ok = abs(got - want) <= tol * max(1.0, abs(want))
    print("  %-58s %s" % (name, "ok" if ok else "FAIL got %.10g want %.10g"
                          % (got, want)))
    if not ok:
        FAIL.append(name)


def main():
    L = C.LAMPORTS
    S0, T0 = int(30 * L), C.INITIAL_VTOK      # canonical virgin curve

    print("1. constant product is conserved by buy_fill / sell_fill (fee=0)")
    for c in (0.1, 1.0, 5.0):
        tok, s1, t1 = C.buy_fill(S0, T0, c * L, 0.0)
        check("k conserved, buy %.1f SOL" % c, s1 * t1, float(S0) * T0, 1e-12)
        out, s2, t2 = C.sell_fill(s1, t1, tok, 0.0)
        check("k conserved, sell back", s2 * t2, float(S0) * T0, 1e-12)
        check("zero-fee round trip at OWN post-state returns the clip",
              out / L, c, 1e-9)

    print("\n2. round trip against an UNCHANGED tape, fee=0.")
    print("   Buying c then selling into the curve as it was BEFORE our buy is")
    print("   the fixed-tape optimism. Analytic answer: proceeds = S*c/(S+2c),")
    print("   so impact alone costs 2c^2/(S+2c) even with perfect foresight")
    print("   and zero fees.")
    S = 30.0
    for c in (0.1, 0.5, 1.0, 5.0):
        got = C.round_trip(S0, T0, S0, T0, c, 0.0)
        want = S * c / (S + 2 * c) - c
        check("net for clip %.1f SOL" % c, got, want, 1e-7)
        print("      -> own-impact cost is %.2f%% of clip" % (-100 * want / c))

    print("\n3. fee is charged OUTSIDE the curve-moving amount.")
    print("   Measured: fee_basis_points=95 and the fee field is exactly 95.0")
    print("   bps of sol_amount, so paying B moves the curve by B/(1+r).")
    for r in (0.0095, 0.0125):
        tok, s1, t1 = C.buy_fill(S0, T0, 1.0 * L, r)
        check("curve moved by budget/(1+r), r=%.4f" % r,
              (s1 - S0) / L, 1.0 / (1.0 + r), 1e-12)
        out, s2, t2 = C.sell_fill(S0, T0, int(1e12), r)
        y = (S0 - s2) / L
        check("seller nets y(1-r), r=%.4f" % r, out / L, y * (1 - r), 1e-12)
    print("   round trip at the SAME state, r=0.0095, 1 SOL clip:")
    rt = C.round_trip(S0, T0, S0, T0, 1.0, 0.0095)
    print("      net %+.4f SOL  (impact 6.25%% + ~1.9%% fee)" % rt)
    if not (-0.10 < rt < -0.07):
        FAIL.append("round trip with fee should be about -8%")

    print("\n4. virgin detection is exact on the canonical initial state.")
    # Build rows the way the chain does: tok is the exact integer reserve
    # difference, so reconstruction of pre-trade reserves is lossless.
    def mkbuy(vsol, vtok, sol):
        tok_f, s1, t1 = C.buy_fill(vsol, vtok, sol, 0.0)
        t1 = int(t1)
        return {"buy": 1, "sol": int(sol), "tok": int(vtok) - t1,
                "vsol": int(s1), "vtok": t1}

    first = mkbuy(S0, T0, int(0.3 * L))
    print("  first trade on virgin curve -> is_virgin %s" % C.is_virgin(first))
    if not C.is_virgin(first):
        FAIL.append("is_virgin(first)")
    second = mkbuy(first["vsol"], first["vtok"], int(0.3 * L))
    print("  second trade                 -> is_virgin %s" % C.is_virgin(second))
    if C.is_virgin(second):
        FAIL.append("is_virgin(second) should be False")

    print("\n5. oracle picks the best exit, and CANNOT use the birth state at")
    print("   L>0, and is charged even when every exit is worse than entry.")
    # synthetic tape: birth cheap, then a spike at +2 slots, then a crash.
    def row(slot, bt, vsol, vtok, buy=1, sol=int(0.01 * L), tok=int(1e11)):
        return {"slot": slot, "bt": bt, "vsol": int(vsol), "vtok": int(vtok),
                "buy": buy, "sol": sol, "tok": tok, "user": "u%d" % slot,
                "fee": 5000, "mint": "M", "w": 0}

    k = float(S0) * T0
    def state(vsol):
        return vsol, k / vsol
    b = state(30.5 * L)          # birth: curve just above initial
    p = state(60.0 * L)          # spike, 2x
    d = state(31.0 * L)          # crash back

    tape = [row(100, 1000, *b), row(101, 1000, *state(31 * L)),
            row(102, 1001, *p), row(103, 1001, *d)]
    # make the birth row genuinely virgin so build_mints keeps it
    b0 = mkbuy(S0, T0, int(0.5 * L))
    tape[0] = row(100, 1000, b0["vsol"], b0["vtok"], sol=b0["sol"],
                  tok=b0["tok"])
    metas = [{"window": 0, "lo_slot": 100, "slots": 4500, "blocks_ok": 1,
              "skipped": 0, "failed": 0, "DONE": True}]
    virgin, seen, _ = O.build_mints(tape, metas, [C.INITIAL_VTOK])
    print("  build_mints kept %d of %d tapes" % (len(virgin), seen))
    if len(virgin) != 1:
        FAIL.append("build_mints should keep exactly the virgin tape")
        print("  cannot continue section 5")
    else:
        c0 = O.oracle_cell(virgin, 0.0, 0, 10, 1.0, 0)
        print("  L=0 H=10s: mean %+.4f SOL on 1 SOL clip (spike reachable)"
              % c0["mean_sol"])
        if c0["mean_sol"] <= 0:
            FAIL.append("L=0 should capture the 2x spike")
        # H=0 gives only the entry state -> pure cost, must be negative
        c1 = O.oracle_cell(virgin, 0.0, 0, 0, 1.0, 0)
        print("  L=0 H=0s : mean %+.4f SOL  (must be < 0: instant round trip)"
              % c1["mean_sol"])
        if c1["mean_sol"] >= 0:
            FAIL.append("H=0 must cost impact")
        # transaction cost is charged
        c2 = O.oracle_cell(virgin, 0.0, 0, 10, 1.0, int(0.05 * L))
        check("txcost is subtracted once per round trip",
              c0["mean_sol"] - c2["mean_sol"], 0.05, 1e-6)
        # coverage: a horizon longer than the window must drop the mint
        c3 = O.oracle_cell(virgin, 0.0, 0, 100000, 1.0, 0)
        print("  H=100000s: %s (expect no qualifying mints)"
              % ("dropped, n=0" if c3 is None else "n=%d" % c3["n"]))
        if c3 is not None:
            FAIL.append("coverage gate must drop under-covered mints")

    print("\n%s" % ("ALL CHECKS PASSED" if not FAIL
                    else "FAILURES: %s" % ", ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
