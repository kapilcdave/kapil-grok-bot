# PREREG: learned pick score + price-driven exit (paper), 2026-10-22

Written before the model has scored a single forward coin. The procedure is frozen: model class,
features, label, retrain schedule, threshold rule and exit. The weights are NOT frozen; they are
re-learned every 6 h from outcomes already closed, which is the point.

## Why (operator, 2026-10-22)

"It has to be dynamic. We can't hardcode filters or we will miss things." On 10-21 a fresh coin whose
curve was bought out at launch rugged 84% of the time; on 10-22 that same fingerprint was OA ($58k ->
$20.9M), VECTOR ($50k -> $4.6M in 30 min) and SHAVCOIN ($46k -> $4.4M), all seen by the bot at
$150k-$700k and all rejected by the hand rules. And: "if the bot sees it at X and it peaks at Y, take
profits on the way up and stay protected; we don't know when it crashes, only that it will."

## Procedure (`runners/model.py`, `runners/exits.py`, in `record.py`)

- Sample: every coin at its decision (enrichment) on either path, price-led (`enrich`) or news (`nenrich`).
- Features (`model.vector`, ~60): price, liquidity, volume and buy/sell flow; age, graduation lag,
  launchpad; dev buy SOL, share, moved/sold in 10 min, wallet age, dev's coin count; first-hour
  candles; Jupiter organic score, organic buyers / traders, organic buy share, net buyers, holders and
  holder change, top-holder and dev share; SOL fees paid by traders (priority fees, tips, share of
  trades paying them, estimated SOL per hour); story (X post, coin launched after the post, Jev kinds
  incl. charity and prediction market, mainstream, news event, named-after-story, fresh event),
  look-alike ranks, hour of day.
- Label: the ladder exit (`exits.Ladder`) ended in profit after costs. Entry = first snapshot within 3 min
  of the decision. Ladder: stop -40% (trailing once +50%), sell 1/3 at 2x and 1/3 at 4x, the rest on a
  trailing stop of 4 x the coin's 1-min volatility (20-50%), never below entry after a take-profit;
  72 h closes; delisted = remainder lost. Costs: 1% + impact per sell, as in `score.py`.
- Model: L2 logistic regression, standardised, retrained every 6 h on samples whose exit had CLOSED
  before the retrain.
- Pick: score >= the 80th percentile of the scores the current model gives the last 48 h of training coins.

## Gates (forward only: coins decided after this file, scored once at 60 model picks or 21 days)

- **M1 real:** model picks' mean net (ladder) > 0, bootstrap 95% CI lower bound > 0. Ladder nets are
  bounded below near -45% except gaps and rugs; with an SD near 80%, n = 60 detects a mean of ~+20%
  or more and is blind to smaller edges.
- **M2 selection:** model picks minus all coins on the same days (same exit) > 0, CI excludes 0.
- **M3 replicates:** both halves by time positive.
- **M4 not one coin:** dropping the best pick keeps the mean > 0.

Reported, not gated: the ladder on the fixed rules' picks (v1, news, avoid), and the ladder vs the 4 h
hold on the same coins.

## Instrument note

From 10-21 ~21Z the 60 s price snapshots had stretched to a 4-14 min median gap: discovery, news and
enrichment shared one loop with rate-limited GeckoTerminal calls. Fixed 10-22 ~18:50Z: snapshots run on
their own thread, DexScreener only, GeckoTerminal reserves cached 15 min and never retried there.
Returns measured from the 10-22 tape before the fix are on coarse snapshots.

## Prior result to beat

Memory: on the pump.fun curve, smarter exits did not create an edge; selection did. Here M2 is the test
of selection; the ladder alone is reported on all coins as the "exit only" baseline.
