# RESULT — reserve state is not proof of birth; 13.2% of audited “virgin” curves traded earlier

Code: `pumpbirthaudit.py`; self-tests: `test_pumpbirthaudit.py`  
Pilot output: `data/pumpbirthaudit_summary.json`

## Finding

The historical frame called a curve “virgin” when its first observed
TradeEvent had an initial-looking reserve state. That is not a valid birth
test.

A deterministic, window-stratified RPC audit checked 160 inferred births. For
each mint it fetched the first observed transaction and up to 20 successful
older mint transactions, decoding official pump.fun CreateEvent and TradeEvent
CPI payloads.

| classification | count |
|---|---:|
| CreateEvent in first observed trade transaction | 136 |
| CreateEvent precedes first observed trade | 2 |
| **prior TradeEvent proves it was already trading** | **21** |
| unresolved within scan | 1 |

Among 159 resolved candidates, the false-birth rate is **13.2%**. It is not
only a window-edge problem: the rate is **15.1%** inside the first 20 slots and
**12.3%** in the interior.

## Creator identity

The CreateEvent supplies the actual `creator`, rather than inferring the
deployer from the first TradeEvent's `user`. Among 138 decoded true births,
those addresses match 98.6% of the time. The old proxy was usually right, but
the CreateEvent is the authoritative field and will be used from now on.

## Consequence

Every pump.fun result based on the state-inferred virgin frame requires a rerun
against CreateEvent-verified births. This includes the same-slot strategy,
wallet persistence, buyer breadth, joint-feature bound, market regime and the
new deployer-history test. No capital conclusion survives this audit by
assumption.

The preliminary direction is favorable rather than adverse: among the audited
rows with an eligible 0.05 SOL same-slot simulation, verified births averaged
**+11.26%**, versus **−0.69%** for false births. That comparison is descriptive
and selected; only the full corrected rerun counts.

## Full correction now running

`pumpbirthaudit.py --all` is resumable and is auditing all 9,290 inferred
candidates against finalized Solana history. The corrected analysis will:

1. retain only candidates with a decoded CreateEvent;
2. use CreateEvent `creator` as deployer identity;
3. preserve the existing causal entry, cost and exit rules;
4. rerun every gate without changing thresholds after seeing corrected labels.
