# RESULT: X post -> coin lag and win rate on the curve tapes (2026-10-24, overnight)

EXPLORATORY. Code `postlag.py`, `postmodel.py`; outputs `outpostlag.txt` (10-14), `outpostlag-blockB.txt`
(10-16/17), `outpostmodel.txt`. Every launch is in the denominator; Mayhem coins (the pump.fun agent trades them)
and self-graduated brand coins (creator buys >= 50 SOL) are dropped. Winner = price touches 2x entry before 0.6x
within 60 min on the bonding curve; entry = first fill 0.5 s after create + DELAY.

Operator thesis (10-24): timing the entry off the X post gives a win rate near 100%.

## What the tape says
1. **DELAY 0 is an artifact.** "Buy at create" shows 15-19% wins only because the first fills are the dev's own buy
   and same-block snipers pushing the price. Not reachable; all numbers below start at +5 s.
2. **Reachable win rate is single digits.** 10-14, 11,853 launches: at +5 s all launches win 6.4%, post-linked 7.6%
   [6.8, 8.5], no-post 5.3% [4.6, 6.1]; stop-first 19% for post-linked. At +15 s: 6.0% vs 4.5%; at +60 s: 4.2% vs 3.1%.
   Block B (10-16/17) agrees in sign (+5 s: 6.8% vs 2.6%) but its trade watch ends early (2,540 of 28,174 launches still
   have trades at +120 s), so its lags are right-censored: weak support only.
3. **Post freshness is not a clean lever.** Win rate at +5 s by post age at create: post dated after the create 12%,
   0-5 s 9.0%, 5-15 s 6.5%, 15-60 s 8.4%, 1-10 min 7.2%, > 10 min 4.2% (CIs ~ +-3 pp; only "> 10 min" is clearly low).
   Fresh posts do not beat 15-60 s-old ones.
4. **Lag (post-linked coins that doubled, 10-14).** 4.6% of post-linked launches (301 of 6,517) doubled after
   create+5 s. Create -> jump median 59 s (p25 20 s, p75 156 s, p90 391 s); post -> jump median 130 s. Half the
   jumps are over inside a minute of the create, so a pipeline that needs minutes of "deep analysis" after the
   create enters after the move.
5. **Decision-time features do not pick them.** 15 visible-at-decision features (post age, coins already on that post,
   creator history, dev buy, early buyers/SOL, price drift, first-buyer lag) in a gradient-boosted regressor on
   realised ladder return, time-split both ways: at +15 s mean R is negative in every bin including the top 2% (base -10%; top-2% win rate 7%/15%).
   At +30 s the top 2% is +17.5% (n 26) and +5.6% (n 39) with CIs spanning 0; top-2% win rate 39%/26%.
6. **Graduated runners (10-23, volume-selected so hindsight-biased).** Of 114 coins with a post, 62 ran >= 5x from the
   first candle. For those: post -> create median 38 min (p10 2 min, p90 13 h), create -> peak median 31 min
   (p10 11, p90 72), post -> peak median 82 min. So runners leave tens of minutes; the problem is that this is only
   known after they have run. Among every news-path flag that day the post coins were 19% 2x-first, 70% stopped first.
7. **Arrival latency is not the bottleneck.** j7 tweets arrive a median 0.19 s (p90 0.32 s) after X `createdAt`, the AI
   ticker 1.0 s after (`ai_suggestion`, n 157). The chain side, not the post feed, sets the speed.

## Verdict
Nothing on this tape supports a near-100% win rate. The best observed precision for a reachable entry is 26-39% in
the top 2% of a ranker at +30 s (n 26-39), with intervals that include zero return. The post is a modest, repeatable lift in base rate
(about +1.5 to +4 pp at +5 s), not a selector. Open and worth the forward test: j7-suggested names joined to the new
coins that follow them (`PREREG-x2coin-20261024.md`, `x2coin.py`; collectors started 07:57Z).
