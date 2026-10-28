# PREREG: does OUTSIDE buying rise with a launch's own self-buy, once the bundle is netted out?

Frozen 2026-10-17, before computing any statistic on the corpus. Motivated by
`RESULT-alien-pepe-20261015.md`: on the launch held up as the target, **5 of the 11 early "buyers"
were the creator's own wallets** (verified by shared funder/sweep target), and real outside buying in
the first 10 s was 4.57 SOL against 13.71 SOL of self-buy. Every dose–response number we hold
(`our-launch-history-and-live-sniper-yield`: mean broad-sniper SOL 0.05 at a 0.3–0.8 dev buy,
0.38–0.60 above 0.8) counted bundle wallets as buyers. This test re-asks the question with them removed.

## AMENDMENT 1 (2026-10-17, before any dose–response statistic was computed)

**Disclosure of what had been seen when this was written:** the eligible-launch count, the wallet-class
histogram, one launch's raw event list (inspected because `net_curve60` came back at −18.17 SOL, which
is impossible on a bonding curve), and a corpus-wide count of trades violating the constant-product
invariant. **No dose–response, bucket mean, or outcome statistic had been computed.**

Eligibility rule 1 below (`v_sol == 30 and v_tok == 1.073e9` on the create) **does not work** and is
replaced. It is the exact failure `pumpfun-tapes-are-40pct-mayhem-curves` records — "pre_vt check lets
them in; use the vs·vt invariant". A Mayhem curve's *create* reports the standard pair, so the create
cannot discriminate; its *trades* then violate `v_sol · v_tok = 30 × 1.073e9`, e.g. a 0.0142 SOL buy
moving `v_sol` 30.06 → 37.24.

Corpus-wide: **1,860,442 of 6,040,410 trades (30.8%) violate the invariant.** The violation is a
property of the trader, not of noise — `BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s` (the Mayhem
agent) accounts for 1,211,959 of them, 100% of its own rows and **20% of the whole tape**, and the next
six top violators are likewise 100% violating. The generalist bots that appear in the Alien Pepe tape
(`ssssswdk4RR8…`, `sssssDdMNAWKing…`) have **zero** violations.

Replacement rules:

1a. A trade row counts only if `|v_sol · v_tok / (30 × 1.073e9) − 1| ≤ 1e-6`. Violating rows are
    excluded from every sum and every wallet set.
1b. Wallet classes (below) are computed over invariant-OK rows only, so "distinct mints traded" means
    real-curve activity.
1c. Each launch records `n_trades` and `n_bad` in its window; the headline is reported both on
    launches with `n_bad == 0` and on all launches with bad rows dropped. **If those two disagree, the
    result is reported as unresolved.**

This also disposes of a contamination that would have hit the primary dependent directly: the Mayhem
agent is `broad` by mint count, so its self-churn would have been counted as *outside money*.

## Corpus

`research/data/tape-20261010T225715Z.jsonl.gz` + `tape-20261011T225728Z.jsonl.gz` (`snipefeed-2`:
`create`/`trade` events with `mint`, `creator`, `user`, `side`, `sol`, `tok`, `v_sol`, `v_tok`, `slot`,
`ts`, `recv`). ~3.0M events and ~41k creates a day. The bundled dev buy appears as a `trade` sharing
the create's `sig`, so it is observable.

## Eligibility (declared before measurement)

1. `v_sol == 30.0 and v_tok == 1_073_000_000.0` on the create — the standard curve. 83.9% of day-1
   creates; this is the Mayhem/non-standard guard (`pumpfun-tapes-are-40pct-mayhem-curves`).
2. The window `[t_create, t_create + 60 s]` contains **no feed gap > 3 s** and lies wholly inside the
   tape. Day 1 has 8 such gaps, all ~64 s. A gapped window under-counts outside flow, which is the
   dependent variable.
3. No filter on volume, buyer count, name, or outcome. Selection is on the **gate** (self-buy size)
   only — `a-breadth-sample-must-be-selected-on-the-gate`, `an-eligibility-filter-can-be-outcome-selection`.

## Wallet classes, computed over BOTH tapes before any launch is scored

By distinct mints a wallet ever traded in the corpus:

- **broad** — ≥ 3 distinct mints. A generalist bot/trader. This is the outside-money class.
- **single** — exactly 1 distinct mint. Bundle/insider proxy.
- **mid** — exactly 2. **Left unclassified and reported separately**; never folded into either side.

## Measures, per launch, over the first 10 s (and 60 s as a secondary)

- `self_sol` — buy SOL from the create's `user`, its `creator`, or any **single**-class wallet.
- `outside_sol` — buy SOL from **broad** wallets, excluding `user`/`creator`. **Primary dependent.**
- `naive_buyers`, `naive_sol` — every non-creator buying wallet, the contaminated statistic.
- `clean_buyers` — distinct broad buying wallets.
- `net_curve` — `v_sol − 30` at the last trade in the window: net SOL actually left on the curve.
- `swarm` — ≥ 5 distinct broad wallets each buying ≥ 0.3 SOL within 60 s.

## Primary test

Bin launches by `self_sol`: `{0, (0,0.1], (0.1,0.5], (0.5,1], (1,2], (2,5], (5,10], >10}` — buckets
fixed now, not after seeing the histogram. Report per bucket: n, mean/median `outside_sol`,
`P(swarm)`, mean `net_curve`, and the bootstrap CI on the mean. Secondary: Spearman of `outside_sol`
on `self_sol` over all eligible launches.

**MDE is declared before reading.** With n per bucket and the observed SD, report the smallest
difference in mean `outside_sol` between the `self_sol == 0` bucket and each higher bucket that the
sample could resolve at 95%. A bucket whose CI contains zero difference is **underpowered, not flat**
(`a-null-is-only-a-kill-above-its-mde`).

## Inference rule, declared asymmetric

- **Flat or decreasing `outside_sol` in `self_sol` (above MDE) ⇒ KILL.** The operator chooses the
  dose, and chooses it when the narrative is hot, so any confound should bias this *upward*. Flat
  survives that bias and closes "a bigger self-buy buys outside flow" at this bankroll.
- **Rising ⇒ NOT a green light.** It is observational and confounded with narrative quality and
  wallet freshness. It would license one further test (within-creator, within-hour comparison), not a
  spend.

## Classifier validation (falsification arm, run regardless of the result)

The **single** class is a proxy and inherits two known traps: a wallet active in only one mint *in a
2-day window* may be a dormant generalist (`a-most-recent-n-sampling-cap-censors-lifetime`), and a
genuine one-shot human buyer is `single` by construction. So: sample 40 **single** and 40 **broad**
wallets, fetch their on-chain signature counts and ages. On Alien Pepe the operator's bundle wallets
had **14–24 signatures and ~0.3 h of age** while real outside bots had 1000+ signatures. Require the
`single` sample's median signature count to be an order of magnitude below the `broad` sample's. If it
is not, the classifier is weak and the primary result is reported as **unresolved**, not as a kill.

## Secondary, from the same pass: does the operator's dump beat holding?

For launches where `user`/`creator` sells inside 60 s, compare their realised average sell price with
the max price in the first 600 s. On Alien Pepe the six wallets averaged 4.58e-8 against a 1.59e-7
peak — 3.5× below. If that generalises, the ladder exit is leaving the trade early and the edge sits
in the second wave, which needs no bundle.

## What would make this test void

- `single`-class wallets failing the on-chain validation above.
- Fewer than ~200 eligible launches in any bucket used for a kill claim.
- Gap intervals overlapping more than ~20% of eligible windows (feed incompleteness becomes the
  dependent variable).
