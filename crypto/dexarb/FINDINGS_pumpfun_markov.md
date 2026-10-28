# A Markov chain over pump.fun curve states: the model is RIGHT and the policy still loses. CLOSED. 2026-09-18.

Question asked: *"applying markov chains to memecoin trading bots on solana"*.

Answer: **the chain works, and that is what closes the branch.** The curve state process is
genuinely first-order Markov — a fitted kernel beats an independence model by **+2.19 nats per
transition on 249,674 held-out transitions**, and a *second*-order chain is **worse** out of
sample, so the state is sufficient and memory adds nothing. Perfect foresight over the stopping
epoch on the same discretised grid is worth **+33.29%**. And the **exactly optimal** stopping
policy over that verified-correct model, solved by backward induction and scored out of sample,
returns **−9.86%** [−11.73, −7.78] — **worse than the trivial rule of leaving immediately**
(−2.68%) by 7.2 pp, and worse than an increment-shuffled placebo that has no timing information
at all (−1.68%).

**Status: CLOSED as a model family.** Verdict rule committed in
`preregistrations/pumpfun_markov_stopping_20260918.md` §4 before the tape was read; the verdict it
returned is `G1 pass, G2 pass, G3 FAIL ⇒ CLOSED as a model family`.

Successor to `FINDINGS_pumpfun.md`, on the same 16 windows × 4,500 slots, seed 20260918, 928,714
trades, 9,290 virgin curves, **no new collection**. 3.5 minutes of compute. $0, read-only against
archived blocks, **no key loaded and nothing signed.**

---

## 0. Why this was worth running after the branch was already closed

`FINDINGS_pumpfun.md` closed the fresh-curve scalp, but it closed it by **sampling** a policy
family: 32 cells of a fixed menu (fixed hold, take-profit/stop-loss, trailing stop) for the exit,
and separately 8 features × 5 quintiles for the entry. Against that sat a perfect-foresight exit
bound of **+38.26%**. A 48 pp realisation gap with a sampled policy family leaves one live
objection: *you did not search the right rule.*

A Markov chain answers that objection in the only way it can be answered. Estimate the transition
kernel, solve the Snell envelope by backward induction, and you get the **supremum of the entire
family** of state-dependent stopping rules — not one more member of it. This is the exact dual of
`oracle-bound-a-selection-closes-a-model-family`: an oracle bound closes a *selection* family from
above, an optimal stopping solution closes a *rule* family from above. If the supremum loses, every
rule in the family loses, and the gap is proven to be **information you do not have** rather than
**policy space you did not search**.

## 1. Setup, all of it frozen before the tape was read

Entry at **L_in = 2 slots** after birth (Geyser-class, ~800 ms) at the state left by the last trade
at or before that slot — the identical entry model as the closed work. Clip **c\* = 0.0406 SOL**,
the measured minimiser of the cost floor, with a **1.0 SOL** capacity row. Fee **95 bps/side** read
from the event, tx cost 0.000110 SOL, own round-trip impact charged analytically as `2c/(S+2c)`.

**63 decision epochs** spaced **12 slots (4.8 s)** apart, spanning 297.6 s. Slots, not seconds,
because a slot is the unit an agent acts in and `blockTime` has only 1 s granularity.

**State (168 values)** = mark bin (7) × curve-progress bin (4) × buy-share-of-last-5 bin (3) ×
staleness bin (2), each built **only** from trades at slot ≤ slot(k). Kernel fitted separately for
**4 age groups**, because the drift is known to decay with age so a homogeneous chain is
misspecified by construction. 160 of 168 states visited in train, **241,862 transitions**.

An exit decided at epoch `k` fills at the state left by the **last trade at slot ≤ slot(k) + 2**.
One declared refinement over the closed work, which waited for the *next print*: a bonding curve
has **no counterparty**, the state persists between trades and can be sold into at any slot, so
waiting for a print is a modelling artifact that on a dying curve is an unbounded wait. The
refinement is adverse in the right direction — any trade landing in the 2-slot gap is ahead of us
and we take the state it leaves.

8 earlier windows train, 8 later windows test, chronological.

## 2. Gate 1 — the process IS first-order Markov. The chain is a correct description.

Out-of-sample mean log-likelihood per transition, three nested models fitted on train and scored on
the test windows (n = **249,674** transitions):

| model | OOS nats/transition |
|---|---|
| independence (next state from its marginal, by age group) | −2.7853 |
| **order 1** (the kernel) | **−0.5956** |
| order 2 (previous mark bin appended to the state) | −0.6331 |

**order 1 − independence = +2.1896 nats. order 2 − order 1 = −0.0374.**

The first number says the state carries real dynamics — this is not a chain fitted to noise. The
second says the state is **sufficient**: conditioning on where the curve was one epoch ago makes
prediction *worse* out of sample, which is the signature of a genuine first-order Markov process
rather than of an under-specified one. Reported because it is a claim about the venue that survives
whatever the P&L does.

## 3. Gate 2 — discretising does not destroy the headroom

Perfect foresight over the stopping **epoch**, still filling 2 slots late, on the test windows:

| | c\* = 0.0406 SOL | clip 1.0 SOL |
|---|---|---|
| **perfect-foresight epoch** | **+33.29%** [+28.07, +39.15] | +20.31% [+15.45, +25.84] |
| leave immediately (k = 0) | −2.68% | −13.26% |
| hold to the horizon | −9.28% | −19.63% |
| anti-oracle (worst epoch) | −27.27% | −36.44% |
| cost floor over all clips | 2.44% | — |

+33.29% against a 2.44% floor, versus Wall 3's +38.26% on the continuous tape. The 4.8 s grid costs
about 5 pp of a 38 pp bound, so **the discretisation is not what is wrong**, and a positive bound
means the branch survives to the decisive gate (`oracle-bound-screens-a-horizon`).

## 4. Gate 3 — the optimal policy loses, and loses to the trivial rule

| | c\* = 0.0406 SOL | clip 1.0 SOL |
|---|---|---|
| in-sample net | −5.80% | −16.82% |
| **TEST net** | **−9.86%** [−11.73, −7.78] | **−20.43%** [−22.29, −18.45] |
| P(win) | 0.134 | 0.107 |
| n | 4,027 | 3,555 |
| entry rate (V₀ > 0) | 100.0% | 88.3% |
| mean stop epoch | 48.5 (233.0 s) | 50.6 (242.9 s) |
| stops immediately / rides to horizon | 0.0% / 34.1% | 0.0% / 15.0% |
| same kernel, full-support smoothing | −10.01% | −20.43% |

**The optimal stopping policy is 7.2 pp worse than leaving immediately** and statistically
indistinguishable from holding to the horizon (−9.28%). It stops immediately on **0.0%** of
episodes. The verdict does not move between the two smoothing conventions, so it is not a
smoothing artifact. At the 1.0 SOL clip the entry gate finally binds and **rejecting 11.7% of
entries made it worse** (−20.43% gated vs −20.09% ungated) — the same anti-selection Wall 4 found,
where a screen cost 0.26 pp.

### What the policy learned, and why it is defensible mathematics and a disaster in realisation

Stop region by mark bin, weighted by visits (train):

| mark bin | visits | mean payoff | policy stops on |
|---|---|---|---|
| ≤ −30% | 76,748 | **−60.46%** | **2.8%** |
| −30…−15% | 16,905 | −20.27% | 57.0% |
| −15…−5% | 17,109 | −9.26% | 48.0% |
| −5…+5% | 108,904 | −2.56% | **2.9%** |
| +5…+15% | 5,103 | +9.57% | 13.2% |
| +15…+30% | 4,704 | +24.36% | 7.2% |
| > +30% | 16,290 | **+189.48%** | 29.2% |

The policy **holds the catastrophic and the flat, and cuts the middling** — an inverted rule. It is
not a bug. Holding a −60% position is *correct under the fitted kernel*, because some of those
curves transition into the >+30% bin whose mean payoff is **+189.48%**, so the estimated
continuation value genuinely exceeds −60%. That +189% is an extreme-tail mean estimated from 16,290
visits concentrated in a small number of curves. Backward induction takes it at face value 62 times
in a row.

Equally, holding at −5…+5% is correct under the kernel and is exactly where the −2.68% instant exit
lives: the policy declines a −2.68% certainty in favour of a lottery the kernel prices above it.

### The optimism gap: backward induction manufactures 26 pp on data it was fitted to

| | fitted V₀ | realised net | gap |
|---|---|---|---|
| **in sample** | +21.09% | −5.80% | **+25.98 pp** |
| out of sample | +21.09% | −9.86% | **+30.95 pp** |

This is the mechanism, and the in-sample column is the important one. `V_t(s) = max(payoff(s),
E[V_{t+1}])` applies a **max over estimated quantities at each of 62 epochs**, and a max over noisy
estimates is biased upward; the recursion compounds that bias. So `V₀ = +21.09%` is not an estimate
of anything the policy collects — it is 26 pp of estimation error, visible **without a test set at
all**. Any presentation of this family that quotes a value function instead of a scored policy
would have reported a +21% strategy.

## 5. Gate 4 — the placebo beats the real policy, which localises the defect

Informational rather than gating (G3 had already failed), 20 draws each, full refit of kernel,
payoff, policy and score:

| null | p50 | p95 | vs measured −9.86% |
|---|---|---|---|
| **increment shuffle** (permute each curve's epoch-to-epoch log increments; terminal return, volatility and age structure preserved **exactly**, only the ordering destroyed) | **−1.68%** | −0.33% | **placebo wins by 8.2 pp** |
| donor path (whole price path from another curve in the same window, `v`/`f`/`q` kept) | −7.80% | −6.71% | placebo wins by 2.1 pp |

A null with **no timing information whatsoever** outperforms the policy by 8.2 pp. That is not a
failure to extract information; the real ordering is *actively adverse*, and it says precisely
where the problem is:

| | net if you leave here | share of the final decline already taken |
|---|---|---|
| k = 0 (0.0 s) | −2.68% | — |
| k = 2 (9.6 s) | −4.97% | **53.5%** |
| k = 6 (28.8 s) | −7.35% | 79.2% |
| k = 12 (57.6 s) | −8.37% | **90.2%** |
| k = 62 (297.6 s) | −9.28% | 100% |

**Over half the entire 300 s decline is already realised 9.6 seconds in, and 90% of it inside the
first minute.** The mean log mark is monotonically decreasing from k = 0 onward, so **no refinement
of the epoch grid creates a positive stopping time** — there is no earlier exit than entry. The
shuffle helps because it spreads a front-loaded decline evenly, giving any stopping rule time to
react. On the real tape the state must *observe* a move before it can condition on it, and by then
the move has happened. The same shape as `a-bar-close-exit-is-half-a-bar-late`, one level up: here
it is not the bar that is late, it is the entire concept of reacting to state.

## 6. Robustness — the reserve-consistent subset, and it cuts the right way

On the 76.2% of curves (7,078 of 9,290) whose reserves move only by their own trades
(`per-observation-invariance-does-not-validate-the-state-machine`):

| | full sample | reserve-consistent |
|---|---|---|
| grid oracle | +33.29% | **+11.98%** |
| policy, test | −9.86% | −14.12% |
| entry rate | 100.0% | 33.3% |

Two-thirds of the oracle's apparent headroom was curves whose SOL reserve moves outside the trade
stream, exactly as the closed work found at 10 s (+7.97% → −0.37%). The oracle takes a **max** over
states, so it preferentially selects the states whose `vsol` jumped for reasons not in the tape.
The policy is worse on the clean subset and the entry gate rejects two-thirds of it. Reported as a
robustness row, never as a headline, because the filter is itself outcome-correlated.

## 7. Gate 5 — not evaluated

The $/day gate is conditional on G3 and was not reached. For the record, had a positive margin
existed, capacity was not the binding constraint: 21,000 mints/day × 0.0406 SOL at ~$150/SOL is
about $128k/day of turnover at a 100% entry rate, so capacity would not have bound a small positive
margin. The measured margin is −9.86% — the edge is not there.

## 8. What this closes, and the one thing it does not

**Closed.** Every state-dependent stopping rule on this state space, at once, rather than 32 of
them. Because G1 established that the state is *sufficient* (order 2 is worse OOS) and G2
established that the *headroom survives discretisation*, the failure at G3 cannot be attributed to
a misspecified chain, too coarse a grid, or an unlucky choice of rule. The 43 pp between a +33.29%
grid oracle and a −9.86% realised policy is unreachable information.

**Inherited as closed.** A richer state that separates "will recover to +30%" from "will not" is
precisely what Wall 4's eight pre-entry features failed to do — every bin of every feature lost,
and the out-of-sample screen made things 0.26 pp *worse* than no screen. The lottery structure that
makes the value function hold losers is the same structure no observable sorted.

**Honestly still untested.** The state here is entirely curve-local. A state carrying market-wide
variables — SOL price, aggregate mint rate, a cross-curve regime — was not tried. Given that the
gap to close is 43 pp and that the decline is 53.5% complete in 9.6 seconds, the prior on that
rescuing the branch is very low, and it is recorded as a residual rather than as a next step.

## 9. Reproducing

```
python3 pumpmarkovtest.py                            # 40+ closed-form checks, run FIRST
python3 pumpmarkov.py --json data/pumpmarkov.json    # the full study, ~3.5 min
```

`pumpmarkovtest.py` is not decoration. The state's mark bin **contains the P&L** — a stopping
problem's state must carry its own payoff — which means a forward leak inside the mark would be
invisible in the P&L and would show up only as a suspiciously good number. Check 5 recomputes the
state at epoch `k` from a tape truncated at `slot(k)` and requires it bit-identical, and requires
`pfill ≠ pobs` on some epochs so that a silently disabled `L_out` cannot pass. Check 6 solves a
two-state chain whose optimal policy and value are derivable by hand.

Saved output: `logs/full_markov.txt`, `data/pumpmarkov.json`.
