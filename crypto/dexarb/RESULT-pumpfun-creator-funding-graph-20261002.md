# RESULT — verified Pump.fun creator/funding graph

Date: 2026-10-02

## Capital verdict: `NO-GO`

The implemented bot remains capital-disabled. This report can authorize only a shadow allowlist; it cannot authorize transaction submission.

## Live shadow verdict: `FAIL-CLOSED-NO-ALLOWLIST`

## Birth-frame correction

- Audited inferred candidates: **9,290**
- Resolved candidates: **9,251**
- Earlier trades proving false birth: **779**
- False-birth rate among resolved: **+8.42%**
- Authoritative creators decoded: **8,472**
- Creator equals first TradeEvent user: **+97.56%**

## Verified same-slot follower base rate

- Eligible verified launches: **7,184**
- Mean return: **+11.40%**
- Window-clustered 95% CI: **[+9.06%, +13.79%]**
- Median return: **-2.44%**
- Win share: **+28.63%**

## Direct creator history

- Verdict: **`DEPLOYER-HISTORY-PERSISTS`**
- Top-history B launches: **134**
- Top-history B mean: **+31.31%**
- Rank rho: **0.480**
- Rank permutation p: **0.001**
- Selection-null p: **0.043**

## Entry-latency reachability

- Verdict at the preregistered five-slot retail floor: **`LATENCY-GATED`**
- Magnitude verdict: **`TOO-SMALL-TO-TRADE`**
- Funding graph justified by latency test: **False**

## Funding extraction

- Verified births scanned: **8,472**
- Direct transfer edge coverage: **+66.36%**
- High-confidence signer-source coverage: **+47.29%**
- Minimum qualifying direct transfer: **0.005 SOL**
- Dust sources skipped: **4,305**
- Repeated primary funders: **604**

## Funder-linked tests

- Persistence verdict: **`FUNDER-HISTORY-PERSISTS`**
- Top-funder B launches: **171**
- Top-funder B mean: **+75.63%**
- Five-slot persistence verdict: **`NO-FUNDER-HISTORY-PERSISTENCE`**
- Five-slot top-funder B launches: **81**
- Five-slot top-funder B mean: **+2.97%**
- Coordinated-burst flagged B launches: **295**
- Coordinated-burst useful exclusion: **False**

## Graph-gated out-of-sample paper bankroll replay

| assumed roundtrip tip | B trades | final SOL | return | max drawdown |
|---:|---:|---:|---:|---:|
| 0 SOL | 0 | 1.0000 | +0.00% | +0.00% |
| 0.001 SOL | 0 | 1.0000 | +0.00% | +0.00% |

These replays enforce the same fail-closed graph authorization as the shadow exporter. A failed graph gate therefore produces zero trades, not a replay of the unfiltered launch population.

## Live safety state

- Exported allow rows: **0**
- Score source: **none**
- Score key: **none**
- Requires live funding lookup: **False**
- Funding lookup verdict: **LOOKUP-NOT-REACHABLE**
- Live funding lookups sampled: **8**
- Completed inside feature deadline: **+0.00%**
- Unknown, stale and same-slot-generated scores fail closed.
- Capital enabled: **false**
- Transaction build/sign/submit path in shadow gate: **absent**
