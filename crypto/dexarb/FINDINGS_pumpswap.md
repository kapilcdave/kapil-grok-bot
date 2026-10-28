# The post-graduation PumpSwap LP seat — CLOSED as ADVERSE, and the dominant term is unmeasurable

Prereg: `preregistrations/pumpswap_lp_seat_20260918.md`, written before any PumpSwap swap was
decoded. Successor to the bonding-curve work in `FINDINGS_pumpfun.md`, which named this as the one
adjacent branch it left genuinely open.

**Verdict: CLOSED as ADVERSE.** A full-range LP in a graduated PumpSwap pool loses
**−73.49%/day** on capital against its own rebalancing benchmark at f=20 bps, capital-weighted over
84 pools; the median pool loses 27.44%/day. A high-TVL subset does have a small *positive* excess
that survives an honest out-of-sample test (+0.2060%/day), and it still does not open the branch,
because the term that decides the seat's P&L is **299× larger than that excess and cannot be
measured from this data at all.**

## The screen, and why it did not close the branch

For a constant-product pool the fee/LVR ratio collapses to a pure property of the price path. With
`y = sqrt(k*p)`, `|dSOL| = V*|dlog p|/4`, so

```
Q/V = sum|r| / 4      sigma^2 = sum r^2      R = 8f*(Q/V)/sigma^2 = 2f * sum|r| / sum r^2
```

Total variation over quadratic variation. `R` is horizon-invariant (both terms scale linearly with
time), needs no annualisation, and `R > 1` iff the typical per-swap move is under `2f`. The identity
was checked against directly measured reserves rather than assumed: **median `Q/V` identity/direct
ratio 0.999** (p25 0.993, p75 1.000).

Measured over 330 s of chain, 93 pools clearing a 24-swap floor (872 excluded under the floor, 52
for a falling `k`, 1,464 duplicate events deduped):

| | f = 20 bps | f = 25 bps |
|---|---|---|
| volume-weighted R | **0.270** | 0.338 |
| median pool R | 0.335 | 0.419 |
| best pool R | 1250.5 | 1563.1 |

Per-swap `|dlog p|`: median 0.00%, p75 1.21%, p90 2.99%; **62.6%** of moves fall under the
`2f = 0.40%` bar. The pooled ratio is ~3.7× below 1, but the prereg's close rule required the *upper
bound across pool groups* to be below 1 by more than 3×, and the SOL-as-quote group came in at
**R = 1.580**. So the rule said PROCEED, and it proceeded.

## The fee gate passed, but only after the scan was fixed

The prereg made the LP fee share a gate: `f` multiplies `R` linearly, so it had to be read off the
chain, not assumed. The first scan found **no** constant fee-shaped field and would have reported
that the event carries no fee rate — but it only looked at 8-aligned offsets, and Anchor serialises
structs *packed*. That was a statement about the stride, not about the chain.

Scanning every offset finds `@80 = 5` in 90.8–96.5% of events, and the paired test confirms it is
real: the u64 at `@88` equals `amount@56 × 5/10000` in 2,170 of 2,397 events across a **7.3×10⁹**
span of sizes. With a proven amount field the remaining legs enumerate directly:

| leg | rate | fixed? | reading |
|---|---|---|---|
| `@72` | p10 **20.00** / p50 25.00 / p90 25.00 bps | varies (two tiers) | the LP share |
| `@88` | 5.00 bps | FIXED | protocol |
| `@376` | 2.50 bps | FIXED | creator |

Legs sum to 32.50 bps of the swap amount. **The LP's `f` is 20–25 bps on chain**, which is exactly
the `F_GRID = [0.0020, 0.0025]` the income analysis already swept. The gate passed and the
assumption was right.

## R is persistent, and useless

`R` ranks beautifully out of sample — Spearman **+0.902** between a pool's first-half and
second-half `R`. It is also worthless, and for a structural reason: `R > 1` is *exactly* `net > 0`,
so `R` is **sign-only**, and a sign carries no magnitude. For a pool nobody trades both terms go to
zero while their ratio explodes, so a persistent ranking on `R` is a persistent ranking of
**deadness**. Its top picks:

| R | TVL SOL | net %/day | fee SOL/day |
|---|---|---|---|
| 1250.5 | 495 | +0.0014% | **0.007** |
| 423.2 | 140 | +0.0040% | 0.006 |
| 250.1 | 5,337 | +0.0109% | 0.585 |

Ranked instead on net income in SOL/day — what an LP is actually paid — the top pool has
**R = 1.4**, 30,910 SOL of TVL and +67.16 SOL/day. A different set of pools entirely.

## The selectable excess is real and out of sample

Splitting the window, ranking on the first half's net and scoring on the second, at a TVL ≥ 1,000
SOL floor (41 pools; eligibility taken from the *first* half only, so the universe cannot peek at
the scoring half):

| top | REAL oos | oracle | placebo p50 | overlap | beats? |
|---|---|---|---|---|---|
| 10% | **+0.2060%/day** | +0.2086% | −26.27% | 75% | YES |
| 25% | +0.1574% | +0.1693% | −31.85% | 80% | YES |
| 50% | +0.1143% | +0.1114% | −32.78% | 95% | YES |

Baseline with no selection: **−33.06%/day**. Spearman on net: **+0.970**. The real selector lands
essentially on perfect foresight and beats the 95th percentile of 400 random selections of the same
size. Without the TVL floor every tier fails — a 5-SOL pool paying 4%/day is not a seat.

So the excess is positive, persistent, and reachable by a rule fixed in advance. That is the most
any selection test in this corpus can deliver. It still does not open the branch.

## What kills it: the excess is not the economics

`net` is measured against **continuous rebalancing**, so it is the LVR excess, not a return. The
exact decomposition is

```
LP(t1)/LP(t0) = sqrt(p1/p0) * exp(f*sum|r|/4)
```

and the `sqrt()` is the LP's cost of being long: it is concave, so *any* price move costs the LP
relative to holding, and a falling token costs it in SOL outright. Per day on capital, over 42 pools
above the TVL floor: fee income **+9.98%**, LVR **+40.88%**, price drift **+147.50%**. The drift
term is the largest by a wide margin, and on the 330 s window it is not identified — **90% of pools
drifted the same way**, so a bootstrap over pools was resampling one market-wide move 42 times. The
effective sample for a drift is the number of independent **time periods**, not pools.

Measured properly, over 15 consecutive 2.4 h periods spanning 36 h (795 distinct pools, 778 with a
WSOL leg), the drift does not resolve — it gets worse:

* survivors-only: **+12.33%/period**, CI [+11.20%, +13.46%], **15 of 15 periods positive**
* survival of a TVL ≥ 1,000 SOL pool across 2.4 h: **57%**
* survivorship-bracketed (vanished pools marked at the worst drift observed): **−2.25%/period**

The bracket spans **[−22.5%, +123.3%]/day**. The sign of the LP's dominant term is decided entirely
by how the 43% of pools that stop trading are marked. And the drift *cost* — half the log drift, by
the `sqrt()` — runs to **299.3× the +0.2060%/day selected excess.**

## The drift is not measurable from this data, at all

The survivors' +12.33% in 15 of 15 periods is too uniform to be a market fact, and the reason is
structural: **a pool can only be priced from trades.** Requiring trades inside a 12 s window
conditions the sample on the pool being *active* at that instant, and for a memecoin activity is not
independent of the price move. That hypothesis makes a falsifiable prediction — tighten the gate and
the measured drift must rise while survival falls. It does, monotonically:

| trades required | survivors drift / 2.4 h | survival | bracketed /day |
|---|---|---|---|
| 1 | +6.75% | 63% | **−313.47%** |
| 2 | +11.10% | 57% | −51.25% |
| 4 | +12.33% | 57% | −22.51% |
| 8 | +15.56% | 44% | +27.10% |
| 16 | **+21.92%** | 30% | **+52.31%** |

Every step raises the drift; the bracketed value spans **[−313.5%, +52.3%]/day** across the sweep
alone and changes sign inside it. **Every value of the LP's dominant term is reachable by choosing
the activity gate**, and the gate is an arbitrary analyst choice, not a market parameter. The
positive record is manufactured by conditioning on the pool still being traded.

This is not a wide confidence interval that more data would narrow. Fixing it needs pool state read
at a chosen slot independently of whether anyone traded — historical account state, which the public
RPC does not serve — so it is a different collection, not a longer one.

## Verdict against the committed rule

The prereg's REOPEN condition required realised LP P&L with a CI clearing zero on the positive side
out of sample. Realised P&L needs the drift; the drift is unmeasurable here, so REOPEN cannot be
satisfied by construction. The population number is significantly negative, which is the prereg's
**CLOSED as ADVERSE** branch: the fee/LVR balance is not the binding term — the directional exposure
is, exactly as §2.2 of the prereg warned when it noted `R` contains no directional term at all.

Capacity was already bounded before any of this: only **4.00%** of curves (372 of 9,290) ever reach
graduation, so the seat exists in 4% of mints regardless of what `R` says.

## Two decode corrections, both recorded rather than quietly patched

**The leg order was wrong for 51 of 93 pools.** `@40` and `@48` are the *base* and *quote* reserves
positionally, not token and SOL. `pumpswapmint.py` resolved the pool account layout
(`base_mint@43`, `quote_mint@75`) and found WSOL is the base mint in 51 pools, the quote mint in 34,
and **absent in 8** — those 8 are USDC- and PUMP-quoted and would have had their TVL wrong by ~1000×
in any income ranking. They are excluded and counted.

`R` survived the bug and not by luck: it depends only on the price *ratio*, and inverting a price
negates `log p` while leaving `|r|` and `r²` untouched. The headline statistic was structurally
incapable of seeing a corrupted input. The **volume weights** are not invariant, so the previously
published volume-weighted **R = 0.306 is void**; corrected on the resolved leg it is **0.270**
(f=20 bps) and **0.338** (f=25 bps). The verdict is unchanged — `R < 1` either way.

**A fake +114%/day drift** came from taking the pool universe from a window at the *end* of the
sample, which restricted every historical period to pools still trading 36 h later. The universe is
now rebuilt from each period's own snapshot.

## What this cannot say

It cannot price a **concentrated** liquidity position — PumpSwap is full-range and the LVR form used
here is the full-range one. It says nothing about the Raydium pools older graduates migrated to, nor
about holding a graduated token directionally, which is a different trade with a different sign. The
excess numbers annualise a ~165 s half-window, so read their **sign and ranking**, never the
magnitude. All of it is read-only against archived blocks: **nothing was signed and no key was ever
loaded.**

## Reproduce

```sh
cd crypto/dexarb

# the pooled screen (R), on the resolved leg
python3 pumpswapr.py --blocks 400

# the fee gate: unaligned byte scan + realisation test + every fee leg
python3 pumpswapfee.py --blocks 40

# resolve which leg is SOL, per pool, from the pool ACCOUNT
python3 pumpswapmint.py --head 448205742 --blocks 900

# income on capital, R-ranking vs income-ranking side by side
python3 pumpswapinc.py --head 448205742 --blocks 900

# selection: split-half, oracle, 400-draw placebo, TVL floors
python3 pumpswapnet.py --head 448205742 --blocks 900

# the three-benchmark decomposition + the common-factor diagnostic
python3 pumpswaplp.py --head 448205742 --blocks 900

# the drift over 15 periods, the survivorship bracket, and the decisive gate sweep
python3 pumpswapdrift.py --head 448211986 --snaps 16 --hours 36 --win 30 --sweep
```

Event collection is cached and resumable in `data/pumpswap_events.jsonl` (1,779 slots), so every
command above re-runs offline on the same blocks. Results: `data/pumpswap_{r,sel,mints,income,net,lp,drift,fee}.json`.
