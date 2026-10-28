# RESULT — CreateEvent-verified creator history persists out of sample

Date: 2026-10-02  
Preregistration: `preregistrations/pumpfun_deployer_persistence_20261002.md`  
Code/output: `pumpdeployer.py`, `data/pumpdeployer_verified.json`

## Verdict: `DEPLOYER-HISTORY-PERSISTS`

The full rerun used only RPC-decoded Pump.fun CreateEvents and keyed launches by
CreateEvent `creator`. Of 9,290 state-inferred candidates, 8,472 were verified
births; 7,184 had the full frozen outcome horizon.

Creators with at least three A-period launches were ranked using A only. The
frozen top and bottom quintiles were scored on B:

| B group | n | mean return | clustered 95% CI | median | win share |
|---|---:|---:|---:|---:|---:|
| top A-history quintile | 134 | +31.31% | [+19.47%, +70.01%] | +14.31% | 64.2% |
| bottom A-history quintile | 130 | −10.08% | [−15.05%, −8.03%] | −18.27% | 25.4% |
| all B launches | 3,782 | +9.68% | [+6.22%, +12.88%] | −2.44% | 29.0% |

All preregistered gates passed:

- power: 134 selected B launches across eight windows;
- positive selected mean and CI lower bound;
- top-minus-population and top-minus-bottom clustered lower bounds above zero;
- creator-level Spearman rho **0.480**, permutation **p=0.001**;
- observed top-minus-population **+21.63 pp**, above the selection-null p95
  **+20.55 pp**; one-sided **p=0.043**.

This proves creator history carries stable information in the archived corpus.
It does **not** prove the profitable fill is reachable. The separate verified
latency sweep is binding and returns `LATENCY-GATED`.
