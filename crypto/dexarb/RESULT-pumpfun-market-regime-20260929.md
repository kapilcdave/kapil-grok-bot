# RESULT — market-wide regime gate: CLOSED

Preregistration: `preregistrations/pumpfun_market_regime_20260929.md`  
Code: `pumpregime.py`  
Output: `data/pumpregime.json`

The market-wide residual named in `FINDINGS_pumpfun_markov.md` does not rescue a reachable entry.
Across 6,190 eligible mints, the train-selected cell was the highest quintile of the preceding 20
resolved trades' win rate. It improved the train loss to −1.20%, then lost **−12.99%** on 801 test
mints, worse than the test pooled **−11.87%**. Its 95% interval was [−14.55%, −11.41%].

The full-procedure shuffle null was also decisive: 92% of shuffled selectors scored at least as well
as the observed test result. The preregistered gate fails every profitability condition. Aggregate
mint rate, all-curve flow, and recent cross-curve outcomes do not open the `L_in=2` strategy.

This closes the remaining curve-local-versus-market-wide filtering objection on the archived state.

