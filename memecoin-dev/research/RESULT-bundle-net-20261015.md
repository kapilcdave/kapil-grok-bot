# RESULT: net the bundles out — bundling is not a draw lever (2026-10-15)

Prereg: `PREREG-bundle-net-20261015.md` (H6/H7 added mid-run at the operator's request and labelled
EXPLORATORY there). Code `bundlenet.py`, outputs `outbundlenet-1010.txt` / `outbundlenet-1011.txt`.
Tapes `data/tape-20261010T225715Z.jsonl.gz` (21,690 non-Mayhem launches) and
`data/tape-20261011T225728Z.jsonl.gz` (24,776) — the same two days as `RESULT-bot-levers-20261014.md`
and `RESULT-bot-draw-20261015.md`, so any disagreement is the estimator, not the window.

**Replicates** = same sign and `|t| >= 2.5` on **both** tapes.

## Classifier validation — PASSES

`ncr == 1` (bought in the early window of exactly one creator, tape-wide) really does find operator
wallets, not retail one-offs:

| | 10-10 | 10-11 |
|---|---|---|
| `ncr==1` wallets that bought exactly one coin all tape | 97.4% | 98.4% |
| in the create slot: `ncr==1` vs broad (`ncr>=50`) | 37.0% vs 17.3% | 39.1% vs 14.7% |
| median buy SOL: `ncr==1` vs broad | 0.81 vs 0.20 | 0.74 vs 0.22 |

Large, same-block, single-coin. Validation 1 and 2 pass on both days.

## ⛔ Validation 3 FAILS as the 10-14 study framed it — the contamination is in the SMALL cell

`RESULT-bot-levers-20261014.md` warned that "the +0.8 SOL median at a >2 SOL dev buy is the BUNDLE
cell". That does **not** reproduce, under either dev-side definition. Self-side share of all non-dev
SOL in the first 10 s, by dev-buy size:

| dev buy | 10-10 | 10-11 |
|---|---|---|
| 0 | 50.6% | 45.4% |
| **<= 0.1** | **71.0%** | **62.3%** |
| 0.1–0.5 | 32.0% | 38.8% |
| 0.5–1 | 36.3% | 27.9% |
| 1–2 | 11.3% | 16.7% |
| 2–5 | 5.8% | 4.6% |
| > 5 | 6.3% | 5.8% |

Bundling concentrates where the **dev buy is ~zero** (the operator self-buys from separate wallets
*instead of* a dev buy), not where it is large. At a >2 SOL dev buy the non-dev flow is 94–95%
genuinely outside. The 10-14 caveat was pointed at the wrong cell.

## ⚑ H1 — outside flow DOES rise with self-buy size. REPLICATES

Monotone on the **price-invariant** outcomes (counts, not SOL — a bundle lifts the curve, so a
SOL-denominated outcome alone cannot tell extra buyers from a higher price):

`P(any outside buyer in first 10 s)` by `total_self` (dev + bundle wallets):

| total_self | 0 | <=0.1 | 0.1–0.5 | 0.5–1 | 1–2 | 2–5 | >5 |
|---|---|---|---|---|---|---|---|
| 10-10 | 11.6% | 16.7% | 36.9% | 59.7% | 74.5% | 82.9% | 86.9% |
| 10-11 | 12.8% | 15.1% | 40.5% | 60.2% | 71.7% | 81.6% | 84.8% |

`log_total_self` on `log1p(n_out)`: +0.2948 (t +4.0) / +0.3886 (t +7.4). **The knee is 0.5–1 SOL** —
that step buys +20 to +23pp, the next two buy +12 to +15pp and +8 to +10pp, and above 2 SOL you pay
~3x the capital for +3 to +5pp.

## ⛔⛔ H2b — the SAME-BLOCK bundle is NOT the thing that draws. The headline was a LABEL ARTIFACT

Entering the `ncr==1` flow as one term (H2) looked like a win: `log_bundle_sol` +0.2357 (t +5.3) /
+0.3084 (t +3.1). Splitting it by **when** those wallets bought destroys it:

| term | 10-10 | 10-11 | replicates? |
|---|---|---|---|
| `log_self_sol` (the dev buy) | t +2.8 | t +5.0 | ✅ **yes** |
| `log_bundle_BLK` (same block — what you control by funding wallets) | **t +0.1** | t +3.1 | ❌ **days disagree** |
| `log_oneoff_1_10s` (`ncr==1` buying at +1–10 s) | t +6.2 | t +6.6 | ✅ yes — but these are TOURISTS |

The whole apparent bundle effect lived in `ncr==1` wallets buying at **+1 to +10 s**. Those are not
operator wallets at all — they are genuinely outside buyers that the one-creator rule miscounted.
Every prior dose-response on "bundle size" inherits this: the `ncr==1` set mixes the operator's
same-block wallets with one-off outside buyers, and only the latter carries signal.

**The directly matched test says the same thing.** At a *fixed* `total_self`, adding wallets does not
add buyers, and the 10–60 s second wave is **lower with 2+ extra wallets on 4 of 4 size bands, both
days**:

| total_self | late OUT SOL, 0 extra wallets | 1 | 2+ |
|---|---|---|---|
| 0.5–1 | 0.766 / 0.792 | 1.493 / 1.273 | **0.343 / 0.249** |
| 1–2 | 0.896 / 0.777 | 1.444 / 1.998 | **2.452 / 0.443** |
| 2–5 | 2.816 / 2.488 | 2.276 / 2.145 | **1.667 / 1.336** |
| >5 | 3.754 / 3.623 | 5.869 / 5.984 | **3.380 / 3.011** |

(10-10 / 10-11. The `2+` column also disagrees in sign between days in the 1–2 band — 2.452 vs
0.443 — so it is unproven even as a positive.)

⇒ **Alien Pepe's 13.71 SOL across six wallets bought no more outside flow than 13.71 SOL from one
wallet would have.** The prereg's disposition applies: outside flow is flat in wallet count, so the
seat is capital-gated with no payoff for the capital. Drop it, don't part-fund it.

## ⛔ H6 — dev-buy TIMING is not a lever, because there is no variance in it

| | 10-10 | 10-11 |
|---|---|---|
| dev buy rides in the create tx | 99.1% | 98.8% |
| slot offset p50 / p90 / p99 | 0 / 0 / 0 | 0 / 0 / 0 |
| separate same-slot cell, matched size | n=57 | n=85 |
| separate 1–8 slots | n=0 | n=4 |

This confirms `RESULT-bot-draw-20261015.md` ("not scoreable from a tape"). The two days' `t` on the
separate-same-slot dummy disagree by ~300x (−40.2 vs −1.2) on a cell of 57–85 launches out of
17–20k — that is a near-zero-variance dummy, not an effect. The 1–8 slot arms (n=0 / n=4) are below
the 30-launch floor and are **not read**. PumpPortal's create bundles the buy, and so does 99% of the
market; there is no timing decision to make.

## ⚑ H7b — chart shape predicts the second wave, but the part we CONTROL has no penalty

Conditioned on the coin being alive at +10 s (>= 1 outside buyer) with early demand controlled, at
matched `total_self` 0.1–2:

| cell | late OUT SOL mean | P(any late) | n_out_late |
|---|---|---|---|
| dev did NOT sell | 1.131 / 1.117 | 42.2% / 42.1% | 2.59 / 2.42 |
| **dev sold, drawdown <= 25%** | **3.565 / 3.429** | **73.4% / 81.7%** | **7.21 / 6.37** |
| dev sold, drawdown > 25% | 0.490 / 0.571 | 24.4% / 25.7% | 1.03 / 1.13 |

Replicating terms (both days, survives the creator × hour same-window control):

- `drawdown10` → **−2.0138 (t −9.6) / −2.4164 (t −13.4)** on `log1p(n_out_late)`. A chart that has
  already given back its gains at +10 s gets no second wave.
- `log_dev_sell10` → **+0.1532 (t +2.5) / +0.1970 (t +5.3)**, i.e. **positive**. Our own early
  selling does **not** cost the second wave.
- `log_total_self` collapses to ~0 (t +1.7 / +1.1) once shape and early demand are in.

**But two thirds of this is not a lever.** The drawdown at +10 s is mostly *other people's*
behaviour, and "dev did NOT sell" scoring worst is selection — a dev sells when there are buyers to
sell into. The one clean, action-relevant reading is the `log_dev_sell10` term: **a ladder exit at
+5–12 s costs nothing in later flow**, which is exactly why Alien Pepe's 4x arrived after its sweep.
It is a licence to exit, not a way to summon buyers. "Make the chart look buyable" is real as a
*predictor* and almost entirely unavailable as a *control*.

## What this changes

1. **Bundling is closed as a draw lever** on the dimension the operator cares about. Not refuted as
   a *price-manipulation* mechanism — it does buy inventory and a fake book — but it draws no extra
   outside buyers at matched size, and lowers the second wave on 4/4 bands.
2. **The dev buy remains the only replicated draw lever**, with the knee at **0.5–1 SOL**.
3. **Dev-buy timing is a non-question.**
4. ⚠ **This is a DRAW study, not an income study.** It says nothing against the standing finding
   that arrival does not pay: 2–3 SOL of bot buying is 0.006–0.009 SOL of creator fee, live
   cross-creator snipers bring ~0.06 SOL at a 0.3–0.8 dev buy, and our own live 0.5 SOL draw ($QUIET)
   got zero. Maximising the draw and making money are different objectives, and only the first is
   measured here.
