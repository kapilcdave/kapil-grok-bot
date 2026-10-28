# RESULT — the birth cohort's own selling is NOT an exit signal. pump.fun scalping is closed.

Prereg: [`preregistrations/pumpfun_cohort_exit_20261003.md`](preregistrations/pumpfun_cohort_exit_20261003.md),
frozen before any P&L for this branch existed.
Code: `pumpcohort.py`, `pumpcohort_diag.py`, self-tests `test_pumpcohort.py` (26 assertions, all pass).
Output: `data/pumpcohort.json`, `data/pumpcohort_diag.json`.

Read-only against `data/pumptape/` — 928,714 events, 16 windows, 9,290 virgin curves, **7,928** with
300 s of forward tape after entry. **No key was loaded, nothing was signed, $0 spent.**

## Verdict: `COHORT-EXIT-CARRIES-NOTHING` → the branch is closed, not deferred

Prereg §0 established that an exit trigger on an exogenous, non-price feature was the **last untested
degree of freedom** in this branch: the entry seat is authorship, mint selection is oracle-bounded,
and every price-path exit was already 0 of 32 positive on test. That trigger has now been tested on
the sharpest cohort available and it fails three of four relative gates. There is no next gate.

## §1 Lever A — the clip arithmetic paid, exactly as predicted, and changed nothing

Prereg §1 wrote this down as a *prediction* (+4 to +5 pp, still negative) so it could not later be
read as the finding. Fixed hold 10 s, identical mint set:

| clip | mean | median |
|---|---:|---:|
| 1.00 SOL | **−9.21%** | −7.97% |
| 0.30 SOL | **−5.40%** | −3.81% |
| 0.10 SOL | −4.29% | −2.58% |

**+3.81 pp recovered by arithmetic alone**, and −9.21% independently reproduces Wall 3's
−9.26% [−10.05%, −8.47%] through a separate code path — which is the harness validation.

Recovery is sublinear because the floor is `1.88% (two 95 bps legs) + 2x/X own impact`: ~8.1% at
1.0 SOL, ~2.5% at 0.1 SOL. So sizing down cannot reach zero, and at 0.1 SOL only ~1.8 pp of the
loss is anything other than cost.

## §2 Lever B — the cohort trigger fails the frozen gates

Paired against the clip-matched fixed hold on the identical mint set, so Lever A cancels exactly.
Clip 0.30 SOL. θ selected on w0–7, scored once on w8–15.

| arm | train paired | **test paired** | mint CI | window CI | label-null p95 |
|---|---:|---:|---|---|---:|
| `excl` (Alg 1 faithful), θ=0+ | **−4.16%** | +1.76% | [−2.55%, +7.19%] | [−6.18%, +8.94%] | **+2.74%** |
| `incl` (+ birth wallet), θ=0+ | **−0.98%** | +0.99% | [−0.84%, +2.81%] | [−3.60%, +3.81%] | **+2.80%** |

Against the four frozen relative gates:

| gate | `excl` | `incl` |
|---|---|---|
| 1. test paired > 0, CI excludes 0 | **FAIL** (both CIs cover 0) | **FAIL** (both CIs cover 0) |
| 2. above time-matched null p95 | pass (−0.01%) | fail/marginal (+1.26%) |
| 3. above label-shuffle null p95 | **FAIL** (+1.76% < +2.74%) | **FAIL** (+0.99% < +2.80%) |
| 4. sign holds on reserve-consistent | fail (+0.68% [−0.31%, +2.06%]) | pass (+1.98% [+0.95%, +3.43%]) |
| ABSOLUTE: test mean > 0 | **FAIL** (−4.52%) | **FAIL** (−5.29%) |

**The sign flips between train and test in both arms** — the best θ of four is *negative* on the
selection half and positive on the scoring half. That is the signature of noise, not of a rule.

### The label-shuffle null is the kill shot

Permuting cohort membership among wallets whose first buy lands in the first 10 slots — same cohort
size, same trigger shape, recomputed end to end — yields a mean improvement of **p50 +1.93% to
+2.54%, p95 +2.74% to +2.80%**. A *randomly chosen* early wallet is **better** at this job than the
real birth cohort. The trigger *shape* ("exit when some early wallet first sells") is worth a little
against a 10 s fixed hold; the cohort **identity** is worth less than nothing.

The time-matched null separates the two cleanly: permuting the exit *times* does worse (p50 −1.95%,
−0.85%), so *when* you leave matters, while permuting the *labels* does better, so *who* sold does
not. The combination is only consistent with a holding-period effect.

## §3 The one number that explains it (disclosed post-hoc, safe because negative)

Among every reachable exit inside the horizon, where does the cohort's first sell actually rank?

| arm | n fired | **median percentile rank** | p25 | p75 | median time to first cohort sell |
|---|---:|---:|---:|---:|---:|
| `excl` | 2,433 | **50.0%** | 26.7% | 75.0% | **2.0 s** |
| `incl` | 5,041 | **42.9%** | 11.2% | 71.4% | **6.0 s** |

**50% is exactly no information.** The birth wallet's own first sell ranks at 42.9% — *below*
median, a slightly worse-than-random moment to leave.

And prereg §6 had the mechanism backwards. I predicted the cohort would distribute *slowly*, exiting
late relative to the peak. It distributes **immediately**: median 2 s after entry for the Alg 1
cohort. The informed cohort's first sell is not a later event that could inform an exit — it is
**the same event as the entry**, arriving while the round-trip cost is still unpaid. A signal
coincident with your entry cannot time your exit.

### Why the fires-only subset looks like a win, and is not

| arm | n (test) | arm | fix | paired |
|---|---:|---:|---:|---:|
| `excl`, fires only | 1,394 | −7.06% | −10.81% | +3.75% [+1.94%, +6.15%] |
| `incl`, fires only | 2,670 | −3.89% | −7.22% | +3.33% [+0.68%, +6.01%] |

Both CIs exclude zero, and both are an artifact. The subset where an early wallet sells within
seconds **is** the subset whose price decays fastest: the fixed hold goes from −5.40% on the
population to −10.81% here. Any earlier exit wins on that subset, which is precisely why the
label-shuffle null (+2.74%/+2.80% p95) was frozen in advance — it brackets these numbers. The
attributable statistic is the rank, and the rank is 50%.

## §4 The oracle gap is untouched

On the fires-only set the best reachable exit is **mean +64.86%, median +6.91%** — a one-sided
lottery, the mean carried by a thin tail. The cohort rule captures a **negative** share of it.
The +38%/−10% spread from Wall 3 survives this test completely intact and is still not for sale.

## §5 What this closes, and what it does not

**Closed permanently:** pump.fun fresh-curve scalping. Entry is authorship
(`the-launch-transaction-is-authorship-not-latency`), selection is oracle-bounded, price-path exits
were 0 of 32, and the exogenous-flow exit is now null with the label permutation beating the real
cohort. Prereg §0 named this the last degree of freedom; it is spent.

**Not closed by this test** (and not rescues — separate claims needing their own pregeristration):
the off-chain comment/reply graph, which was deferred in advance as the expensive arm and whose prior
is `buyer-breadth-is-crowding-not-quality`.

**Kept from the run, independent of the verdict:**

1. `2x/X` is **exact**, not an approximation, and `x` is the curve-moving amount `clip/(1+fee)`, not
   the gross clip — the gross-clip form overstates the penalty by the fee. Asserted in
   `test_pumpcohort.py`.
2. A flat round trip on a constant-product curve is **path-independent**: it costs exactly the two
   fee legs (1.88%) and zero slippage, at any clip size. That is why a bump costs the priority fee
   and essentially nothing else, and why bump activity is cheap talk.
3. Wall 3's −9.26% reproduces at −9.21% through independent code.
