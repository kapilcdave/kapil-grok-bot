# RESULT — wallet profitability IS persistent, and the persistent seat is the launch transaction

Preregistration: `preregistrations/pumpfun_wallet_persistence_20261001.md`
Code: `pumppersist.py`, self-tests `test_pumppersist.py`
Output: `data/pumppersist.json`

Read-only against the 16 archived windows in `data/pumptape/`. No key loaded, nothing signed, $0.

## Verdict: `PERSISTENCE-IS-THE-SLOT-SEAT`

All six preregistered gates pass — and the preregistered falsifier fires.

Selection on windows 0–7 only (top decile of the 2,749 wallets with ≥5 records, by mean A-ROI =
275 wallets), scored once on windows 8–15:

| | n (B records) | mean B-ROI | clustered 95% CI | median | win share |
|---|---:|---:|---:|---:|---:|
| **leaders (top decile, ≥5 A-records)** | 961 | **+10.74%** | [+8.64%, +15.16%] | +1.04% | 51.6% |
| top quintile | 2,115 | +5.61% | [+3.66%, +8.23%] | −1.59% | 46.8% |
| top decile, ≥3 A-records | 1,236 | +9.02% | [+7.28%, +12.01%] | +0.41% | 50.4% |
| top decile, ≥10 A-records | 570 | +1.95% | [−2.46%, +7.82%] | −1.92% | 44.9% |
| contemporaneous B population | 76,611 | **−7.78%** | — | — | — |

Contrast vs the B population **+18.52 pp**, CI **[+15.63, +23.33]**; vs eligible-but-unselected
wallets **[+13.91, +20.72]**. The full-procedure shuffle null (permute A-ROI labels, re-select,
re-score B, 500 draws) peaks at **+11.83 pp** with p95 **+8.09 pp** — the observed +18.52 clears it.
G6 clears the +32 bps copy-entry penalty by two orders of magnitude.

**So the premise behind every "top trader" leaderboard is correct and this corpus had it wrong by
omission.** Pump.fun profitability is not survivorship. The wallets that won in one set of windows
win again in windows sampled days later, out of sample, against a population losing −7.78%.

## Why it still authorizes no copy-trading

The preregistered decomposition of leaders' B profit by entry group:

| entry group | B P&L (SOL) |
|---|---:|
| **birth transaction** | **+540.97** |
| plus_13_50_slots | +7.98 |
| plus_5_12_slots | +1.60 |
| plus_2_4_slots | +1.08 |
| plus_1_slot | +0.75 |
| same_slot_later_transaction | +0.50 |
| plus_51_slots_or_later | −4.95 |

**97.9% of leaders' positive profit is in the slot seat, and 97.8% is in the launch transaction
itself** — the falsifier's 60% threshold is not close. A copier cannot be inside the leader's own
transaction, so the persistent quantity is **launch access**, not a signal. Everything a copier can
actually reach — `plus_1_slot` onward — totals **+6.46 SOL against +540.97**, and the latest bucket
is negative.

This is consistent with, and upgrades, the census in `FINDINGS_pumpfun.md` (106% of net profit in
the birth-slot bucket; the "sniper seat" 99.0% lat=0). That was a within-sample accounting. This is
**out-of-sample persistence of the same seat**: the lat=0 population is a repeatable operation, not
a lucky tail.

## What this closes and what it opens

**Closes, on the selection step rather than execution:** copy-trading a pump.fun leaderboard, by any
front-end, hold, or ranking metric. `RESULT-copytrade-20260919.md` showed the entry destroys a
leader worth copying; this shows the leader's edge is not in any entry a copier can occupy. Do not
reopen on a better leaderboard, a faster copier, or a filtered leader set.

**Opens nothing new.** It sharpens the one arm already open — the same-slot live shadow in
`RESULT-pumpfun-same-slot-20260929.md` — and relocates where its value would have to come from:
`same_slot_later_transaction` earns **+0.50 SOL** of leaders' profit here, so the leaders are not
making their money there either. The archived same-slot result (+5.73% mean ROI) and this one are
not in conflict — that cell is positive but thin — but the persistent operators sit one step earlier,
inside the launch transaction, which is a launch-access business and not a market-data one.
