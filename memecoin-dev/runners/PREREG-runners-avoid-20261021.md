# PREREG: avoid fresh launch-bought and brand coins (paper), 2026-10-21 ~23:30Z

Written after an in-sample exploration (`runners/explore.py`) of 186 admitted coins from 2026-10-21
(08:07-22:38Z, v0 and v1 tapes, 119 with a 2 h outcome). The filter is frozen here and scored ONLY
on coins admitted after it was deployed, using features recorded at admission (`enrich.features`).

## Operator thesis

High-market-cap "fake" coins: the dev buys in with a lot of SOL, the first candle is already at
$400k-$3M, and it rugs. Avoid them. Charity and Polymarket-linked stories and a linked X post are
among many features, not the rule by themselves.

## Filter (`runners/rule.avoid`, frozen)

AVOID a coin if it is less than 1 h old at admission (age from Jupiter `createdAt`) AND any of:
- `grad_lag_s == 0`: it graduated in the second it was created (the curve was bought out at launch);
- the dev wallet spent >= 10 SOL in the create transaction;
- Jev `mainstream` >= 0.5 (its subject is a big brand, person or story: TRUMP, OpenAI, NVIDIA, YouTube).

## In-sample (the reason for the filter, NOT evidence for it)

| hold | avoided | kept |
|---|---|---|
| 1 h | n=69, mean -53%, rug 71% | n=76, mean -9%, rug 13% |
| 2 h | n=49, mean -67%, rug 84% | n=70, mean -26%, rug 24% |
| 4 h | n=33, mean -90%, rug 91% | n=53, mean -24%, rug 26% |

Fresh + mainstream alone: n=20, mean -98%, rug 95%. Kimchi (the day's biggest runner) had the same
launch shape (87 SOL dev buy, graduated in 1 s, first candle $2.2M) but was admitted at 14 h old, so
the filter does not judge it. The kept group still LOSES on average: this filter is a necessary
cut, not a pick rule.

## Gates (forward, scored once at 150 coins with a 4 h exit or 14 days)

- **A1:** keep minus avoid, 4 h mean net, bootstrap 95% CI excludes 0 (> 0).
- **A2:** avoided rug rate (<= 20% of entry at 4 h, or delisted) at least double the kept rug rate.
- **A3:** both halves by time agree in sign on A1.

Passing makes `avoid` a precondition of any later pick rule. It does not make anything tradeable:
the kept group's own mean is not gated here.

## Not in this test

Organic-flow features (Jupiter organic buyers, organic buy share, holder growth) could not be
backfilled without reading them after the outcome; they are recorded at admission from 22:38Z and are
the candidates for the positive (pick) side in the next preregistration.
