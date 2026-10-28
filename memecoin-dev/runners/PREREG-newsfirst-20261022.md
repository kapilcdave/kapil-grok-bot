# PREREG: news first (paper), 2026-10-22

Written before the news-first path recorded anything. Rule, costs and gates are frozen; a change is
a new preregistration on a fresh window.

## Thesis (operator, 2026-10-22)

Buy low, sell high: catch the coin BEFORE it runs, because the run is fast. Start from the story
(headlines, trending searches, the burst of launches an X post sets off), find the coin named after it
while it is still at $30k-$300k, and only then apply the vamp and avoid filters. The price-led
recorder (`PREREG-runners-20261021.md`) arrives after the run: PHP at $836k after $67k -> $761k,
Macca at $1.07M after 3x, and CYBER 12 min before its peak.

Timing evidence: "PHP warns admins of two critical flaws" (BleepingComputer) 20:56Z 10-20; Ars Technica
02:12Z; PHP-UP-2026 launched 05:14Z 10-21 and ran $67k -> $761k by 08:00Z. The story led by 8 h.

## Instrument (`runners/stories.py`, `runners/newsfirst.py`, in `record.py`)

- Stories, every 10 min: Google News top + 7 sections, Google Trends US (RSS); plus launch bursts
  (>= 3 new coins from >= 2 devs sharing a name word within 30 min) from Jupiter's newest-coins list.
- Coins, every 30 s: Jupiter recent, top trending, top organic score and top traded (5 min), coins
  created within 72 h. A coin whose name/ticker shares a word with a story already seen, a word that
  is in at most 4 live headlines, goes on the news watch.
- A watched coin is a candidate the first time its Jupiter market cap is $30k-$300k; it is priced
  (DexScreener) and written as `nadmit`, enriched as `nenrich` (X post, features at admission, Jev).

## Rule (`rule.news_why_not`, frozen)

At the end of enrichment: 1) DexScreener market cap at admission $30k-$300k; 2) matched a news
headline or trending search (bursts are a separate secondary); 3) Jev `named_after_story` >= 0.5 and
`variant` < 0.5; 4) biggest by market cap of the watched coins sharing its story word; 5) not `avoid`.

Amended 2026-10-22 ~05:50Z, before any return was looked at: condition 3b, the coin reacts to the story
(created after a matched headline was published, or Jev `fresh_event` >= 0.5). The first news pick was
JONATHAN, a 10-21 coin, on an NFL trade-deadline headline about a player named Jonathan (fresh_event
0.44). The 15 min of news rows before the amendment are scored under the amended rule like the rest.
DexScreener market cap 0 at admission falls back to Jupiter's (`mc_src: jup`).

The `named_after_story` wording was chosen on 9 hand cases, including the operator's runners
(PHP-UP-2026, cybercurrency, Macca, Microduck): those are IN SAMPLE and prove nothing.

## Trade model

Same as `runners/score.py`: entry at the first snapshot within 3 min after `nenrich`, exit at the
first snapshot >= 4 h later; cost per leg 1% + $100 / (liquidity / 2), flat 5% impact when no
liquidity is reported. Gone = -100%. 1, 12 and 24 h reported, not gated.

## Gates (all must pass), scored once at 60 news picks with a 4 h exit or 21 days

- **N1 real:** news_pick 4 h mean net > 0, bootstrap 95% CI lower bound > 0. Memecoin 4 h net
  returns have an SD near 100%, so at n = 60 the CI excludes 0 only if the mean is above ~+26%:
  the test is designed to detect a mean of +30% or more, and is blind to smaller edges.
- **N2 selection:** news_pick minus news_control (candidates in band that failed the rule) > 0.
- **N3 replicates:** both halves by time have mean net > 0.
- **N4 not one coin:** dropping the best pick leaves mean net > 0.

## Secondary (reported, not gated)

`news_pick_no_avoid` (does the avoid filter cost news coins?), `burst_pick` (the X-post proxy),
`subject_words` (matched on a capitalised subject word or not), Jev `fresh_event`.

## Not in this test

No orders, no wallet. j7tracker / X firehose not wired (12 h token, CAPTCHA login); launch bursts
stand in for X. Health and counts only before the score point.

## Amendment 4 (2026-10-23 ~06:50Z): interim look, NOT the score

Operator: "our win rate can be very very high … we see the tweet the news the headline and jump in." To test
that claim, the news path's returns were looked at at 9 rule picks (4 h exit n = 6), well before the 60-pick
point. Ladder exit: news_pick win 44% (4/9), touched 2x 44%; burst_pick win 33% (7/21); news_control win 18%
(n = 50). 4 h hold: news_pick win 0% (0/6). This look is in sample and proves nothing; the v1 gates are still
scored once at 60 picks. It found the stale-rival bug that rule v2 (PREREG-newsfirst-v2-20261023.md) fixes.
