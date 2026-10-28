# RESULT — the zero-slot funder edge is a launcher's bundle, and it inverts at every reachable seat

Date: 2026-10-04

Preregistration:
`preregistrations/pumpfun_graduation_exit_20261004.md`

Code:

- `pumpfundergradexit.py`
- `pumpfundergradrescore.py`

Supersedes the capital-facing reading of
`RESULT-pumpfun-zero-slot-funder-reach-20261004.md`. That result's archive
gates were computed correctly; its seat and its exit were not attainable.

## Verdict

Archive verdict: **`BUNDLE-SEAT-UNREACHABLE`**

Capital verdict: **`NO-GO`**. The live-shadow gate is **withdrawn**, not left
open — but for the seat, not for the feed: an account-filtered transaction
stream would have fixed the funder-cache completeness problem and would still
not have bought a reachable seat.

The branch closes on **one** ground: the seat. A second criticism I raised —
that the exit was unsellable — was tested against the post-migration pool and
is **worth 1.73 percentage points of the 27.18**, not the branch. It is
recorded below as a modelling defect, not as a reason for the verdict.

## 1. All of the profit is in bundled launches

A birth-slot *cluster* is three or more distinct wallets buying in the birth
slot at one identical priority-fee value. At the `up_to_1_same_slot_ahead`
seat, among the 171 B-period top-funder rows:

| subset | n | share of top-group profit | mean |
|---|---:|---:|---:|
| cluster ≥ 2 wallets | 46 | **101.91%** | +102.97% |
| cluster ≥ 3 wallets | 33 | **100.40%** | +141.42% |
| cluster ≥ 4 wallets | 23 | 88.80% | +179.44% |
| no cluster ≥ 3 | 138 | −0.40% | **−0.14%** |

Drop the 33 bundled launches and the seat's mean is **−0.14%**, not +27.18%.

The effect is not a property of the selected funders. In the whole B
population at the same seat, cluster ≥ 3 launches mean **+36.66%** (n=256) and
the rest mean **−2.14%** (n=1,612). The funder selector's contribution is that
it over-selects bundling launchers: cluster ≥ 3 at 19.30% of top-funder rows
against a 13.70% population base rate, and cluster ≥ 4 at 13.45% against
4.28%.

## 2. No outsider has ever occupied the modelled seat

On each of the 33 bundled launches, the cluster's transactions form a
**contiguous, uninterrupted run** inside the birth slot:

- birth slots in which any non-cluster transaction landed between the
  cluster's first and last buy: **0 of 33**;
- cluster begins at birth-slot transaction index 1, immediately after the
  create: **27 of 33**.

The `up_to_1_same_slot_ahead` seat places our buy between the create and the
cluster's first buy. In this entire sample no outside transaction has ever
been in that position. This is not a race lost on latency; the block is
atomic and the seat belongs to its author. It is the same lesson as
`the-launch-transaction-is-authorship-not-latency`, extended from the create
to the whole paying birth slot.

On the one launch inspected transaction by transaction, five distinct wallets
each bought ~19.4–20.0 SOL at an identical 6,500-lamport priority fee in the
birth slot, consuming the curve from 30 to ~110 SOL, and the first wallet that
paid for priority (30,010,000 lamports) arrived *after* them at vsol ≈ 110.
That is what an outsider's seat on these launches actually looks like.

## 3. The exit is mis-modelled, and that costs 1.73 points, not the branch

An exit-path audit of the same scored set:

- 81 of 171 rows exit through the `horizon_end` fallback — the 15% trailing
  rule never fired;
- **77.8%** of those exit at the tape *terminus*, median **47 s** after entry,
  not at the 300 s horizon;
- 11 rows exit at exactly the bonding-curve completion ceiling,
  `vsol = 115.005 SOL`, all at the terminus, all within **12 s** of entry;
- those 11 rows are **6.4%** of the set and carry **68.65%** of its profit.

On all 11 the curve runs from the virgin state to 106-115 SOL *inside the
birth slot*. After completion the bonding curve is closed, so selling at that
state is not executable as written and the study's own 2-slot exit latency has
no curve state to land on. That much is a real defect.

**It is not worth what I first claimed.** The position does not vanish at
completion: the SPL mint is unchanged, so the tokens migrate into the PumpSwap
pool and can be sold there. `pumpfundergradexit.py` fetched the post-migration
pool for all 11 mints (22,282 RPC calls, 7.9% throttled) and re-scored each
under the same causal rule at the measured 25 bps sell-side pool fee:

| mint | archived (curve ceiling) | realized (pool) | outcome |
|---|---:|---:|---|
| 6ueU7yoNxG… | +437.6% | +340.2% | pool_exit_measured |
| PKGyhPFRkD… | +410.4% | +353.0% | pool_exit_measured |
| E6J7gD5km4… | +410.2% | +341.0% | pool_exit_measured |
| H9haopefn4… | +427.9% | **+592.5%** | pool_exit_measured |
| 45wggqbXA9… | +423.9% | +340.9% | pool_exit_measured |
| GuBHJG4GzD… | +173.2% | +176.7% | pool_exit_measured |
| 54WwadtQpR… | +173.2% | +236.4% | pool_exit_measured |
| 4sBivuSskP… | +164.4% | +438.2% | pool_absent_in_horizon (bound) |
| asKMSzdFqh… | −2.2% | — | fetch coverage loss |
| BPMgEry5uJ… | +166.9% | — | fetch coverage loss |
| FD9LXEbNmK… | +405.5% | — | fetch coverage loss |

Substituting every measured pool exit into the seat:

| treatment | n | mean | clustered 95% CI |
|---|---:|---:|---|
| as archived | 171 | +27.18% | [+16.30%, +36.34%] |
| **realized pool exits substituted** | 168 | **+25.45%** | [+13.68%, +35.27%] |
| pool-absent row scored as a total loss | 168 | +22.25% | [+9.00%, +32.33%] |

The exit correction is **1.73 points**. Six of eight covered mints retained
77-102% of the modelled value and two exceeded it. The preregistered coverage
gate (9 of 11) fails at 8 of 11, but that is a **paging-cap defect in my own
fetcher** — `--max-pages 40` at `limit=100` cannot reach back past ~4,000
signatures, and the three missing mints are the ones that needed more — not an
economic finding. It does not license treating those three as unsellable.

So the stronger form of this criticism is **refuted**: the graduated position
was approximately realizable, and the completion-ceiling exit was a wrong
*instrument*, not a fictional value. Dropping the 11 rows (which gives
+9.11%) is the wrong counterfactual; measuring the alternative instrument is
the right one, and it gives +25.45%.

A separate, minor defect stands: when `A.exit_after` finds no state,
`simulate_from_entry` falls back to the last row inside the horizon, which is
*earlier* than the 2-slot latency permits. It touches 7 of 171 rows and their
mean is −19.70%, so it flatters nothing. `pumpfunderreach.py` is deliberately
left unpatched so its archived numbers stay reproducible;
`pumpbundleseat.py:strict_outcome` is the corrected reference implementation.

## 4. At every reachable seat the signal inverts

The cluster is fully observable once the birth slot has closed, so entering at
`birth_slot_end` or later conditions on nothing unobservable and needs no
funder selection. Scored on the whole tape under a strict exit rule — a curve
that completes before the trail fires is held out rather than sold at the
completion state — the bundled launches are the **worst** cohort at every
seat, in both window halves, at every threshold:

| seat | cohort | A mean | B mean | pooled mean | pooled 95% CI | n pooled |
|---|---|---:|---:|---:|---|---:|
| birth_slot_end | cluster ≥ 3 | −10.29% | −10.64% | **−10.48%** | [−12.21%, −8.17%] | 1,074 |
| birth_slot_end | no cluster | −5.39% | −7.54% | −6.48% | — | 3,755 |
| plus_1_slot | cluster ≥ 3 | −11.21% | −11.75% | **−11.50%** | [−13.38%, −9.04%] | 1,065 |
| plus_1_slot | no cluster | −5.93% | −8.22% | −7.10% | — | 3,739 |
| plus_2_slots | cluster ≥ 3 | −11.52% | −12.77% | **−12.20%** | [−14.27%, −9.49%] | 1,047 |
| plus_2_slots | no cluster | −6.73% | −8.15% | −7.45% | — | 3,721 |

The sign is stable across the A/B split, so this is not a window artifact, and
n is an order of magnitude larger than the archived seat's. A bundled launch
is one where the supply sits in the launcher's wallets; the reachable seat is
the other side of their exit.

Coverage note: the strict exit rule holds out 2,997 of 9,290 launches as
`exit_state_absent` and 37 as `graduated_before_exit`. The holdout is applied
identically to both cohorts, so the contrast is unaffected, but the levels are
conditional on a curve that still trades.

## What survives

The funder-persistence measurement itself still stands: funder identity does
carry information across windows. What it carries is *which launchers run
multi-wallet bundles*, and that is a label on an unreachable seat rather than
a tradable edge. The pre-index engineering also stands on its own terms —
59-slot minimum funding lead, 20,000/20,000 exact historical recovery, 17.3 μs
p99 local lookup — and is reusable by any branch that needs a creator-to-funder
join before CreateEvent.

## Safety state

- capital authorized: **false**
- keypair loaded: **false**
- transaction built: **false**
- transaction signed: **false**
- transaction submitted: **false**
