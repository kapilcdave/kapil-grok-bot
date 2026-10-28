# RESULT — pre-indexing makes the funder feature fast; the entry seat is still exact

Date: 2026-10-04

Preregistration:
`preregistrations/pumpfun_zero_slot_funder_reach_20261004.md`

Code/data:

- `pumpfunderreach.py`
- `pumpfunderpreindex.py`
- `pumpfunderwatch.py`
- `data/pumpfunderreach.json`
- `data/pumpfunderpreindex_benchmark.json`
- `data/pumpfunder_research_scores.jsonl`

## Verdict

Archive verdict: **`ZERO-SLOT-FUNDER-SHADOW-CANDIDATE`**

Capital verdict: **`NO-GO`**

The fixed zero-slot funder selector remains strongly positive with one or two
later same-mint transactions ahead. It becomes negative by the end of the
birth slot. This is a narrow transaction-ordering strategy, not a generic
same-slot or one-slot-late strategy.

## Fixed-selector decay

The top 19 funders were selected once from windows 0–7 using the outcome
immediately after the birth transaction. The identical set was scored on
windows 8–15 at every later seat; funders were not reselected to fit each
latency.

| entry seat | B n | mean | clustered 95% CI | median |
|---|---:|---:|---|---:|
| after birth transaction | 171 | **+75.63%** | **[+45.47%, +109.13%]** | −2.42% |
| at most 1 later transaction ahead | 171 | **+27.18%** | **[+16.55%, +36.44%]** | −2.81% |
| at most 2 later transactions ahead | 171 | **+9.34%** | **[+3.24%, +14.27%]** | −3.06% |
| end of birth slot | 171 | **−4.72%** | [−7.80%, −0.99%] | — |
| end of +1 slot | 171 | **−5.85%** | [−8.53%, −2.28%] | — |
| end of +2 slots | 171 | **−5.79%** | [−9.23%, −1.81%] | — |

At one transaction ahead, top-minus-population is
**[+14.71, +33.19] percentage points** and top-minus-bottom is
**[+16.02, +40.05] points**. All preregistered archive gates pass.

## The pre-index is mechanically viable

For the 171 selected B launches:

- minimum funding lead: **59 slots**, about **23.6 seconds**;
- p05 lead: **196 slots**, about **78.4 seconds**;
- median lead: **4,374 slots**, about **29.2 minutes**;
- share funded at least five slots before creation: **100%**.

Therefore the creator-to-funder edge does not need to be discovered after
CreateEvent. Monitoring the 19 selected funders and caching their outgoing
signer transfers can make the launch-time operation a local lookup.

The SQLite replay recovered the exact historical funder and funding signature
on **20,000/20,000** selected-funder lookups. Local lookup latency was:

- p50 **13.2 μs**
- p95 **14.6 μs**
- p99 **17.3 μs**

This removes feature-lookup latency. It does not prove transaction landing.

## Important payoff-shape warning

This is not a reliable “take 20% and leave” scalp. At one transaction ahead:

- median return: **−2.81%**;
- win share: **32.75%**;
- only **22.22%** returned at least 20%;
- capping every winner at +20% changes mean return to **−2.54%**;
- capping winners at +50% leaves only **+2.93%** mean.

The positive expectation requires retaining the large runners under the
existing trailing exit. The five largest outcomes provide **47.66%** of total
profit. This is still not a single-funder artifact: every leave-one-funder-out
mean remains positive, ranging from **+19.21% to +38.97%** at one transaction
ahead.

## Live plumbing check

The public processed WebSocket worked:

- 5,000 records in 28.1 seconds;
- 21 CreateEvents;
- 20 birth candidates.

The local pre-index decision path completed in roughly tens of microseconds
and produced no transaction, signature, or submission.

The public HTTP RPC did **not** sustain the pre-index feed. A batched sample
encountered rate-limit/errors across all 19 funders and decoded only nine
transactions/five edges. None of the 20 births joined the incomplete cache.
This is a feed-completion failure, not evidence against the archived signal.

The live gate therefore remains open but unsatisfied: use an account-filtered
transaction stream or a configured RPC, record at least 100 eligible births,
and directly measure whether the shadow decision can still target transaction
position 0–2 after creation. Capital stays disabled.

## Follow-up independence audit

`RESULT-pumpfun-funder-independence-20261004.md` removes repeated known
creators and retains only each previously unseen creator's first B launch.
The fixed selector remains **+38.38%** with one later transaction ahead and
**+15.26%** with two ahead, with both window- and funder-clustered intervals
above zero. Verdict: **`FUNDER-ROTATION-INDEPENDENT`**.

That audit also found a 2.29% stale-selected-edge false-candidate rate if the
cache ignores later funding from other sources. The index now supports
candidate-recipient invalidation; live transport must capture those later
incoming transfers before the shadow is considered complete.

## Safety state

- research score rows: **19**
- research-only: **true**
- capital authorized: **false**
- keypair loaded: **false**
- transaction built: **false**
- transaction signed: **false**
- transaction submitted: **false**
