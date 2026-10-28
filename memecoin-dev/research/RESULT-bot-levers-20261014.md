# RESULT: what brings BOTS to a fresh pump.fun coin, and what the dev nets (2026-10-14)

EXPLORATORY (no prereg). Same tapes as the human-levers study (10-10, 10-11, ~33k standard-curve
launches/day) and the same returning-wallet human definition. Code: `bots.py` (levers + dev NET),
`blocksplit.py`, `snipers.py`, `policy.py`, `policy_fe.py`; outputs `out*.txt` next to them.
Replication rule from the human prereg: same sign and |t| >= 2.5 on both days, within creator.

**Dev NET** (SOL, first hour) = 0.30% creator fee on all volume - dev buys x 1.0125 + dev sells x 0.9875
+ leftover dev tokens sold into the final curve x 0.9875 - 0.0056 launch cost.

## 1. Bots are the fee base
Bots are 87-88% of non-creator volume (counting buys and sells). The median launch's creator fee is 0.0074 SOL, but with no dev buy
it is **0.0005 SOL** against a 0.0056 cost.

## 2. Bot-volume levers, within creator (x on bot SOL, Oct 10 / Oct 11)
| lever | x | t | verdict |
|---|---|---|---|
| creator's first launch today | x1.22 / x1.36 | 7.9 / 12.4 | **replicates** |
| dev buy 1-2 SOL | x3.34 / x2.40 | 3.3 / 3.3 | **replicates** |
| dev buy > 2 SOL | x4.33 / x3.37 | 4.1 / 3.7 | **replicates** |
| X post, website, ticker = name, ALL-CAPS, US hours, metadata host | | | no |

## 3. The big dev-buy profits are BUNDLE cells
With launch-block buyers classified by how many distinct creators' launch blocks they appear in
(1 = that creator's bundle, >= 50 = broad sniper; 71-85 broad wallets carry 22-31% of block buys):
- no buyer in the launch block, dev buy > 2 SOL: median NET **-0.055 SOL**, P(NET>0) 37-38%.
  The within-creator dev-buy effect on NET is flat to negative.
- bundle-only / bundle+sniper blocks: median NET +1.1 to +2.0 SOL at > 2 SOL. That's undisclosed
  insider supply sold into followers. Not designed for (see the creator-fee memory).
- broad snipers only, dev buy 1-2 SOL: median **+0.096 / +0.083 SOL**, P(NET>0) 75% / 70%.

## 4. The policy a non-bundling creator can run (block empty or broad snipers only)
Across creators, a first launch today with a 0.5-2 SOL dev buy averages +0.12 to +0.23 SOL (top 1% capped, CI > 0
both days). Later launches the same day average ~0 or less. **Within creator** this mostly shrinks:

| term (within creator) | NET SOL Oct 10 / Oct 11 | P(broad sniper) |
|---|---|---|
| first launch today | **+0.024 (t 2.8) / +0.033 (t 4.2): replicates** | **+2.4pp (t 3.7) / +2.9pp (t 5.0): replicates** |
| first x dev buy 0.5-2 SOL | +0.067 (t 1.1) / +0.080 (t 1.9): same sign, unproven (~195 creators) | +14.5pp (t 4.6) / +5.8pp (t 1.8) |
| dev buy 0.5-2 SOL on a later launch | -0.003 / +0.022: nothing | nothing |

Snipers act in the launch block on pre-launch information only (creator history, dev buy), so
outcome-driven stopping rules can't produce the sniper-arrival term. That is the mechanism: **bots
read the dev wallet's history.**

## What this means for devbot
- A creator wallet that hasn't launched recently is the one replicated lever: +0.024-0.033 SOL net
  per launch within creator, 4-6x the launch cost, and it matches devbot's own $gctrl/$QVEIN pair.
  Wired 10-14 as `ROTATE_WALLETS=1` (devbot/wallets.py), with `AUTOSELL_PCT` selling part of the dev buy
  into the first outside SOL on the curve.
- A dev buy (`DEV_BUY_SOL`, default 0) multiplies bot volume, but it only pays when snipers
  arrive, and it loses when they don't (median -0.01 to -0.03 SOL at 0.5-2 SOL). On a fresh wallet the
  combination points positive, but it's not established on two days.
- Metadata, socials and timing do nothing for bots.
- Caveats: two days, one venue, a first-hour window; "first today" is weaker than "first ever"
  (wallet age is not in the tape); the means are tail-driven (the medians are near zero or negative).

## Amendment (10-14, later): Mayhem coins were in the sample, and Mayhem itself is scored
The create-time reserves check lets Mayhem coins through (their curves start standard), so the agent
traded **27-34%** of the "standard" sample. `bots.py` now excludes the agent (BwWK17...) from the
fee base (it pays 0 creator fee), from "bots" and from launch-block buyers, and charges Mayhem coins
+0.0027 SOL. The agent is **never** in a launch block (0.0% both days), so the broad-sniper
classification was not contaminated. Corrected, within creator (`policy_fe.py`, `outpolicy_fe_v2.txt`,
`outpolicy_fe_standard.txt`):

| first launch today | all coins NET | non-Mayhem only NET | non-Mayhem P(broad sniper) |
|---|---|---|---|
| Oct 10 | +0.0240 (t 2.9) | +0.0096 (t 3.1) | +4.1pp (t 4.5) |
| Oct 11 | +0.0284 (t 3.9) | +0.0089 (t 2.2) | +4.6pp (t 5.2) |

So the sniper-arrival effect of a fresh wallet is robust and LARGER on standard coins. The NET effect is
~+0.009 SOL on standard coins and falls just short of the |t| >= 2.5 bar on day 2.

**Mayhem, within creator** (260-288 creators who launched both kinds; `mayhem.py`, `outmayhem.txt`):
| outcome | Oct 10 | Oct 11 |
|---|---|---|
| broad sniper in launch block | **-6.7pp (t -5.0)** | **-5.4pp (t -4.1)** |
| non-agent bot SOL | x1.24 (t 5.4) | x1.42 (t 8.1) |
| human SOL | x0.96 (t -2.8) | x0.97 (t -2.4) |
| creator fee | -0.0003 (t -0.2) | +0.0008 (t +0.5) |
| dev NET (incl. +0.0027 cost) | +0.0085 (t 0.3) | -0.0173 (t -0.7) |

The agent is 69-70% of a Mayhem coin's volume and pays no fee. Mayhem draws more small bot trading but no
more creator fee, and it drives away launch-block snipers by more than a fresh wallet adds.
