# PREREG: news first, rule v2 (live rival ranks), ladder exit (paper), 2026-10-23

Written before v2 has decided a single coin. Rule, exit, costs and gates are frozen.

## Why

Operator, 2026-10-23: "our win rate can be very very high … in hindsight it seems so obvious: we see the tweet,
the news, the headline, jump in on the coin and make a couple x."

The interim look (amendment 4 of PREREG-newsfirst-20261022.md, in sample) showed what hindsight hides. Same-named
coins launch together and the bot picked both coins of each pair:

| coin   | admitted | cap at admission | outcome (ladder)          | launch rank among rivals |
|--------|----------|------------------|---------------------------|--------------------------|
| Humor  | 03:29Z   | $64k             | 4.2x peak, +176%          | 1 of 12                  |
| Humor  | 03:29Z   | $33k             | -100% in one minute       | 12 of 12                 |
| MEOWE  | 03:51Z   | $55k             | 8.8x peak, +246%          | 1 of 12                  |
| MEOWE  | 03:51Z   | $33k             | -100% in one minute       | 12 of 12                 |
| mint   | 04:37Z   | $40k             | 8.6x peak, +279%          | 5 of 5                   |
| mint   | 05:01Z   | $47k             | -100% in one minute       | 9 of 9                   |

Cause: condition 4 ("the biggest of the coins sharing the story word") compared the coin's cap with each rival's cap
from when the rival was last on a Jupiter list, often its launch, so every member of a pair ranked itself first.

## Rule v2 (`rule.news_why_not(..., ranks="news_ranks_live")`)

Identical to the v1 rule (including amendment 3b and the avoid filter), except that condition 4 uses
`newsfirst.live_ranks`: the coin and all of its rivals re-priced in one DexScreener batch at the decision.
A rival DexScreener does not list keeps its stale cap; a failed batch fails condition 4. Two variants are
recorded: `news_pick_v2` (headline or trending search) and `burst_pick_v2` (a burst of same-named launches,
the X-post proxy). **Primary group: `any_pick_v2` = either.** Each coin counts once.

## Exit (primary): `exits.Ladder`, as in PREREG-model-20261022.md

Entry: the first snapshot within 3 min of the decision. Stop -40% (trailing once +50%), 1/3 at 2x, 1/3 at 4x,
rest trailed at 4x the coin's 1-min volatility (20-50%), never below entry after a take-profit, 72 h max.
Cost 1% + impact per leg (flat 5% when no liquidity is reported). The 4 h hold is reported, not gated.

## Gates (forward only: coins decided after deploy, scored once at 60 `any_pick_v2` coins whose ladder has closed, or 21 days)

- **W1 win rate:** any_pick_v2 ladder win rate (net > 0) has a Wilson 95% lower bound above the win rate of
  `news_control_v2` in the same window. Interim: control ~18%, picks 33-44%. At n = 60 the bound sits ~12 pp
  under the point estimate, so this detects a pick win rate of ~35% or more over an ~20% control.
- **W2 real:** mean ladder net > 0, bootstrap 95% CI lower bound > 0 (SD ~90%: detects a mean of ~+25%).
- **W3 replicates:** both halves by time have mean net > 0.
- **W4 not one coin:** dropping the best pick leaves mean net > 0.

Operator's "very very high" win rate is read as >= 60%; reported is whether W1's lower bound reaches it.

## Known losses this does not address

Of the interim losers, most fell -90% or more between two 60 s snapshots (pulled liquidity). No stop on a
60 s clock exits those; v2 tests whether picking the right twin avoids them.

## Amendment 1 (2026-10-24 ~08:00Z): pool-flap bug, and a look at 52 of 60

At 52 closed v2 picks (18 open), operator asked for status. Found: the tracker re-picks each coin's deepest pair
every minute and flapped between pools; 119 of 368 news coins switched, 157 switches jumped the price > 3x in one
minute. Both of v2's big "winners" were flaps (XFILMS 13.9x on an $8k copy pool, Friendship 4.2x on a $4k pool).
Fix (`score.Pin`, also used for the model's labels from 10-24): follow the entry pool; move only after 3 snapshots
in a row on another pool with >= $5k liquidity, or after the entry pool is unseen for 30 min.
At 52 picks: raw prices win 13.5% vs control 15.7%, mean -36%; pinned win 9.6% vs control 13.3%, mean -58%.
All four gates fail on either price series; W1 cannot pass even if the 8 remaining picks all win. Scored
formally at 60.
