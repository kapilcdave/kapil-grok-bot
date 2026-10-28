# RESULT — funder selection remains positive across three walk-forward folds

Date: 2026-10-04

Preregistration:
`preregistrations/pumpfun_funder_walkforward_20261004.md`

Code/output:

- `pumpfunderwalkforward.py`
- `data/pumpfunderwalkforward.json`

## Verdict: `FUNDER-WALKFORWARD-STABLE`

Each fold selected funders only from earlier zero-ahead outcomes, then scored
previously unseen creators' first test-period launch with one later same-mint
transaction ahead.

| training windows | test windows | selected n | mean | window CI | funder CI |
|---|---|---:|---:|---|---|
| 0–3 | 4–7 | 78 | **+34.90%** | **[+16.35%, +50.05%]** | [−1.71%, +50.33%] |
| 0–7 | 8–11 | 84 | **+29.94%** | **[+9.28%, +46.91%]** | [−0.52%, +102.27%] |
| 0–11 | 12–15 | 55 | **+50.38%** | **[+27.62%, +87.23%]** | **[+36.36%, +99.75%]** |

All three powered folds are positive and all three window-clustered intervals
exclude zero. The first two funder-cluster intervals cross zero, so those
folds do not independently establish broad cross-funder replication. The most
recent fold passes both window and funder clustering.

The selected set evolves rather than remaining permanent. Adjacent Jaccard
overlap is **0.579** and **0.567**. A live shadow should therefore maintain a
causal rolling score table; the original 19-address research list is not a
permanent allowlist.

This strengthens temporal validation but does not solve live transaction
landing or transfer-feed completeness. Capital remains disabled.

