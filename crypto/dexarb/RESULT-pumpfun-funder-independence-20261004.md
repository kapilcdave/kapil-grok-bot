# RESULT — the zero-slot funder edge survives wallet-rotation controls

Date: 2026-10-04

Preregistration:
`preregistrations/pumpfun_funder_independence_audit_20261004.md`

Code/output:

- `pumpfunderindependence.py`
- `data/pumpfunderindependence.json`

## Verdict: `FUNDER-ROTATION-INDEPENDENT`

The prior 171-launch result is not being created by repeatedly counting the
same known creator. Of those launches, there are 132 creators, 132
funder/creator pairs, and 131 distinct funding signatures. No creator or
funding signature contributes more than eight launches.

The strict primary subset keeps only the first B-period launch from creators
with no verified launch in A. It therefore tests the case where funder history
adds an identity signal before direct creator history exists.

| later same-mint transactions ahead | n | mean | window CI | funder-cluster CI |
|---:|---:|---:|---|---|
| 0 | 130 | **+100.92%** | **[+56.88%, +156.07%]** | **[+46.37%, +283.29%]** |
| 1 | 130 | **+38.38%** | **[+23.29%, +54.93%]** | **[+18.34%, +104.34%]** |
| 2 | 130 | **+15.26%** | **[+6.90%, +23.51%]** | **[+6.17%, +45.36%]** |

At one transaction ahead, top-minus-population is
**[+17.55, +48.06] percentage points**. The equal-funder-weighted mean is
**+50.16%**. Every frozen gate passes.

Only one of the original 171 selected launches came from a creator already
seen in A, and it lost 2.42%. The edge is almost entirely new-wallet activity,
which is exactly the funder-graph hypothesis.

## Cache invalidation correction

Watching only the selected funders is insufficient. Historical replay found
four false candidates where a creator had once received selected-funder SOL
but its newest qualifying funding source at a later launch was different.

Selected-edge-only cache replay:

- true positives: **171**
- false positives: **4**
- precision: **97.71%**
- recall: **100%**

The cache must therefore watch candidate recipients after a selected-funder
transfer and index any later qualifying signer-funded transfer. The local
index now supports these invalidating edges. With invalidators loaded, it
recovered the exact expected source and signature on 20,000/20,000 historical
selected launches; p99 lookup latency remained below one millisecond.

## What remains unresolved

The statistical selector now survives:

- chronological A/B separation;
- unseen-creator restriction;
- one launch per new creator;
- one launch per funding signature;
- funder-cluster bootstrap;
- one and two later-transaction ordering delays.

The remaining hard gate is live transport: continuously capture both selected
funder transfers and later candidate-recipient funding, then prove that joined
CreateEvents can target the first two later transaction positions. Public HTTP
RPC rate limits did not provide complete transfer capture.

Capital, transaction construction, signing, and submission remain disabled.

