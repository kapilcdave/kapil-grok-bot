# pump.fun fresh-curve scalping: CLOSED. The candle is green before you can reach it. 2026-09-18.

Question asked: *"the first candle for newly minted is like always green"* — so buy early, scalp,
and repeat. Can a bot make money doing that?

Answer: **no, and not for the reason I expected.** I expected the move to be real and the fees to eat
it. Instead, by the time an outside observer can place a trade, the expected move is already
**negative**. Buying the first reachable trade after a mint's birth and holding — with **zero** fees,
zero transaction cost and negligible size — returns **−1.84%** at 10 seconds and **−8.61%** at 900
seconds, and the price is higher 10 seconds later only **24.0%** of the time. Across 32 causal exit
rules, **0 have a positive gross P&L**. The cost stack is then a further 2.44% on top of a loss.

The premise is simultaneously true and self-defeating: the first candle *is* mechanically green, and a
census of 68,200 real wallets puts **106% of all net profit** in the mint's **own birth slot** —
every latency bucket from +2 to +50 slots is *negative*. That is not a seat you reach by being fast;
a slot is 400 ms and Solana has no mempool, so you reach it only by being **in the launch
transaction**. Restricted to wallets that could actually have got there by reacting, the mean is
**+0.076 SOL with a CI touching zero**.

**Status: CLOSED.** Verdict rule committed in
`preregistrations/pumpfun_first_trade_oracle_20260918.md` §5b **before** the full sample was read.
The verdict it returned is `CLOSED as ADVERSE`.

Sample: **16 windows × 4,500 slots** (~8 h of chain), disjoint, seed 20260918 over the last 80 days.
**928,714 trades, 9,290 curves born in-window, 71,948 blocks** (47 skipped, 5 failed — 0.007%).

$0 spent. Read-only against archived blocks. **No private key was loaded and nothing was signed.**

---

## Wall 0 — the premise is an identity, so it carries no information

On a pump.fun bonding curve price is a deterministic function of tokens sold. At mint the only holder
of tokens is the curve itself, so **no sell is possible**; the first trade is necessarily a buy, and
a bar containing only buys necessarily closes up.

"The first candle is always green" is therefore not an observation about memecoins — it is
arithmetic, the same shape as `an-unconditional-two-sided-maker-is-an-identity`: the label cancels
and what is left is bookkeeping. Mints that never trade emit no candle at all, so the sample in which
the pattern gets noticed is **selected on the very event being predicted**
(`a-breadth-sample-must-be-selected-on-the-gate`).

So the question was never "is it green". It is whether a **reachable fill** ahead of a **reachable
exit** clears the cost stack.

## Wall 1 — the move is negative before any cost is charged

The decisive measurement, and the one that closed the branch. Buy at the first trade at
slot ≥ birth + 2 (Geyser-class latency, ~800 ms), hold to `H`, mark out. **Fee 0, transaction cost 0,
clip 1e-4 SOL so own impact is 0.0007%.** Nothing but the price path:

| horizon | n | gross P&L | CI | P(price up) |
|---|---|---|---|---|
| 10 s | 9,252 | **−1.84%** | [−2.69%, −0.98%] | **0.240** |
| 30 s | 9,145 | −3.12% | [−4.56%, −1.54%] | 0.224 |
| 60 s | 9,008 | −4.35% | [−6.53%, −2.03%] | 0.199 |
| 300 s | 7,928 | −7.91% | [−11.30%, −4.35%] | 0.140 |
| 900 s | 4,933 | −8.61% | [−12.48%, −4.07%] | 0.119 |

Every horizon is negative with a CI clearing zero, and it gets monotonically worse with time. **Only
24% of entries are up even 10 seconds later.**

This is not a statement about my exit rules — it is the price path itself. Confirmed by sweeping the
rules: over the 32-cell causal grid at gross, **0 of 32 cells have a positive mean and 0 of 32 have a
CI clearing zero.** The best gross cell is a 10 s fixed hold at −1.84%; the worst is −8.61%.

The direction matters for what would rescue it, and nothing does: a **negative gross** means the cost
floor never binds. Cheaper fees, better sizing, faster fills and tighter stops all operate on a cost
term that is not the reason for the loss.

## Wall 2 — the cost floor, which turns out not to be the binding constraint

Reported because it was the expected killer and because the arithmetic is worth keeping. Round-trip
cost as a fraction of clip `c`, on a virgin curve holding `S ≈ 30` virtual SOL, fee rate `r` per
side, fixed transaction cost `t`:

```
f(c) = 2c/(S+2c)  +  2r  +  t/c
        impact        fee    fixed
```

Impact **rises** with clip, the fixed cost **falls** with it, so `f` has an **interior minimum** at
`c* = sqrt(S·t/2)`. Measured `t` = 0.000110 SOL (2× median observed fee), giving:

| clip SOL | impact | fee | fixed | total |
|---|---|---|---|---|
| 0.01 | 0.07% | 1.90% | 1.10% | 3.07% |
| **0.0406 = c\*** | 0.27% | 1.90% | 0.27% | **2.44%** |
| 0.10 | 0.66% | 1.90% | 0.11% | 2.67% |
| 1.00 | 6.25% | 1.90% | 0.01% | 8.16% |

**78% of that floor is the 95 bps/side protocol fee** — a venue constant no sizing, latency, signal
or filter can move. Because the floor is a *minimum over all clip sizes*, "no clip size works" is
provable rather than a sweep result; a sweep can only report the sizes it happened to try. Empirical
confirmation: across 6 clips × 32 rules = **192 cells, 0 positive, 0 with a CI clearing zero.**

Decomposing the best net cell, each cost leg matches its closed form to **≤0.03pp**, which is what
licenses treating the cost side as arithmetic rather than as an estimate:

| leg | mean, % of clip | analytic |
|---|---|---|
| gross P&L of the rule | **−2.89%** [−3.96%, −1.65%] | — |
| − curve fee | −1.83pp | −1.90pp |
| − own impact at c\* | −0.24pp | −0.27pp |
| − fixed tx fee | −0.27pp | −0.27pp |
| net | −5.24% | floor 2.44% |

## Wall 3 — the oracle bound is positive, which is why the causal test was mandatory

With **perfect foresight over the exit** — sell at the single best curve state in the window — the
bound is strongly positive: **+38.26%** at L=2, H=300 s, clip 1.0 SOL, and +71.29% for a
birth-slot bundle at a 0.10 SOL clip. A positive bound proves nothing except that the branch survives
to the next gate (`oracle-bound-screens-a-horizon`).

The latency gradient is the interesting part, because it prices the seat:

| L (slots) | H=10 s | H=60 s | H=300 s |
|---|---|---|---|
| **bundle (in the birth tx)** | **26.19%** | **47.91%** | **59.42%** |
| 0 | 9.32% | 29.48% | 39.85% |
| 2 (Geyser-class) | 7.97% | 27.85% | 38.26% |
| 16 (public RPC) | 3.37% | 21.97% | 31.14% |

Being *in the birth transaction* is worth 2.8× the L=0 bound at 10 s. That is not a latency
difference — L=0 is already "first slot after birth" — it is the difference between reacting and
being present.

Every **causal** rule loses, everywhere, and the walk-forward is unambiguous:

| | result |
|---|---|
| best fixed hold (10 s) | −9.26% [−10.05%, −8.47%] |
| best trailing stop (q=15%, 900 s) | −10.30% [−11.25%, …] |
| best tp/sl (20%/10%, L_out 0) | −8.82% [−9.20%, …] |
| **walk-forward: picked on 8 earlier windows, scored on 8 later** | train −8.19% → **test −10.27%** [−11.34%, −9.13%] |
| **cells with a positive TEST mean** | **0 of 32** |

Realisation is negative in every cell: the gap between a +38% bound and a −10% rule is entirely the
value of knowing the exit in advance, which is not for sale
(`gate-the-realisation-product-not-one-factor`).

The horizon decay is real, not a coverage mix shift: on a **fixed** mint set covered to 900 s at
every horizon (n=4,933) it still runs −9.28% → −15.67%
(`capture-is-an-identity-so-pooled-capture-is-a-spread-mix` is the trap avoided).

**Robustness, and it cuts the right way.** Restricted to the 7,078 curves (76.2%) whose reserves move
*only* by their own trades, the oracle bound at 10 s collapses from +7.97% to **−0.37%** — negative
*with perfect foresight*. The filter drops winners, so it should read worse, and it does; the point is
that the bound's short-horizon headroom was substantially an artifact of curves whose SOL reserve
moves outside the trade stream (see trap 1).

## Wall 4 — "a filter would fix it" is closed, and this time the features really do separate

The standing objection to a losing average is *select better mints*. Two tests, added post-hoc and
**disclosed as post-hoc** in prereg §5a — acceptable only because the results are negative, since a
post-hoc test can only launder a claim if it rescues one.

**1. Oracle-bound the selection.** Rank mints by realised causal P&L — perfect foresight over *which*
to enter — and take the top `p`. No screen operating at rate `p` can beat this, so it bounds the
entire filter family (`oracle-bound-a-selection-closes-a-model-family`):

| entry rate | oracle-selected net | worst mint kept |
|---|---|---|
| 2% | +146.90% | +70.46% |
| 10% | +51.26% | +7.83% |
| 20% | +24.66% | −7.43% |
| 100% | −10.55% | −92.80% |

Large headroom exists **if you know which mints**. So:

**2. Do pre-entry features carry that information?** Eight strictly pre-entry features, quintiled,
with the outcome shuffled against the features 20× for the null. Unlike the partial-sample run, the
separation here is **genuinely beyond the null** on both statistics:

| statistic | measured | shuffled (20×) | null p95 |
|---|---|---|---|
| features that "separate" | 7 of 9 | 3.1 | 8 |
| max between-SD / within-SE | **5.27** | 1.42 | 1.97 |
| best single-bin mean | **−7.61%** | −8.95% | −8.10% |

**And it does not matter, for a reason worth stating precisely: every bin of every feature loses.**
The best single bin across all features and all quintiles is **−7.61%** (`vsol_entry`) against a
pooled −10.55%. The features genuinely rank outcomes — they sort *losses*. There is no positive
subgroup to select into, so a screen can only lose less, never win.

Confirmed out of sample. Feature **and** cutoff picked on the earlier 8 windows, scored on the later
8: picked `buy_share_pre` bin 4, **train +22.69% → test −12.03%** [−12.95%, −11.17%], versus a test
pooled mean of −11.77%. **The screen made it 0.26pp worse than no screen at all.**

## Wall 5 — who actually makes money, and why you cannot be them

Not a backtest — a census. Every trade on every curve born inside a window, attributed to its wallet,
no strategy of mine in it. There is nothing to overfit in an accounting identity.

**88,405 (window,wallet) pairs, 68,200 distinct wallets.** The token float closes exactly: outstanding
tokens equal what the curve issued to float precision on 4,390 of 4,412 curves.

Model-free primary — wallets that **fully exited**, so P&L is pure cash flow from the event stream
with no curve model at all (58,273 pairs, 65.9%) — bucketed by how many slots after birth their first
trade landed:

| bucket | n | share profitable | mean SOL | total SOL |
|---|---|---|---|---|
| **same slot as birth** | 7,540 | 49.0% | **+0.767** | **+5,784.7** |
| +1 slot | 749 | 44.1% | +0.076 | +56.6 |
| **+2..4 slots** | 2,302 | 37.3% | **−0.051** | **−116.6** |
| **+5..12 slots** | 4,734 | 34.0% | **−0.061** | **−288.3** |
| **+13..50 slots** | 11,560 | 30.5% | **−0.026** | **−295.5** |
| +51 or later | 31,388 | 35.5% | +0.010 | +300.6 |
| all | 58,273 | 36.3% | +0.093 | +5,441.4 |

The birth-slot bucket alone is **+5,784.7 SOL against a census total of +5,441.4 — i.e. 106% of all
net profit.** Every bucket from +2 to +50 slots is *negative* in aggregate. A slot is 400 ms and
Solana has **no mempool**, so you cannot react to a mint inside its own birth slot — you have to be
**in the launch transaction or bundle**, i.e. be the launcher or party to it. The oracle bound agrees
independently: the bundle row is worth 2.8× the L=0 row at 10 s.

**The one cell that looked like a business, and what it actually is.** Persistent (3+ windows) *and*
active (4+ trades) *and* fast (lat ≤ 1) wallets average **+0.895 SOL** [+0.738, +1.083] model-free.
Splitting that cell by whether the seat can be reached at all:

| | n | mean SOL | CI | total |
|---|---|---|---|---|
| **lat = 0 — in the birth tx, UNREACHABLE** | 1,856 | **+1.010** | [+0.829, +1.185] | **+1,873.8 (99.0%)** |
| lat = 1 — one slot later, reachable | 260 | +0.076 | **[−0.001, +0.163]** | +19.7 |
| lat 2–4 — reachable | 783 | +0.018 | [−0.049, +0.086] | +14.3 |
| lat ≥ 5 — reachable | 5,408 | +0.049 | [+0.010, +0.094] | +262.7 |

**99.0% of the seat is the unreachable bucket.** At the first reachable slot the mean is +0.076 SOL
with a CI touching zero. Persistence does buy something real at reachable latency — 9+ windows gives
**+0.052 SOL** [+0.030, +0.076] per (window, wallet) — but that is ~$10 per wallet per 30 minutes of
chain, it is measured on the **biased-high** fully-exited subsample, and the whole-population bracket
is **[−0.234, −0.011] SOL**, entirely negative. So the reachable edge is not distinguishable from
zero once the survivorship direction is accounted for.

**Beware the top of the distribution.** The 10 largest wallets by total P&L carry **negative realised
cash** (−85.21, −107.44, −14.71 SOL) alongside large positive totals: their profit is almost entirely
*unliquidated bags*, marked at pro-rata liquidation into the final curve state. That mark is precisely
the quantity the reserve anomaly makes untrustworthy (trap 1), which is why every headline number in
this section is realised cash only.

Corroborating from the tape side: **38.2%** of all first-60-second buy volume comes from wallets
already present in the birth slot.

The census closes as an identity should: realised cash −20,722.4 SOL, marked bags +19,766.4, total
**−956.0**, against fees alone of **−2,894.9** (tx −347.4, curve −2,547.5).

The population as a whole plays a strictly negative-sum game — the only external counterparty is the
curve, which keeps the fees — so "find an edge" reduces to "identify the losers before paying the
toll yourself", and Wall 4 says nothing observable pre-entry does that.

**There is also no maker seat.** The one strategy family that has ever made money in this lab is
passive market making (`inplay-wide-maker-income`, `pmus-wide-maker-income`), and on a bonding curve
**the curve is the only maker**. There is no spread to quote, so that family cannot be expressed here
at all.

## Base rates, for scale

- Peak virtual SOL reserve per mint: p50 **33.8**, p90 65.7, p99 225.0, max 2000.0 (curves start at 30).
- **53.8%** of curves never get past 35 vSOL — they raise less than 5 SOL, ever.
- Only **4.9%** exceed 100 vSOL.
- Trades per curve: p50 **13**, p90 165.
- Brand-new curves: ~0.1 per block, ≈21k/day.

## Eight traps this family set, seven of which I fell into

1. **A per-observation invariant validated the decoder, not the system.** Constant product held to
   median `1.1e-11` *within* each trade, so I treated the reserves as a closed system. They are not:
   `virtual_sol_reserves` moves 2–153 SOL between consecutive trades with the token reserve
   **exactly unchanged**, on 19.4% of transitions, giving p90 life-cycle drift 0.865. Re-decoding one
   good and one bad event from raw bytes put both at the same verified offset, proving real structure
   rather than a parse error. This mattered: it inflated the short-horizon oracle bound from −0.37%
   to +7.97%. `per-observation-invariance-does-not-validate-the-state-machine`.
2. **The clean subsample was the biased one.** Restricting the census to fully-exited wallets removed
   the model-dependent mark but selected on *behaviour* — nobody holding a worthless bag ever exits.
   Fully-exited mean **+0.111 SOL** against a population bracket of **[−0.140, −0.006]**, entirely
   negative. The "clean" figure sat above the whole bracket. Fix: bracket the mark by sign rather than
   choosing between modelling it and dropping it.
   `a-closed-position-subsample-is-selection-on-behaviour`.
3. **"Between-group SD exceeds mean within-group SE" is not a test in the positive direction.** It
   fires on 40.6% of pure noise at 5 bins and **72.2%** on a lottery-shaped outcome (92.0% at 10
   bins), because bins holding no tail event have small SEs that drag the denominator down while the
   bin holding the tail pushes the numerator up. This corrected a rule I had been applying elsewhere
   in the lab. `between-group-sd-must-exceed-mean-within-group-se`.
4. **A sign flip that was pure sample size.** On 2 windows the trailing stop showed **+1.70%** at a
   0.05 SOL clip and the gross move looked like **+2.56%** [−1.25%, +6.56%]. At 16 windows the same
   cells are −0.76% and **−2.89%** [−3.96%, −1.65%]. The CI included zero the whole time, which is
   exactly why the verdict rule was committed to the prereg before the full sample was read rather
   than written up from the partial one. When a sweep runs against a *growing* dataset, the sweep and
   its comparison must be re-run together.
5. **A placebo pinned at zero by its own initialiser.** The shuffled best-bin statistic was seeded
   `sp = 0.0` and updated with `max()`. Once every bin mean went negative — which is the whole
   population here — the null degenerated to a constant `0.00%`, making the measured −7.61% look
   *worse* than its own null. Seeded at `None` it reports properly. The partial-sample run hid this
   because bin means were positive then. A placebo needs to be able to represent the sign of the thing
   it is nulling.
6. **A marked bag is not a profit, and the top of the distribution was all mark.** The 10 largest
   wallets by total P&L had **negative realised cash** — their entire result was unliquidated
   inventory, priced by the same reserve model trap 1 discredited. Any "who is winning" analysis that
   ranks on marked P&L ranks on *inventory held*, not on money made. Every headline in Wall 5 is
   realised cash only for this reason.
7. **A quadratic scan that looked like a slow job.** The census rebuilt each wallet's best latency by
   rescanning the full `first_lat` dict per wallet — O(wallet-windows × mint-entries), ~1e10
   iterations on 16 windows. It ran 16 minutes with no output on the full sample and would not have
   finished; a single-pass accumulator does it in **34 seconds**. It was invisible on 3 windows
   (~3e8 iterations, tolerable), which is the trap: *a quadratic is a constant factor on the pilot
   sample and a wall on the real one.* This was the second quadratic in the same file.
8. **The one avoided by pricing it first:** own impact on a virgin curve is `2c²/(S+2c)` — **6.25%** at
   a 1 SOL clip. Any backtest that holds the tape fixed while inserting a 1 SOL clip into a 30 SOL
   curve is not measuring a strategy, it is measuring its own footprint.

## What this does not say

It does not price a **swing** hold of days. It does not price the post-graduation PumpSwap/Raydium
pool, where fees are ~8× lower and where a maker seat may actually exist — that is the one adjacent
branch this work leaves genuinely open, and it is where I would look next. It says nothing about token
selection by social or metadata features, only the eight price/flow features tested.

**Update 2026-09-18: the PumpSwap half of that open branch is now CLOSED as ADVERSE** — see
`FINDINGS_pumpswap.md`. A full-range LP loses 73.49%/day against its own rebalancing benchmark, and
the drift term that dominates its P&L turns out not to be measurable from trade-conditioned data at
all. The Raydium pools older graduates migrated to remain untested.

Two known optimisms, both pointing the same way: the tape is held fixed while our clip is inserted (in
reality our buy raises the price every later buyer pays, and our exit sells into a curve we have
drained), and **Jito tips are invisible** — they are plain SOL transfers, not part of the transaction
fee, so measured transaction cost is a lower bound on landing a competitive snipe. Both make the true
result worse than reported, which is the only reason a negative result is decisive.

## Reproduce

```sh
cd crypto/dexarb

# collect: 16 disjoint 4,500-slot windows over the last 80 days, seed 20260918.
# ~4h, resumable via data/pumptape/manifest.json + per-window DONE markers.
caffeinate -dimsu python3 pumptape.py --windows 16 2>&1 | tee -a logs/pumptape.log

python3 pumpcausal.py --json data/pumpcausal.json   # decomposition, causal rules, walk-forward
python3 pumporacle.py --json data/pumporacle.json   # oracle bound, latency grid, base rates
python3 pumpselect.py --json data/pumpselect.json   # entry screen: oracle bound + placebo + WF
python3 pumpwallet.py --json data/pumpwallet.json   # who actually made money

# the headline verdict alone
python3 pumpcausal.py --json /tmp/pc.json 2>&1 | sed -n '/DECOMPOSITION/,/rule A/p'
```

Saved output for this run: `logs/full_causal.txt`, `logs/full_oracle.txt`, `logs/full_select.txt`,
`logs/full_wallet.txt`.
