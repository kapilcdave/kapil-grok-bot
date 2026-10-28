# PREREG: net the bundles out — does OUTSIDE flow rise with self-buy size? (2026-10-15)

Open question registered by `RESULT-alien-pepe-20261015.md`: the admired launch bought **34.5% of its
own supply (13.71 SOL) from six operator wallets** and drew only **4.57 SOL of genuinely outside
flow**. Every prior dose-response on self-buy size (`RESULT-bot-levers-20261014.md`,
`RESULT-bot-draw-20261015.md`) counted launch-block wallets as "buyers drawn" **without netting out
the operator's own bundle**, and the 10-14 study flagged the headline cell ("+0.8 SOL median at a >2
SOL dev buy") as *the bundle cell*. So the measured arrival gradient may be the operator counting
itself.

This decides whether multi-wallet bundling is a **draw** lever at all. Written before any number
below was computed.

Tapes: `data/tape-20261010T225715Z.jsonl.gz`, `data/tape-20261011T225728Z.jsonl.gz` (the same two days
as both prior studies, so a disagreement is attributable to the estimator, not the window).
Sample: standard-curve creates with meta ok, as in `bots.py`. Mayhem coins excluded (the agent drives
broad snipers away, −6.7/−5.4pp); the agent `BwWK17…` is never counted as a buyer.

## Dev side and wallet classification

Dev side = **creator wallet AND the create signer** (`RESULT-bot-draw-20261015.md`: signer != creator
on 16.8/20.0% of creates and it made the bundled dev buy on 21.0/23.7% of launches that had one).

Early-window buyer wallets are classified by `ncr` = number of **distinct creators** whose coins the
wallet bought in its early window, over the whole tape (`blocksplit.py` convention):

- `ncr == 1` → **bundle-suspect**: the operator's own wallet. One-creator block wallets were already
  excluded from "draw" in the 10-15 prereg; here they get measured instead of dropped.
- `ncr >= 50` → **broad outside**: a generalist sniper fleet. Primary outside definition.
- `2 <= ncr <= 49` → **ambiguous**, reported separately and never inside the headline. The 10-14
  study warned a ">= 3 distinct creators" rule is too loose because farms rotate creator keys, so
  the headline must not depend on this bucket.

Windows: **first 10 s** (`slot - create_slot <= 25`) is the Alien Pepe window; **10–60 s** is the
second wave, reported separately because that study's 4× arrived *after* the operator swept.

## Quantities per launch

- `self_sol` — dev-side buys in the first 10 s (the thing prior studies called "dev buy").
- `bundle_sol`, `n_bundle` — buys/wallets with `ncr == 1`.
- `total_self = self_sol + bundle_sol` — the Alien Pepe dose (13.71 SOL there, not 3.08).
- `out_sol`, `n_out` — buys/wallets with `ncr >= 50` (primary outside).
- `out_mid_sol` — `2 <= ncr <= 49` (robustness only).
- `out_sol_late` — primary outside flow in 10–60 s.

## Hypotheses

- **H1 (primary).** Outside SOL rises with **total** self-buy size. Test: within creator, regress
  `log1p(out_sol)` on `log1p(total_self)` with controls (creates in prior 60 s, same-slot creates,
  first-in-tape). Prediction if bundling is a draw lever: positive and replicating.
- **H2 (the decision).** The bundle adds outside flow **beyond the plain dev buy**. Test: same
  regression with `log1p(self_sol)` and `log1p(bundle_sol)` entered **separately**. The coefficient
  on `log1p(bundle_sol)` is the whole question — if it is ~0 or negative, the extra wallets buy
  inventory and a fake book, not ignition, and the seat is capital-gated with no payoff for the
  capital.
- **H3 (the knee).** Cell table of outside SOL by `total_self` bucket
  (0, ≤0.1, 0.1–0.5, 0.5–1, 1–2, 2–5, >5) to locate where outside flow stops responding, if it ever
  starts. A flat profile is a PASS for "drop it, don't part-fund it".
- **H4 (second wave).** Does a bundle buy *later* outside flow (10–60 s)? Same two-term regression on
  `log1p(out_sol_late)`.

## Classifier validation (must pass before any H is read)

The result is about a label, so the label gets audited first. A `ncr == 1` wallet could be ordinary
retail that happened to buy one coin, which would make `bundle_sol` meaningless.

1. **In-block share.** Bundle-suspect wallets should be in the create slot far more often than broad
   wallets (a bundle is same-block by construction).
2. **Single-coin share.** Report the share of `ncr == 1` wallets that bought exactly one coin in the
   whole tape, and their buy-size distribution against broad wallets. Retail one-offs are small and
   late; bundle wallets are large and in-block.
3. **Reproduce the known contamination.** The `self_sol > 2` cell must show a high bundle share —
   that is the cell the 10-14 study named. If it does not, this classifier is not finding what that
   study found and nothing here is read.

## Rules

- Every causal arm is **within creator** (creators with >= 2 launches), cluster-robust SE by creator.
- **Replicates** = same sign and `|t| >= 2.5` on **both** tapes. Anything else is unproven and is
  labelled so, however large.
- Robustness arm: demean within **creator × hour-of-day**, so a dose effect cannot be a time-of-day
  effect (`RESULT-bot-draw-20261015.md` found the clean-slot term only survives this control).
- No cell with < 30 launches, and no within-creator arm with < 30 creators, is read.
- `total_self` is measured in the same window as the outcome, so this is a **dose-response on
  observational data, not a causal estimate**: a confound that raises both would show as H1 positive.
  H2 is the protected comparison, because `self_sol` and `bundle_sol` share any such confound.
- A positive H1 with a null H2 means *size* draws flow but *extra wallets* do not — report it that
  way and do not call bundling a lever.

## Amendment A (added after H1-H4 were computed — EXPLORATORY, 2026-10-15)

The operator asked mid-run for two more levers. These were added **after** the H1-H4 numbers above
had been seen, so they are exploratory and carry no prereg protection; they are held to the same
two-tape replication bar and are labelled exploratory wherever reported.

- **H6 dev-buy TIMING.** `RESULT-bot-draw-20261015.md` declared this unscoreable from a tape (99.3%
  of dev buys ride in the create tx). Re-measured here: report the share riding in the create tx and
  the slot-offset distribution **before** reading any timing arm, because an arm with no variance
  cannot be read regardless of its t-stat.
- **H7 CHART SHAPE** ("make the chart look buyable"). Net SOL into the curve is the price, so the
  cumulative signed flow in the first 10 s is a price path; the outcome is outside flow in 10-60 s,
  so there is no lookahead. Features: drawdown from the 10 s peak, sell share, the dev's own sells,
  and whether there were any sells.
  - **H7 is expected to be reverse-caused and is reported as such**: "no sells in the first 10 s"
    mostly means *nobody bought*, so a dead coin scores as a clean chart. H7 is descriptive only.
  - **H7b is the decision version**: restrict to coins already ALIVE at +10 s (>= 1 outside buyer)
    and control for how alive (`log1p(out_sol)`), so the residual variation in the shape terms is
    the dev's own choice rather than the demand it proxies. The dev-controlled term is
    `log_dev_sell10` — does dumping early cost us the second wave? Alien Pepe's 4x arrived *after*
    its sweep, which predicts no penalty.
