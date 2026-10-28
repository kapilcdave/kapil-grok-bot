# PREREG: narrative runners (paper), 2026-10-21

Written before any tape exists. The rule, the costs and the gates below do not change once the
recorder starts. A changed rule is a new preregistration on a fresh window.

## Thesis (operator, 2026-10-21)

Every day some memecoins run from about $1M to several million in market cap on a news or social
story. We stop competing at launch and instead read the story, find the coin that is actually *the*
coin for it (not a vamp, a misspelling or a "2.0"), and hold it for hours. Cabals and Telegram groups
run many of these; we follow the runners, we don't fight them in PvP.

## Instrument

`runners/record.py`, on the box. A coin is **admitted** the first time it is seen with market cap
$150k-$100M, liquidity >= $15k and a deepest pair <= 7 days old. Discovery: GeckoTerminal
trending (5 m, 1 h, 6 h), GeckoTerminal top 24 h volume, DexScreener profiles and boosts.
Every admitted coin is enriched once (`enrich` row) and priced every 60 s for 12 h, then every 5 min
to 72 h.

## Primary rule (`runners/rule.py`, frozen)

At the time enrichment finished:

1. market cap at admission $150k-$5M;
2. biggest **and** earliest of its look-alikes (DexScreener search on its ticker and name, launched
   within 48 h, liquidity >= $5k);
3. a story: it links a specific X post, or Jev `news_event` >= 0.5 (a headline dated before admission
   reports a fresh event about the coin's subject).

Amended 2026-10-21 ~08:10Z, before any return was looked at: the first Jev wording ("same subject")
passed kimchi recipe articles (0.72) and PHP (0.55) and picked the Kimchi vamp as the original; the
"event" wording gives Kimchi 0.60, its vamp 0.45, PHP 0.42. Jev no longer picks the original (code does).
The ~10 min of tape recorded before the amendment was deleted, and the run restarted with Jev on.

Amended 2026-10-21 ~19:45Z (instrument, not rule), before any return was looked at: the first 11.6 h
(08:07-19:45Z, 122 admits, 13 picks) admitted NONE of the day's real runners (NI, Lovot, MOON COIN,
pablo, JONATHAN, BINA, NORMIE: $40-80k to $2-5M within 1-2 h). They are Meteora DBC launches that
trade in PumpSwap pools: page-1 trending never listed them, DexScreener's batch endpoint returns
only their dead curve pair, and their pools report liquidity $0. Fixed: discovery reads trending
pages 1-2 plus the 24 h tx-count sort; such coins are priced through their real pair; liquidity falls
back to GeckoTerminal's reserve; a coin with no reported liquidity is admitted at >= $50k volume in
the last hour and costed at a flat 5% impact per leg. The 11.6 h tape is archived as
`data/v0-blind-to-dbc/` and NOT scored; the 21-day clock restarts.

Interim look 2026-10-21 ~22:25Z, at the operator's request: mark-to-market of the first 4 scorable
picks (PHP -6%, TRUMP -92%, ChatGPT -100%, shiit +10% net; control n=45 mean -31%). Rule unchanged.

Amended 2026-10-22 ~02:30Z (instrument bug, found AFTER the interim look, while checking runners the operator
named): when DexScreener's search did not return the coin itself, its look-alike ranks were 0 and it failed
condition 2 as a "vamp". 11 of 202 admits on 10-21 (PANGEA, admitted at $418k and 8x within 5 h; CYBER).
`rule.original_ranks` now ranks the coin by its own admission market cap and pair time; the scorer applies
it to every row, old and new. Condition 2 is otherwise unchanged.

## Trade model (`runners/score.py`, frozen)

- Entry: first snapshot within 3 min after the `enrich` row. Exit: first snapshot >= **4 h** later
  (primary; 1, 12 and 24 h reported, not gated). Pair gone before the exit = -100%.
- Cost per leg: 1% (fee, priority fee, slippage slack) + $100 / (liquidity / 2); with no reported
  liquidity, 1% + 5%.

## Gates (all must pass)

- **G1 real:** primary 4 h mean net > 0, bootstrap 95% CI lower bound > 0.
- **G2 selection:** primary minus control (same $150k-$5M band, failed the rule) > 0, CI excludes 0.
  Without G2, "it went up" is just "trending coins went up that week".
- **G3 replicates:** the first and second halves of the picks by time both have mean net > 0.
- **G4 not one coin:** dropping the single best pick leaves mean net > 0.

Score **once**, at 60 primary picks with a 4 h exit or 21 days, whichever comes first. Before
that, look at counts and health only (picks per day, admits, gaps), never at returns.

## Secondary (reported, not gated)

`no_story` (rule without condition 3, so primary minus this measures what the story evidence adds),
`jev`, `vamps` (do vamps underperform originals, as the thesis says), the top-10 holder share split at
50% (sign unknown: Kimchi, the day's biggest runner, had 78.7%), and session of admission
(Asia 23-07Z, Europe 07-13Z, US 13-23Z). These are hypotheses for the next preregistration.

## Not in this test

No order is sent and no wallet is loaded. Passing every gate makes the case for a small live clip,
decided separately with its expected loss stated first.
