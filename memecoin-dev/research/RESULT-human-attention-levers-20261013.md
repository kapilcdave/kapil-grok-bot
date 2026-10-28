# RESULT: what brings HUMAN buyers to a fresh pump.fun coin (2026-10-13)

Prereg: `PREREG-human-attention-levers-20261013.md` (+ Amendment A). Code: `levers.py` (prereg),
`explore2.py`, `explore3.py`, `timing.py`, `top.py`, `sybil_check.py` (exploratory). Data: the
Iowa box's `snipefeed-2` tapes for 2026-10-10 and 2026-10-11, ~33k standard-curve launches a day.

## 1. A low-activity wallet is not a human. v1 was VOID.
The first definition (not the creator, >= 2 s after launch, <= 10 coins that day) put the top coins
at 800-1,100 "humans" each. Those wallets return to buy anything on the adjacent day at **0.6-2.2%**,
against 44-64% for mid-table coins and a 19.8% base rate. They are **holder farms**: an operator
spraying ~1,000 single-use wallets over the first minutes to fake a holder count. v2 adds "also
buys on the other day". Lesson for any pump.fun attention metric: **require cross-day return**.

## 2. Humans are a small slice
With returning wallets as humans: **79% of launches get zero human buyers**, 7.3% get >= 5, ~3% get
>= 20. Humans are ~12% of buyers and **~6% of volume**. A dev's creator fee is mostly bot volume.

## 3. Levers (v2, prereg rule: same sign and |t| >= 2.5 on both days), x on (1 + humans)
| lever | Oct 11 | Oct 10 | within-creator FE (Oct 11 / Oct 10) |
|---|---|---|---|
| links an X **post** | x1.17 | x1.14 | **x1.16 (t 4.4) / x1.11 (t 3.1): survives** |
| website | x1.16 | x1.21 | x1.03 (t 0.7) / x1.11 (t 3.0): mixed |
| dev buy > 1 SOL | x1.61 | x1.61 | x1.21 (t 1.8) / x1.33 (t 2.8): weak |
| dev buy (0, 1] SOL | x1.09 | x1.08 | x1.10-1.24, t 1.0-2.2: weak |
| third-party launch tool | x1.14 | x1.22 | (host invisible to traders: pros vs amateurs) |
| creator's earlier launches 5+ | x0.82 | x0.76 | |
| ALL-CAPS name | x0.84 | x0.89 | x0.98 / x0.99: **selection, not cause** |
| description | x0.93 | x0.90 | x0.99 / x1.00: **selection, not cause** |
| **copies a hot coin** | x0.95 | x0.99 | **not real** |
| X profile link, telegram, US hours, ticker = name, non-ASCII | | | not real |

Only the X post survives the creator fixed effect on both days. Everything else is mostly WHO the
creator is: one-off creators' coins draw 2-3x the humans of repeat creators', and a repeat creator's
very FIRST coin is already low (5.5 vs 8.8-14 humans, v1 numbers), so a fresh wallet by itself buys
little (first-vs-next launch within creator: +0.09 CI above 0 on Oct 11, +0.03 CI spanning 0 on Oct 10).

Copying a hot coin is not associated with more humans, and goes with ~11% LESS total volume
(log1p V: -0.105 t -3.6 / -0.117 t -3.2, the fee base, not affected by Amendment A).

## 4. Timing: the first 30 s is bots; humans come at minutes 2-10
| first buy after launch | p25 | median | within 30 s |
|---|---|---|---|
| returning humans | 36 s | **138 s** | 22% |
| bots (> 10 coins/day) | 5 s | 29 s | 51% |
The first 30 s carry 39% of first-hour volume, of which humans are 2.8 points. A coin has to still
look alive 2-10 minutes in, after the snipers have dumped (devbot's $gctrl: every bot out by 32 s).

## What this means for devbot
- Keep linking our own X post: it is the one lever that survives within creator. Our account has ~0
  followers, so the effect for us is likely smaller than the average creator's; reach is the gap.
- Metadata tweaks (casing, description, fresh wallet) are selection effects: don't expect them to move humans.
- The hot-copy mode does not attract humans and costs some volume.
- The large, unexplained gap is between KINDS of creators: distribution and story, not settings.
