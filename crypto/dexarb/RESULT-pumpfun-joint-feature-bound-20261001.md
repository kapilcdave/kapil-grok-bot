# RESULT — the oracle headroom is NOT in these features, and capacity is not the constraint

Preregistration: `preregistrations/pumpfun_joint_feature_bound_20261001.md`
Code: `pumpjoint.py` · Output: `data/pumpjoint.json`

All 15 pre-entry features (the 8 from the closed marginal screen + the 7 independence features),
7,913 mints, frame unchanged (clip 1 SOL, `L_in=2`, `L_out=2`, trail 15%, H=300 s, 95 bps).
Train windows 0–7 (3,892), test 8–15 (4,021). Read-only, $0.

## Verdict: `NO-JOINT-SELECTION`, and the family is bounded far below the prize

### The number that settles it

At the **same 10% selection rate**, on the **same test mints**:

| selector | test mean |
|---|---:|
| pooled, no selection | −11.88% |
| **a function of these 15 features, fit on the TEST LABELS THEMSELVES** | **−2.33%** |
| **the unrestricted oracle** (knows each outcome, picks any 10%) | **+42.10%** |

The middle row is cheating and still loses money. The 44 pp between it and the unrestricted oracle is
**information these features do not contain** — it was never a model-capacity problem.

**And capacity is demonstrably not the constraint.** Pushing the tree deeper on the test labels moves
nothing:

| depth | leaves | rate-matched mean (~10%) | best single leaf |
|---:|---:|---:|---:|
| 2 | 4 | −6.16% | −6.16% (n=939) |
| 3 | 8 | −6.25% | **+1.72%** (n=115, 2.9%) |
| 5 | 18 | −2.33% | +1.72% (n=115) |
| 8 | 30 | −2.29% | +1.72% (n=115) |

The best single cell these features can describe is **+1.72% on 2.9% of mints**, and it is identical
at depths 3, 5 and 8. More model does not find more; there is nothing more to find.

### The honest out-of-sample arm, and why "beats pooled" meant nothing again

Arm A — gradient boosting (300 trees, depth 3, lr 0.05) fit on train, top decile selected on test:

* in-sample on train: **+28.59%**
* out of sample on test: **−10.69%** (402 mints)

A 39 pp train-to-test collapse. It does beat the pooled −11.88% (G2 passes), and that is again worth
nothing: arm B — the identical pipeline refit on **permuted** train labels, 200 draws — has
**p95 −10.12%** and max −7.96%. Arm A sits *below* its own capacity-matched null's p95. G1 and G3 fail.

This is the second independent replication in two days that **beating the pooled mean is not evidence
when the procedure selects**. The pooled mean is the wrong benchmark; the procedure's own null is the
right one.

## What is closed

Combining these features — by weighting, stacking, trees, boosting, or any function of them — is
closed **at its supremum**, not model by model. The feature set is exhausted. Do not re-test it with
another learner, more trees, interactions, or a deeper grid.

## What is explicitly not tested here, and why

A diffusion model of the price path. That direction is already at its exact optimum:
the curve state process is **first-order Markov** (order-1 beats independence by **+2.1896
nats/transition**; **order-2 is worse at −0.0374**), so higher-order path dynamics carry no
information, and the **exactly optimal** stopping policy over that state — Snell envelope, backward
induction — scores **−9.86% OOS, 7.2 pp worse than exiting immediately**
(`FINDINGS_pumpfun_markov.md`). A continuous-diffusion approximation would be a coarser attack on a
problem already solved exactly.

## Consequence

The only remaining route to the +42.10% headroom is **information not in the on-chain pre-entry
state** — off-chain, which on this venue means social data. That is a data-access problem, not a
modelling one, and no amount of further work on this feature space changes it.
