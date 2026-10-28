# Pump.fun creator/funding graph research pipeline

## Safety contract

- Archive and funding collectors are read-only JSON-RPC clients.
- `pumpgraphshadow.py` has no RPC client, keypair, transaction builder, signer
  or submission path.
- `pumpfundingshadow.py` has a read-only history RPC client but no keypair,
  transaction builder, signer or submission path. A positive lookup that
  misses the declared deadline is rejected.
- Unknown, stale and noncausal scores fail closed.
- `pumpgraphexport.py` has no force flag. It emits live allow rows only when a
  CreateEvent-verified out-of-sample research verdict passes all gates again at
  the five-slot retail entry floor.
- Funder-keyed rows additionally require at least 100 live lookup attempts with
  at least 95% completing inside the two-second feature deadline.
- Paper compounding models overlapping positions and unavailable cash; it does
  not multiply every return as if all launches were sequential, and it replays
  zero trades when the graph signal is not authorized.

## Pipeline

```text
archived TradeEvents
        |
        v
pumpbirthaudit_batch.py
  finalized RPC CreateEvent / prior-TradeEvent verification
        |
        +--> pumpsameentry.py --birth-audit
        +--> pumpdeployer.py --birth-audit
        |
        v
pumpfunding_batch.py
  direct native-SOL funding edges before creation; signer attribution;
  0.005 SOL anti-dust floor
        |
        +--> pumpfundingmerge.py
        |      fail-closed merge when collection is split across RPC shards
        |
        v
pumpfundinganalyze.py
  zero-slot and five-slot funder persistence +
  causal coordinated-funding burst test
        |
        v
pumpfundinglookupreport.py
  live feature-extraction deadline and reliability gate
        |
        v
pumpgraphexport.py
  passed verdict only; creator- or funder-keyed frozen score file
        |
        +--> pumpgraphshadow.py
        |      direct live creator join
        |
        +--> pumpfundingshadow.py
               resolve the new creator's pre-creation signer funder,
               enforce lookup deadline, then join the funder score
```

Run the resumable sequence:

```bash
./run_pumpgraph_overnight.sh
```

For the current full-corpus collection, the funding stage is split across two
independent shards. Initial history collection used both configured and public
RPC capacity; terminal transaction scans used the configured endpoint. The
fail-closed supervisor resumes both shards, batches only transaction reads,
merges only complete clean terminal rows, runs every downstream report/test,
and restores the unrelated `swinghist` collector afterward:

```bash
./run_pumpgraph_sharded_finish.sh
```

## Data-validity correction

Reserve state is not accepted as proof that a curve was just created. The
initial 160-mint audit found 21 earlier TradeEvents among 159 resolved
state-inferred births, a 13.2% false-birth rate. All strategy and signal results
are therefore rerun only on decoded CreateEvents.

## Causal features

Available at the first verified TradeEvent:

- authoritative CreateEvent creator;
- creator's launches and outcomes strictly before the current slot;
- finalized direct SOL funding before creation;
- prior launches linked to the same signer funding source;
- prior/current same-funder launch bursts.

Not available for immediate entry:

- buyers who arrive later in the slot;
- first-N-buyer clusters not already present;
- holder growth, churn or later price action.

Those belong to separate exit or later-entry experiments.

## Live validation

An earlier creator-only capture decoded 538 TradeEvents, six CreateEvents and
six verified births from 5,000 records; its shadow smoke test joined one
synthetic causal score and rejected five unknown creators.

The funding-enrichment capture used for the final gate contains 3,000 live
WebSocket records, 881 decoded TradeEvents, nine CreateEvents and eight verified
birth pairs. Eight of the nine CreateEvents had `creator == user`. The
non-deadline funder shadow rejected four unknown funders, three missing direct
funders and one non-signer source. The strict two-second run made eight lookup
attempts and all eight failed closed on RPC lookup errors, so the preregistered
100-birth/95%-completion gate remains `LOOKUP-NOT-REACHABLE`. Every shadow
decision explicitly recorded capital, build, sign and submit as false.

Funder scores are never converted into stale historical creator allowlists.
The funder shadow resolves each newly observed creator's own pre-creation edge,
which preserves the wallet-rotation feature the funding graph is meant to
measure.

See:

- `RESULT-pumpfun-birth-validity-audit-20261002.md`
- `RESULT-pumpfun-deployer-persistence-20261002.md`
- `RESULT-pumpfun-creator-funding-graph-20261002.md`
- `RESULT-pumpfun-zero-slot-funder-reach-20261004.md`
- `RESULT-pumpfun-funder-independence-20261004.md`
- `RESULT-pumpfun-funder-walkforward-20261004.md`
- `../../memecoin/research/dexarb/RESULT-pumpfun-creator-graph-live-shadow-20261002.md`

## Pre-indexed zero-slot branch

The five-slot funding lookup failure does not require discovering the funder
after launch. `pumpfunderreach.py` freezes the zero-slot top funders on A and
scores the identical set on B after zero, one and two later same-mint
transactions, at the end of the birth slot, and at later slot floors.

`pumpfunderpreindex.py` stores qualifying outgoing signer transfers from those
funders in SQLite. At CreateEvent, the shadow gate performs one local query for
the creator's newest strictly pre-creation edge. `pumpfunderwatch.py` is the
read-only RPC polling adapter. The research score file is separate from the
production fail-closed score file:

```bash
python3 pumpfunderreach.py
python3 pumpfunderpreindex.py scores
python3 pumpfunderpreindex.py build \
  --db data/pumpfunderpreindex.sqlite \
  --scores data/pumpfunder_research_scores.jsonl
python3 pumpfunderpreindex.py benchmark \
  --db data/pumpfunderpreindex.sqlite \
  --scores data/pumpfunder_research_scores.jsonl
```

The archived branch is a shadow candidate only. End-of-birth-slot and later
slot entries are negative, so the unresolved gate is actual transaction
position after CreateEvent. None of these tools can build, sign, or submit a
transaction.
