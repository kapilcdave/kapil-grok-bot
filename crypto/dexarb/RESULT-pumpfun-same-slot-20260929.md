# RESULT — later transactions in the birth slot: OPEN FOR LIVE SHADOW

Preregistrations: `preregistrations/pumpfun_same_slot_order_20260929.md` and
`preregistrations/pumpfun_same_slot_exit_20260929.md`  
Code: `pumpsameslot.py`, `pumpsameentry.py`  
Outputs: `data/pumpsameslot.json`, `data/pumpsameentry.json`

## The correction

The earlier wallet census called every zero-slot-distance wallet “in the birth transaction,” but its
code recorded only slot distance. Transaction signatures split that population cleanly.

For fully exited, buy-first `(window, wallet, mint)` records:

| first entry | n | mean realised ROI | clustered 95% CI | trimmed mean | mean SOL |
|---|---:|---:|---:|---:|---:|
| exact birth transaction | 7,204 | +5.37% | [−4.09%, +12.79%] | +5.14% | +0.534 |
| **later transaction, same birth slot** | **10,388** | **+5.73%** | **[+2.87%, +8.95%]** | **+3.24%** | **+0.189** |
| +1 slot | 3,444 | −6.00% | [−7.77%, −4.55%] | −6.30% | +0.014 |

The same-slot-later group beats `+1 slot` by a window-clustered **8.25–15.42 percentage points**.
All five preregistered population gates pass.

## Concrete strategy

Entering with 1 SOL immediately after the final event in the birth transaction, then using the
already-fixed 15% trailing stop, 300-second maximum hold, and two-slot exit latency returns **+3.59%**
on average after the measured curve and transaction fees. The window-clustered interval is
**[+1.62%, +5.66%]**. The median is −7.98% and only 22.62% win, so the payoff remains lottery-shaped.

Post-gate size sensitivity, with every other rule frozen:

| clip | mean return | clustered 95% CI | median | win share |
|---:|---:|---:|---:|---:|
| 0.02 SOL | +10.14% | [+7.91%, +12.52%] | −2.57% | 26.96% |
| **0.05 SOL** | **+10.23%** | **[+8.01%, +12.60%]** | **−2.43%** | **27.11%** |
| 0.10 SOL | +9.99% | [+7.80%, +12.31%] | −2.64% | 26.82% |
| 0.50 SOL | +7.08% | [+4.99%, +9.30%] | −5.05% | 24.73% |
| 1.00 SOL | +3.59% | [+1.62%, +5.66%] | −7.98% | 22.62% |
| 2.00 SOL | −2.59% | [−4.41%, −0.72%] | −13.34% | 19.24% |

## Status

This opens one strategy and only one: react to a newly observed virgin curve and land in a later
transaction **inside the birth slot**. It does not reopen `+1 slot` sniping, copytrading, market-wide
filtering, or post-buy momentum.

The archive cannot prove public reachability. Later transactions may share private bundles or
off-chain launch information. The next gate is a live shadow collector measuring detection time,
submission deadline, transaction order, and actual same-slot landing probability. No capital is
authorized by this result alone.

