# Prereg: which dev-controlled launch choices bring HUMAN buyers to a pump.fun coin

Written 2026-10-13 ~10:00 UTC, before any outcome-by-lever number was computed. Only the
descriptive pass in `explore.py` (counts and marginal distributions, no outcomes split by
feature) had been run.

## Why
devbot's coins draw zero buyers from the old dev wallet and only launch-block bots from a fresh
one (`$gctrl`: 5 wallets in block 0, all out by 32 s). The operator's goal is human attention.
Every buy-side pump.fun study in the corpus is closed, but none measured the DEV side: which
choices a creator makes at launch go with human buyers arriving.

## Data
`snipefeed-2` tapes from the Iowa box (public logsSubscribe; each mint watched ~2 h; known gaps
every few minutes, assumed unrelated to the levers).
- **Primary day: 2026-10-11 tape** (`tape-20261011T225728Z`), analysed first.
- **Replication day: 2026-10-10 tape** (`tape-20261010T225715Z`), scored once with the frozen code.

Sample: launches with a `create` row, a successful `meta` row, and a STANDARD curve
(`v_sol * v_tok` within 1% of `30 * 1.073e9`); Mayhem curves are excluded.

## Human-like buyer (frozen)
A wallet buying the coin that is (a) not the creator, (b) whose first buy of this coin is at
least 2 s after the create (no launch-block snipers or bundles), and (c) that buys at most 10
distinct mints in the whole day's tape. Sensitivity (reported, not decisive): cap 30.

## Outcomes, window [create, create + 60 min]
- **PRIMARY H:** number of distinct human-like buyers.
- Secondary: P(H >= 5); non-creator volume V (buy + sell SOL, all wallets, the fee base);
  human buy SOL.

## Levers (all set by the creator at launch), fitted JOINTLY
1. X link kind: x_post / x_profile / none (reference) (x_community + other pooled as "other").
2. website present. 3. telegram present. 4. description present.
5. creator's earlier launches in the same tape before this one: 0 (reference) / 1-4 / 5+.
6. creator's dev buy (creator buys in the create transaction): 0 (reference) / (0, 1] / > 1 SOL.
7. launch tool by metadata host: ipfs.io (reference) / j7tracker / other tool.
8. hot copy: name/ticker shares a term (devbot `trends.terms`) with a coin launched in the
   previous 30 min that already had >= 10 human-like buyers BEFORE this launch.
9. name style: non-ASCII name; ALL-CAPS name; ticker equals name (normalised).
10. US hours: create between 13:00 and 22:59 UTC.

## Model and decision rule
OLS of log1p(H) on all levers, cluster-robust SE by creator. Repeated for P(H >= 5) as a
linear probability model. A lever is called **REAL** only if its log1p(H) coefficient has the
same sign and |t| >= 2.5 on BOTH days (2.5 for ~15 coefficients tested). Effects are reported as
multipliers on (1 + H). These are associations from other people's launches, not causal effects:
serious creators do many things at once. A lever that passes gets a causal test with devbot's own
launches next.

Not tested here, by choice: anything that fakes demand (wash or volume bots, bought followers,
undisclosed multi-wallet bundles). Those deceive the human buyers this is about.

## AMENDMENT A (2026-10-13 ~10:40 UTC): the human definition was measuring sybil farms. v1 verdict VOID.
After scoring v1 on both days, the top coins by "human" buyers (800-1,100 each) turned out to be
holder farms: their low-activity wallets return to buy anything on the adjacent day at 0.6-2.2%,
against 44-64% for mid-table coins and a 19.8% base rate (`sybil_check.py`). The v1 outcome was
therefore a mix of humans and single-use sybil wallets, and the v1 lever table is not used for any
decision. (Recorded, not deleted: `out-20261011.txt`, `out-20261010.txt`.)

**Human-like buyer v2 (frozen before re-scoring):** conditions (a) and (b) as before, (c) at most
10 distinct mints bought that day, AND (d) the wallet buys at least one coin in the OTHER tape
(Oct 10 <-> Oct 11). Everything else (outcomes, levers, model, the |t| >= 2.5 both-days rule) is
unchanged. Conservative by design: a real human who traded only one of the two days is not counted.
