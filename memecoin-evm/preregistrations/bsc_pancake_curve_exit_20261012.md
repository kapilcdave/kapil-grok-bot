# Preregistration — BSC PancakeSwap-v2 launch seat, curve-priced exit

**Written 2026-10-12, BEFORE collecting `Sync` data or computing any result.**
Frozen config, frozen decision rule, one evaluation.

## Question

Rule B in `FINDINGS_offsolana_sniper_venue_vet_20261011.md` charged −100%
whenever *nobody else* sold, which assumes we could not have sold either. That
is probably wrong: we could have sold our own clip into whatever standing
liquidity the pool had. This test replaces that assumption with an executable
exit priced on the AMM curve, for every launch including the previously "stuck"
ones, and includes our own market impact on both legs.

## Universe (frozen)

- Chain BSC. Factory `0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73`
  (PancakeSwap v2), event `PairCreated`.
- Keep a pair iff **exactly one** leg is WBNB
  (`0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c`). Single numeraire; no USDT /
  BUSD / USDC pairs, so one fake-volume stablecoin token cannot dominate a mean
  (it did in the first pass: a 200,068,727 USDT row).
- Require at least one `Mint` (liquidity actually added) and at least one `Swap`
  within the forward window.
- Forward window 900 blocks (~11 min at 0.75 s) from `PairCreated`.
- Sample = every qualifying pair inside the public log-retention window
  (~2.4 h). Collection may be repeated to accumulate more launches; all
  qualifying launches collected are scored, with no post-hoc exclusions.

## Entry (frozen)

- The **open** = block of the first observed `Swap` on the pair.
- We are the first buyer in the open block: reserves used are those from the
  last `Sync` at or before `open_block - 1`.
- Clip `C ∈ {0.01, 0.05} BNB`, reported separately (clip is a stated axis, not a
  tuned parameter).
- Tokens received `T` from the standard v2 constant-product formula with the
  0.25% fee, so our own slippage and fee are charged:

  `T = 997·C·R_tok / (1000·R_bnb + 997·C)`

- A launch is skipped only if no `Sync` exists at or before the open (cannot
  price an entry at all). Skips are counted and reported.

## Exit (frozen)

- Horizon `N ∈ {1, 2, 5, 10, 30}` blocks after the entry block; every N is
  reported. No N is selected after the fact as "the" result.
- Reserves used are those from the last `Sync` at or before `entry_block + N`.
- We sell the entire holding `T` back on the curve, charging fee and our impact:

  `B_out = 997·T·R_bnb / (1000·R_tok + 997·T)`

- Realised return `= (B_out − C) / C`. If the pool has been drained,
  `B_out → 0` and the return approaches −100% **by measurement, not by
  assumption** — this is the point of the test.
- Reserves are NOT adjusted for the counterfactual that our own entry perturbs
  the subsequent observed path. Justification: `C` is small relative to launch
  pools. This is a stated approximation, and the `C = 0.05` arm is the
  sensitivity check — if results diverge sharply between clips, the
  approximation is doing work and the finding is withdrawn pending a full
  counterfactual replay.

## Metric and clustering (frozen)

- One observation per launch (pair). The launch is the clustering unit.
- Primary statistic: **mean realised return** across launches, with a 20,000-draw
  bootstrap 95% CI over launches.
- Median, win rate, p10/p90 reported as descriptive context only.

## Decision rule (frozen, one evaluation)

**PASS** iff, for at least one N:

1. the bootstrap 95% CI of the mean realised return is **strictly above 0**, AND
2. the result **replicates** on a disjoint window: split the launches
   chronologically into halves and require the mean to be positive in both
   halves, with the first half's CI also strictly above 0.

**FAIL** otherwise. A positive median with a CI spanning zero is a FAIL.
No re-scoring after seeing the result; no post-hoc N, clip, or filter selection.
Per [[judge-edges-not-dollar-targets]], capacity is reported, never a kill reason.

## Known limitations, stated in advance

- Public BSC RPCs retain logs ~2.4 h and refuse archive, so the sample is a
  single contiguous recent window and the time-of-day arm stays unrun.
- Gas cost is not charged (BSC gas is ~0.0001 BNB/tx, ~1–2% of a 0.01 BNB clip);
  if the result passes, gas must be charged before any arming.
- Being "first buyer in the open block" is optimistic by one queue position; the
  measured competition at the open was mean 1.24 txs, so this is a small but
  real favour to the strategy.
- A pass here is a seat-level finding only. It is not an arming decision and
  implies no live capital.

---

## AMENDMENT 2026-10-12T03:45Z — stopping rule for the powered re-evaluation

Written **before** looking at any result beyond the n=42 evaluation already
reported in `RESULT_bsc_pancake_curve_exit_20261012.md` (verdict: FAIL). At the
time of writing the tape stands at **n=68** and **has not been scored**.

The n=42 evaluation is spent. Accumulating data and re-scoring at will would be
multiple looks at one hypothesis, which inflates the false-positive rate and
would let a stop-when-it-looks-good decision manufacture a pass. So:

- **Exactly one further evaluation**, triggered at **n ≥ 500 launches**.
- No scoring between now and that trigger. `score_curve_exit.py` is frozen as
  committed; the universe, entry, exit, clips, horizons, metric and decision
  rule are unchanged from the original preregistration above.
- The decision rule gains its replication arm, as originally written: PASS
  requires, for at least one (clip, N), a bootstrap 95% CI strictly above 0 AND
  a chronological-split replication (first-half CI > 0, second-half mean > 0).
- Because 10 (clip, N) cells are reported, a single cell clearing zero at 95% is
  **not** sufficient on its own — the replication arm is what controls the
  multiplicity, and it is required, not optional.
- If the trigger is never reached (collection stalls, retention window lost), the
  standing verdict remains **FAIL at n=42** and nothing is claimed beyond it.

Accumulation: `run_accumulate.sh` under `caffeinate -ims`, interval 4200 s
(scan span 11200 blk = 1.40 h at the measured 0.450 s/block, so no coverage
gaps). Halt with `touch memecoin-evm/STOP`. Cycle 1 added +26 launches with 0
failures; 2000 blocks were lost to RPC throttling and the collector now backs off
20 s/attempt on 403/429.

## AMENDMENT 2 — 2026-10-12T08:50Z — the n≥500 trigger is underpowered; raise it to n≥1500

Written **before any scoring of the accumulating tape**, which stands at **n=112
and remains unscored**. Nothing below uses the new data: the power simulation
draws only on the *spent, already published* n=42 evaluation
(`data/curve_exit_scored.json`). The universe, entry, exit, clips, horizons,
metric and decision rule are **unchanged**. Only the sample-size trigger moves,
and it moves up.

`power_curve_exit.py` simulates the frozen rule exactly as it will be run — draw
n launches, take a percentile bootstrap 95% CI of the mean, ask whether it clears
zero — holding the observed shape and spread (`sd = 18.31%`, 86% win rate, a
−99.5% tail at 1/42) and sweeping the assumed true mean, because **the n=42 point
estimate is itself the noisy quantity and sizing to it is how an underpowered
test gets re-run at a sample that passes only if the estimate was lucky.**

Power of condition (1) alone:

| n | true +2.00% | true +3.00% | true +4.84% |
|---|---|---|---|
| 500 | 64% | 94% | 100% |
| 750 | 81% | 99% | 100% |
| 1500 | 97% | 100% | 100% |

But **condition (2) binds, and it halves the operative sample.** PASS requires the
first chronological half's CI to clear zero on its own, so the test that actually
gates the verdict runs at n/2:

| total n | first half | true +2.00% | true +3.00% | true +4.84% |
|---|---|---|---|---|
| 500 | 250 | 42% | **69%** | 96% |
| 750 | 375 | 56% | 82% | 100% |
| 1500 | 750 | **81%** | 99% | 100% |

At the n≥500 trigger a genuine +3% edge fails this test roughly **three times in
ten**, and a genuine +2% edge fails it more often than it passes. Since the
amendment grants **exactly one** evaluation, a fail at n=500 would close a live
branch on a coin flip. That is the same error this corpus has already paid for
elsewhere — a bar has to be powered before it is a bar.

**Trigger raised to n ≥ 1500 launches.** 81% power on the binding replication arm
against a +2% truth, ~100% against +3%. Still exactly one evaluation, still no
scoring before the trigger. If collection stalls before n=1500, the standing
verdict remains **FAIL at n=42** and nothing is claimed beyond it.

⚠ **The sd is the fragile input, and it errs toward needing more n.** `sd = 18.31%`
is dominated by a single −99.5% observation, so the tail frequency is 1/42 = 2.4%
with a 95% interval of roughly 0.1%–12.6%. If the true total-loss rate sits above
2.4%, the spread is larger than simulated and every n above is an underestimate.
This is a floor on the requirement, not an estimate of it.

**Collector fixed, same date.** The accumulator had hung for 3 h 42 m and
collected nothing: on a 403 `rpc()` slept `20*(i+1)` over 10 tries = 900 s per
failed call, and the factory scan is 3 chunks × (1 + 4 sub-chunks) = 13,500 s =
3.75 h. The backoff was also self-defeating — public BSC retention is ~1.2–2.4 h,
so sleeping 15 minutes before retrying a block range is what made those ranges
"unrecoverable". The scarce resource is the retention window, not the request.
Now: 4 tries, backoff capped at 8 s (≤15 s per call), and a hard `DEADLINE_S`
(default 1200 s) that abandons a cycle rather than overrunning the 4200 s
interval. First patched cycle added **+44** launches against +26 before.
