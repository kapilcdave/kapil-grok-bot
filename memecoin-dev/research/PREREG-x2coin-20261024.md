# PREREG: X post -> new coin, arrival lag and outcome (2026-10-24)

Written BEFORE any outcome is read. Exploratory descriptive look at ~8 h; the formal test needs more days.

## Question
The operator's thesis: the time between a news post on X and the coin's market-cap jump is long enough to do deep
analysis and still enter early, and entering on that basis should win almost always. Which parts survive an
unbiased denominator?

## Data (collected raw, joined offline; nothing decides live)
- j7tracker social feed on the laptop (`research/j7tape.py`): tweets from the account list j7 follows, with X
  `createdAt` (ms) and our `recv_ns`; `ai_suggestion` (j7's coin name/ticker for the tweet) and
  `ai_suggestion_update` (existing coins j7 matched, mint in the image URL).
- pump.fun chain tape (`crypto/dexarb/snipefeed.py`, public RPC logsSubscribe, creates + trades of the watched set):
  `~/tapes/x2c-chain-<stamp>.jsonl`.
- Join: `research/x2coin.py`.

## Definitions
- Tweet = j7 event `tweet` of type TWEET or QUOTE (replies and the Grok-bot spam are dropped) that has an
  `ai_suggestion`. Keys = normalised (lowercase, alnum only, length >= 3) `prediction`, `ticker`, `ai_ticker`.
- Linked coin = a pump.fun create in [tweet, tweet + 30 min] whose normalised name or symbol equals a key.
  A coin links to its earliest matching tweet.
- Outcome = ladder from entry at max(create, tweet) + DELAY + 0.5 s landing: win = price touches 2.0x entry before 0.6x
  within 60 min, else marked at the last price; round-trip cost 3%. DELAY in {5, 15, 30, 60, 120} s.
  DELAY 0 is NOT reported: the first fills are the dev buy and same-block snipers (a measured artifact on 10-14).
- Control = every other non-self-graduated create in the same window with the same outcome function.
- Coins with no trade observable at or after the entry time are dropped from n and counted, never scored as losses.

## Primary comparison (one, read once)
Win rate and mean realised return of LINKED coins at DELAY = 15 s versus CONTROL, with Wilson / bootstrap 95% CIs.
Secondary (descriptive only): lag tweet -> create, create -> first 2x, tweet -> first 2x quantiles; the same
table split by author followers (>= 1M vs less) and by first-linked vs later-linked.

## Power
With ~8 h the linked set will be tens of coins at best. The CI will be wide: a win-rate difference below ~10pp is
not detectable. State that the result is inconclusive if the CI spans both "no difference" and "+10pp"; do not
read a point estimate. The formal score needs >= 300 linked coins or 7 days, whichever is later.

## Already known (do not re-derive; Mayhem coins excluded, see RESULT-postlag-20261024.md)
- 10-14 curve tape, 11,853 launches, post-linked at +5 s: win (2x before -40%) 7.6% vs no-post 5.3%; at +15 s 6.0% vs 4.5%.
  Stop-first 19% / 15%. Mean realised return of post-linked at +15 s is -10%. A gradient-boosted model on 15
  decision-time features does not lift the top bins above zero on a time-split holdout (`outpostmodel.txt`); the
  top 2% at +30 s is +17.5% / +5.6% with CIs spanning 0 (n 26-39).
- j7 delivers a tweet a median 0.19 s after X `createdAt` (p90 0.32 s), and its AI ticker about 1 s after the tweet.
