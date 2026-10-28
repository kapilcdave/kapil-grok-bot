# RESULT — BSC PancakeSwap-v2 launch seat, curve-priced exit

Preregistration: `preregistrations/bsc_pancake_curve_exit_20261012.md` (frozen
before collection). One evaluation. **Verdict: FAIL.**

## Verdict

No `(clip, N)` cell has a bootstrap 95% CI strictly above zero, so condition (1)
of the decision rule is unmet and the replication arm was never reached.

```text
n = 42 launches (WBNB leg only), 0 skipped for unpriceable entry, 0 collection failures

 clip   N    n       mean              95% CI       median   win
 0.01   1   42     +1.70%  [ -4.36%,  +5.86%]      +2.17%   81%
 0.01   2   42     +1.70%  [ -4.25%,  +5.88%]      +2.17%   81%
 0.01   5   42     +1.81%  [ -4.33%,  +5.98%]      +2.17%   81%
 0.01  10   42     +2.11%  [ -3.88%,  +6.26%]      +2.33%   86%
 0.01  30   42     +4.84%  [ -1.36%,  +9.25%]      +4.63%   86%   <- closest to passing
 0.05   1   42     +0.59%  [ -5.20%,  +4.38%]      +1.35%   69%
 0.05  30   42     +3.68%  [ -2.54%,  +7.81%]      +3.99%   83%
```

Net of measured gas (below) the picture is unchanged: best cell clip=0.01, N=30,
mean **+4.70%**, CI **[−1.50%, +9.11%]**. Still spans zero. **FAIL.**

## Two things the test overturned — both of them mine

**1. Rule B's −100% assumption was wrong, and it was doing all the work.**
The earlier finding scored a launch at −100% whenever nobody else sold, which
assumed we could not have sold either. Priced on the curve against actual
reserves:

```text
returns below -90%:  1/42   (rule B implied ~35% were total losses)
returns below -50%:  1/42
returns above 0   : 34/42
```

Only **one** launch in 42 is a total loss. The pool is *not* drained within 30
blocks of the open — the drain to ~0.000077 BNB documented in the addendum
happens over **hours**, long after a seat would have exited. So "the exit is the
wall" was too strong: the exit is available, and the earlier mean of −22.76% was
an artifact of the −100% convention, not a measurement.

**2. Gas is irrelevant, not the 1–2% I flagged in the preregistration.**
Measured, not assumed: BSC gas price **0.0500 gwei**; buy 122,024 + approve
46,506 + sell 108,799 = 277,329 gas → **0.0000139 BNB** round trip = **0.14%** of
a 0.01 BNB clip, 0.03% of a 0.05 clip. (Floor: launch tokens are
fee-on-transfer and burn more gas than the USDT control used here.)

## Why this is still a FAIL and not a thin edge

- **Underpowered.** n=42 with a CI half-width of ~±6% against a point estimate of
  +1.7–4.8%. The sample cannot resolve the effect.
- **The shape is a warning.** The mean rises monotonically with horizon
  (+1.57% → +4.70% from N=1 to N=30). A genuine *seat* edge should be
  concentrated at the open and decay with horizon. An edge that grows with
  holding time is post-launch drift, which is the launch-selection problem
  already closed forward at −5 to −9% — not something the seat earns.
- **Pennies-and-tails shape.** 76–86% win rate with a +2% median and a −100%
  tail is the classic configuration that needs large n to establish and is
  dominated by tail frequency. One more total loss in 42 moves the mean by
  ~2.4 points, i.e. roughly the entire point estimate.
- **The entry is favoured.** We are scored as first buyer in the open block;
  measured competition there was mean 1.24 txs, so this is a real if small gift.

## What would settle it

Power, which means more launches. Public BSC RPCs retain logs ~2.4 h and refuse
archive, so one collection run yields ~42 launches. `collect_sync.py` is
**resumable** (it skips pairs already in `data/sync_tape.jsonl`), so repeated
runs every ~2 h accumulate a disjoint sample without paying for archive:
~500 launches/day. At n≈500 a +3% mean would have a CI half-width near ±1.7%
and the question resolves either way. The chronological-split replication arm in
the preregistration then becomes testable as written.

Until then this is a **FAIL with a small positive point estimate**, which is a
finding, not a seat.

## Reproduce

```text
preregistrations/bsc_pancake_curve_exit_20261012.md   frozen config + decision rule
collect_sync.py      -> data/sync_tape.jsonl          resumable; Swap+Mint+Burn+Sync
score_curve_exit.py  -> data/curve_exit_scored.json   frozen scorer, one evaluation
sellsim.py                                            gas measurement + safety gate
```

## Safety

Read-only `eth_getLogs` / `eth_call` / `eth_simulateV1`. No key loaded, no
transaction signed or broadcast. No capital at risk.
