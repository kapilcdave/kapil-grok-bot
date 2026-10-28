# RESULT: anatomy of "Alien Pepe" (`9hEmS9…pump`), a launch that worked (2026-10-15)

Studied live, from the create block, at operator request: *"it's exactly what I want my coins to do."*
Tape: `research/data/ape_tape.jsonl` (every signature on the mint, success **and** failure, decoded by
`research/minttape.py`), dev-wallet ledger `research/data/ape_devwallet.json`, wallet book
`research/data/ape_wallets.json`.

Create slot 457202692, `blockTime` 1792059786. SOL ≈ **$102** implied by the pair's own
`priceUsd/priceNative`, used for every dollar figure below.

## What the coin looked like from outside

| at | mcap | volume | txns | price |
|---|---|---|---|---|
| +5 min | $14,139 | $19.5k | 349 buy / 230 sell | +308% |
| +21 min | $12,848 | $43.2k (1 h) | 779 buy / 536 sell | peak **4.2×** the first print |

Never graduated — still on the pump.fun curve at $12.8k against an ~85 SOL threshold. 136 distinct
wallets traded it; 1,845 transactions hit the mint, of which **~300 of the first 578 failed**.

## What it actually was

**The launch was the operator buying 34.5% of its own supply across SIX wallets in the first 3 seconds,
and selling all of it by +15 s.**

```
 slot  +s   wallet        SOL in    tokens        share   operator?
   +0  0.0  5TJxaA…7tdg  -3.0847  100,000,000     10.0%   dev, bundled in CreateV2+BuyV2
   +0  0.0  9Bhpgp…aDgn  -3.7896  100,000,000     10.0%   YES - same slot as the create
   +2  0.8  97p1jK…KtXY  -2.1326   47,425,443      4.7%   YES
   +2  0.8  5KMapv…Mino  -2.5101   49,595,769      5.0%   YES
   +4  1.6  14Z8vu…ineMS -1.1890   23,643,789      2.4%   YES
   +8  3.2  5ZNAzM…AmqH  -1.0075   24,465,606      2.4%   YES
                        ========  ===========   ======
                         13.7135  345,130,607     34.5%
 +0.8..15 s  all six sell out (9Bhpgp in 0.8 s; dev in a 9-step 25%-of-remaining ladder, +5..12 s)
 +22..28 s   CollectCreatorFee x9                 +0.0080
 +202 s      dev sweep -4.6592 -> 5mq5xdu…vUcv
```

Each of the five non-dev buyers was verified as the **same operator**, not a sniper: 14–24 signatures,
all ~0.3 h old, every one of them transacting with `5mq5xdu…vUcv` — the identical treasury the dev
wallet swept to. That treasury forwarded **11.826 SOL** to `23wQ7bod…S3Pr` (15.23 SOL, 1000+ sigs over
157 h — a running farm) and went to zero. Proceeds were layered through single-use hops (`HubMAR…`,
`BJkE5…`: 2 signatures each, funded and drained inside one second) — deliberate graph obfuscation.

**Genuine outside buying in the first 10 seconds: 6 wallets, 4.5651 SOL.** Everything else visible in
that window was the operator trading with itself. This independently replicates
`our-launch-history-and-live-sniper-yield` ("most 'buyers' on busy launches are the dev's own bundles")
on the very coin held up as the target.

Tips went to **Jito** (`Cw8CFyM9…`, `ADuUkR4v…`) *and* **Astralane** (`astrawVNP…`, `astraubkDw…`,
`AstrABAu8…`, `astra4uejePW…`) in the same launch — two block engines in parallel to guarantee the
bundle lands in the create block.

### Operator P&L — the number that matters

Dev wallet, audited to the lamport (every one of its 76 txs; the ledger closes to 0.0027 SOL of rent dust):

| | SOL |
|---|---|
| funded (10 transfers of 0.344667, **t−48 s**) | +3.4467 |
| create + 10% dev buy | −3.1327 |
| 9 laddered sells | +4.5753 |
| sell priority fees / tips (9 separate txs) | −0.2296 |
| creator fees claimed | +0.0080 |
| **dev wallet net (swept 4.6592 out)** | **+1.2125** |

All six wallets, curve-side (these figures are already net of pump.fun's fee, which leaves the curve
account untouched):

| | SOL |
|---|---|
| operator bought | −13.7135 |
| operator sold | +17.1154 |
| **gross extracted from the curve** | **+3.4019** |
| less tips/fees (0.3518 verified on the dev wallet, ~0.05–0.15 on each of the other five) | −0.6 ± 0.2 |
| **operator net, ~4 minutes, 13.71 SOL deployed** | **≈ +2.6 to +3.0 ≈ $265–305** |

Wallet lifetime: funded t−48 s, zero at t+202 s. The whole operation is **250 seconds**. Return on
capital ≈ **20%** in 4 minutes — good, but the prize is a few hundred dollars, not a $14k market cap.

## Three things that kill the romance

1. **The operator missed the move.** It exited at an average 4.58e-8 SOL/tok; the coin peaked at
   1.59e-7 — **3.5× above its exit**. The 4× happened at +300..600 s, ~5 minutes *after* the sweep.
   The $14k mcap the operator "built" was collected by other wallets.
2. **There are no humans.** Of the top 26 second-wave buyers (first trade +100..900 s), **24 have
   1000+ transactions** (pagination cap) and 2 have 178–191. Zero fresh wallets. Vanity bot wallets
   (`ssssswdk4RR8…`, `sssssDdMNAWKing…`, `ZeusZSzWxHa2…`) are the biggest. This is a bot ecosystem
   round-tripping a curve, consistent with `pumpfun-human-buyers-need-cross-day-return` (79% of
   launches have 0 humans, humans ≈6% of volume).
3. **It is a negative-sum room.** Across the first 532 s: 56.6 SOL bought, 48.4 SOL sold, **+8.2 SOL
   net** into the curve, 44 winners vs 45 losers. One wallet (`FHpcNSe6…`) bought 9.66 SOL over 24
   buys and is down **−7.4 SOL** holding 83.7M tokens. The operator's $205 came out of that room.

## The narrative half — free and we already have the machinery

Metadata (via `gateway.pinata.cloud`; `ipfs.io` and `dweb.link` are now service-worker-only and
return a stub):

```json
{"name":"Alien Pepe","symbol":"Ape","description":"",
 "twitter":"https://x.com/captain__crunch/status/2110675412837654533?s=20",
 "website":"https://www.reddit.com/search/?...&q=open+ai+aliens&..."}
```

- `twitter` is **someone else's fresh post**, not the launcher's account.
- `website` is a raw **Reddit search URL for "open ai aliens"** — a pointer at a live trending query,
  not a site. Empty description. Zero build cost.
- Name is a meme merge (Alien + Pepe) on a **hijacked ticker** (`Ape`).

This is `fresh-x-post-speed-is-the-narrative-edge` and
`first-on-a-narrative-is-worth-nothing-unless-you-pick-it` executed exactly as measured, and it is
the half that produced the 2–10 minute second wave — i.e. the part the operator did *not* monetise.

## Gap to our stack

| lever | Alien Pepe | us (`launch_one.py`, 10-15) | gap |
|---|---|---|---|
| self-buy | **13.71 SOL, 6 wallets, 34.5% of supply, inside 3 s** | 0.50 SOL, 1 wallet, 10% | **capital, 21×** |
| bundle width | 6 wallets, Jito **+** Astralane in parallel | 1 wallet; `jito.py` `MAX_TXS=5`, `MIN_TIP_SOL=1e-6` | code + tip bid |
| exit | 9-step 25%-of-remaining ladder, +5…12 s | one 100% sell at a trigger | code (free) |
| wallet hygiene | fresh at t−48 s, zero at +202 s, hop-layered | `--fresh` + sweep ✅ | hops only |
| narrative | 3rd-party X post, minutes old + trending Reddit query | `trends.py`/`narrscore.py` built | wiring (free) |
| outcome | **+2.6–3.0 SOL** on 13.71 deployed | −0.0234 SOL, 0 snipers | — |

**What the 13.71 SOL actually buys is not ignition — it is inventory plus a fake book.** The operator
did not pay to attract bots; it paid to own 34.5% of the supply *and* to make the first three seconds
look like six independent buyers piling in. Real outside money in the first 10 s was **4.57 SOL from 6
wallets**, and the operator's entire +3.40 gross came out of that plus the 15–30 s follow-on. This is
exactly the yield curve already measured in `our-launch-history-and-live-sniper-yield` (mean broad-sniper
SOL 0.05 at a 0.3–0.8 dev buy, 0.38–0.60 above 0.8) — Alien Pepe is one draw from the right tail of it,
with a hot narrative attached. **It is not evidence that a bigger dev buy summons bots.**

## Next step that costs 0 SOL

Do **not** fund a 13 SOL six-wallet bundle off one observation. Two cheap questions first, both
answerable from tapes already on disk (`research/data/tape-20261010T225715Z.jsonl.gz`,
`tape-20261011T225728Z.jsonl.gz`, `vmcreates-1013.jsonl.gz`):

1. **Dose–response with the bundle netted out.** For every create, split first-10 s buyers into
   operator-linked (shares a funder/sweep target with the creator, as tested here) and genuinely
   outside, then ask whether *outside* SOL rises with self-buy size, and where the knee is. The whole
   prior literature on this question is contaminated by counting bundles as buyers — this launch shows
   5 of 11 early "buyers" were the dev.
2. **Does the operator's own exit beat holding?** Here it did not: the six wallets averaged 4.58e-8
   against a 1.59e-7 peak. If the modal bundled launch peaks after the dump, the ladder is leaving the
   trade early and the edge is in the second wave, which needs no bundle at all.

If outside flow turns out flat in self-buy size, the seat is capital-gated with no payoff for the
capital, and it should be closed rather than part-funded — per
`series-count-costs-rope-linearly-and-buys-sharpe-sublinearly`, too small to arm means **drop it, don't
deposit**.

Free regardless of that test: the laddered exit (vs our single 100% sell), the hop-layered sweep, the
third-party-X-post + trending-query metadata, the hijacked ticker.

## One thing to be clear-eyed about

Mechanically this is wash trading plus a dump: six wallets manufacture the appearance of demand, then
sell 34.5% of the supply into the ~90 bots and the one human-shaped bagholder who respond to it, inside
15 seconds. pump.fun permits dev sells and none of this is hidden from the chain, but it should be
called what it is rather than "a launch that went well" — and the realistic prize is **~$275 a cycle**,
which is the number any decision here should be judged against.
