# RESULT: the dev buy is a MARKER, not a lever — and at our size it has a negative median

Prereg + amendment: `PREREG-bundle-netted-dose-response-20261017.md`. Code: `research/bundlenet.py`
(two streaming passes), `bundlenet_analyse.py`, `bundlenet_affinity.py`, `bundlenet_within.py`,
`bundlenet_pnl.py`. Outputs: `out-bundlenet*.txt`. Corpus: `tape-20261010`/`tape-20261011`
(6,215,976 events, 48.0 h, 28 feed gaps), **71,321 eligible launches**, 50,847 on provably standard
curves, 40,003 of those where the creator bought.

## Verdict

| question | answer |
|---|---|
| Does outside buying rise with the creator's own buy? | **Yes**, cross-sectionally, and it survives both confound arms |
| Does a bigger buy *cause* a bigger payoff for a given operator? | **No.** Median per-creator slope **−0.0203 SOL per SOL** [−0.0224, −0.0169], 35.1% positive |
| Does the seat pay at a dose we can afford? | **No.** 0.4–0.7 SOL, one-launch creators, clean counterparties: median **−0.0092 SOL**, P(>0) **43.9%** |
| Were our two live launches unlucky? | **No** — they sit at the **2nd** and **52nd** percentile of that distribution |

## 1. The primary dose–response rises, and survives both confounds

Dependent is `outside_sol`: first-10 s buy SOL from **broad** wallets (≥3 distinct mints in the
corpus), creator excluded. Every bucket difference is resolved above its declared MDE.

| self-buy | n | outside_sol mean | median | P(swarm) |
|---|---|---|---|---|
| 0 | 10,579 | 0.300 | 0.000 | 0.026 |
| 0.5–1 | 6,900 | 4.699 | 0.938 | 0.296 |
| 2–5 | 8,466 | 11.583 | 7.764 | 0.623 |
| 5–10 | 1,722 | 12.775 | 6.747 | 0.606 |
| >10 | 2,164 | 8.476 | 2.367 | 0.445 |

Spearman ρ = +0.630. Both declared confound arms leave it standing:

- **C2, recycled bundle wallets** (a farm reusing wallets makes them `broad`, so they'd be counted as
  outside money). 1,701 insider (creator, wallet) pairs found by tape-only affinity — the analogue of
  the on-chain shared-funder test that caught the Alien Pepe bundle. Removing them takes out only
  **8.1%** of outside SOL; ρ stays +0.615.
- **C1, simultaneity/popularity.** Re-run with a *predetermined* exposure — the creator's buy in the
  **create block** — against broad buying in later slots only: the gradient attenuates ~30% but holds
  (ρ = +0.541, all buckets resolved).

But the **marginal ratio falls monotonically**: 0.89 SOL of create-block buy draws 3.20 SOL of later
outside buying (ratio 3.6), 3.63 draws 7.61 (2.1), 7.14 draws 8.82 (1.2), and 57.0 draws 5.97 (**0.10**).
And the median launch gets back *less* than it put in at **every** dose: median `outside − dev` is
−0.57 at 0.5–1, −0.46 at 2–5, −4.09 at 5–10, −85.0 above 10.

## 2. The decision variable: the creator's own round trip

Outside gross flow is not profit, and the tape contains the creator's own sells, so the actual decision
variable was free all along. Marked exactly to the curve — dumping `q` tokens returns
`v_sol − K/(v_tok + q)` — with this repo's fee calibration (pay 1.0125×, receive 0.9875×).

**Control (declared a-priori sign: negative).** Launches where the creator is the only trader for
600 s: n=10,707, mean **−0.141 SOL**, mean return **−2.126%**, P(>0) = **0.004**. A round trip with no
counterparty loses the fee, as it must.

Counterparties restricted to genuine broad wallets (no `single`/`mid`/insider money), **no exit
selection**:

| create-block dev buy | n | mean | median | P(>0) | p10 |
|---|---|---|---|---|---|
| 0.1–0.5 | 4,552 | +0.028 | −0.005 | 0.262 | −0.012 |
| 0.5–1 | 6,037 | +0.117 | **−0.015** | 0.432 | −0.025 |
| 1–2 | 3,650 | +0.236 | +0.038 | 0.553 | −0.049 |
| 2–5 | 5,201 | +1.007 | **+0.564** | 0.688 | −0.124 |
| 5–10 | 658 | +2.067 | +0.604 | 0.614 | −0.173 |
| >10 | 864 | −0.635 | **−2.125** | 0.144 | −2.125 |

The >10 bucket is a trap, not a prize: only **30.9%** of those creators ever exit inside 600 s.

## 3. Why it is a marker and not a lever

| estimate of P&L per SOL of create-block buy | value |
|---|---|
| pooled slope | **−0.0105** |
| creator fixed-effects slope | +0.1158 |
| **median per-creator slope** (≥5 launches, n=687) | **−0.0203** [−0.0224, −0.0169], 35.1% positive |
| paired hi(≥2) − lo(≤1) within creator (n=168) | mean +0.699 [+0.366, +1.061], **median +0.055, 52.4% positive** |

The mean-weighted arms are positive and the typical-operator arms are flat-to-negative — the cross
-sectional gradient is **which operators choose 2–5 SOL**, not what the SOL does. Consistent with
`first-on-a-narrative-is-worth-nothing-unless-you-pick-it`: the dose is a symptom of a launch that was
already going to work. Concentration agrees: the top 500 of 25,417 creators hold **57.2%** of all
positive P&L, and creators with ≥10 launches have a median of **+0.003 SOL per launch**.

## 4. Our own position, priced

0.4–0.7 SOL dose, one-launch creators (our situation), clean counterparties, n=1,620:
**median −0.0092 SOL, P(>0) 0.439, p10 −0.016, p90 +0.180.** Our live draws of −0.0234 and −0.0066 sit
at the **2.2nd** and **51.9th** percentile — the second one is the median outcome of this seat. The
corpus says our launches were not mistakes and not bad luck; they were the base rate.

To reach the only bucket with a positive median we would need **2–5 SOL of working capital per
launch** against a **0.6381 SOL** bankroll (3–8×), to buy an effect that the within-operator estimate
says is worth about **−0.02 SOL per SOL**. Per
`series-count-costs-rope-linearly-and-buys-sharpe-sublinearly`: too small to arm means drop it, don't
deposit.

## 5. Two estimator bugs caught, and one claim of mine withdrawn

- **Exit selection.** The first cut scored only launches where the dev fully exited (56.8%), which
  selects winners because an underwater dev just holds. It read the >10 bucket at **+6.90** mean; the
  unselected mark-to-market reads **+1.71**. (`a-closed-position-subsample-is-selection-on-behaviour`.)
- **A control that could not fail.** My first no-flow control returned **+5.08%** against an a-priori
  negative sign, because it zeroed only *broad* buying and let single/mid wallets move the price. The
  real control (`n_other600 == 0`) gives −2.126%.
  (`a-conditioned-cost-cheaper-than-unconditional-is-a-broken-statistic`.)
- **Mayhem guard.** Filtering on the create's `v_sol/v_tok` does **not** work — a Mayhem create reports
  the standard pair. 30.8% of corpus trades violate `v_sol·v_tok = 30 × 1.073e9`, and
  `BwWK17cb…de6s` alone is 1,211,959 of them (**20% of the whole tape**). Use the trade-level invariant.
- **Withdrawn:** my `RESULT-alien-pepe-20261015.md` suggestion that the operator's ladder exits too
  early and "the edge is in the second wave". Corpus-wide the dev's realised average sell price is
  **0.895 of the 600 s peak** (median; 49.0% exit above 90% of it). Alien Pepe's 0.29 was the tail, not
  the rule.
- **Softened:** "most 'buyers' on busy launches are the dev's own bundles"
  (`our-launch-history-and-live-sniper-yield`) is too strong as a general claim. Corpus-wide **20.5%**
  of early non-creator buy SOL is not broad-wallet money, and on launches with ≥2 SOL of self-buy
  **89%** of visible buyers are genuine broad wallets. Alien Pepe (5 of 11) was unusually heavy.
  The bias that *is* material: selling to one's own non-broad wallets inflated dev P&L by ~a third at
  the big dose (2–5 bucket median +0.841 → **+0.564** once counterparties are restricted).

## 6. Classifier validation (prereg arm, passed with a caveat)

40 `single` vs 40 `broad` wallets, sampled and checked on chain: median signature count **75 vs 746**,
share with ≥400 sigs **7.5% vs 55.0%** — the order-of-magnitude separation the prereg required. Caveat
worth recording: `single` wallets have a median age of **3,855 h**, i.e. they are *dormant*, not fresh.
So `single` is a low-activity class (consistent with `pumpfun-human-buyers-need-cross-day-return`'s
holder farms), **not** a bundle class. Bundle identification still needs the on-chain
funder/sweep test. This is why the headline uses the create-block exposure, not `self_sol`.
