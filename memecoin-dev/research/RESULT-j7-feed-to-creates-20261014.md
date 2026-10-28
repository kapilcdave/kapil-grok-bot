# j7tracker feed -> pump.fun creates -> can we trade them (2026-10-14)

Data: `research/data/j7creates-20261014/` (j7 feed tapes, PumpPortal create tape, 135 coins' early chain trades).
Code: `research/j7tape.py`, `research/j7creates.py`, `research/chaintrades.py`. One 22-minute window, 221 live posts,
422 pump.fun creates. NOT preregistered; descriptive. Read the n's.

## 1. Are coins created off feed posts?
- j7's `ai_suggestion_update.results` (max 5 coins) is NOT "coins launched for this post": it is there 0.9 s after the
  tweet (p50), and every dated result (17) was created before the post. It is a name search over existing coins.
- Strict name/prefix match of creates to posts within 300 s: 29 matches on 14 posts, vs 11-28 under a rotated-clock
  placebo (post times rotated inside the tape). No effect across posts in general.
- Two clusters ARE post-driven (zero coins before the post, many after):
  - "Peptide": StockMKTNewz "NEW ETF LAUNCH ... Peptide ETF". First coin +18 s, 80 coins, 23 distinct creators, peak 17/min.
  - "Callout Rewards": @a1lon9 (pump.fun cofounder) post. First coin +1 s, 15 coins, 10 creators.
  Others (Neutron/Rocket Lab +5 s, Anthropic +3 s, Gemini +17 s) are single coins.

## 2. Could we trade them?
- Feed: PumpPortal create stream reaches us 0.5 / 1.0 / 1.4 s (p10/p50/p90) after the chain blockTime.
- Activity: median TRADES in the first 120 s = 0 for Peptide and Callout (1 for random). 31% of Peptide coins and 27% of
  Callout coins see any outside buy at all. Where one exists it is in the create slot or the next: first-outside-buy dt
  p50 = 0.0 s (Peptide), 0.2 s (Callout). That is inside the block, before our feed shows the create.
- Entry sim (first on-chain trade >= L after create, exit last trade <= H later, -3.5% round trip): reachable L >= 1 s
  has n = 20 (Peptide), 4 (Callout), 17 (random) coins with a fill. Peptide L=1 H=15: gross +2.6% mean, -2.1% median,
  30% win, net -0.9%; H=60 net -3.6%. Callout L=1: -20% gross (n=4). Random coins look the same (+/- few %).
  Nothing separates the post-driven coins from random ones; n is far too small to call any cell.
- 60/80 Peptide entries have NO fill: the coin never trades after the create. Copy-flood coins are mostly dead.

## 3. Snipe vs scalp, from the DZo2 (Frankfurt) seat
- Snipe: the only buyers are in the creation slot. We learn of a create ~1 s (2-3 slots) after it. A j7/PumpPortal-fed
  seat cannot be in that block (see in-block-position-is-a-lottery-over-every-free-lever: 7% reach pos <= 82).
  Unchanged: creator-bundle / own-feed territory.
- Scalp: after ~1 s there is usually nothing to trade (no counterparty), and where there is, gross ~ 0 against a 3.5%
  floor. Not a positive shape at this n.
- Not modelled: the bundle/tip cost of a tiny clip (DZo2 holds ~0.08 SOL; a 0.0085 SOL tip is ~10% of it).

## Caveats
One window, one big-news cluster and one pump.fun-internal cluster. Slot-based dt (0.4 s/slot), blockTime 1 s. Price
= curve SOL delta / token delta (net of fee). PumpPortal trade subscriptions need a funded API key (0.02 SOL):
not used, trades came from QuickNode (1,823 calls).
