# Runbook — pump.fun paper rig with GLiNER2.5-Decide as the text layer

Preregistration: `preregistrations/pumpfun_gliner_decide_paper_20261005.md`.
**Capital-disabled by construction.** No file in this rig holds a keypair, builds
a transaction or signs anything; `test_snipepaper.py` asserts that over the parsed
module rather than over its prose, because the docstring itself says "no signer".

## What it is for

The pump.fun fresh-launch family is closed in this tree and **two of the closures
are bounds**: Doob's optional stopping caps any price-path exit on the curve at
break-even, and the paying birth slot is authorship (0 of 33 bundled launches
admitted an outside transaction). So this rig is **not** an attempt at a working
sniper. It measures the one channel none of those closures touched: the token's
own **text**. The headline is G4 — does the model pick *which* rows, at matched
turnover — not the level of any arm, which is expected to be negative.

## Pieces

| file | role |
| --- | --- |
| `snipedecode.py` | pump.fun Anchor event decoding from `logsSubscribe` log lines, plus the constant-product quotes |
| `snipefeed.py` | one `logsSubscribe`, bounded watch set, writes a replayable tape |
| `snipepaper.py` | gate, decider call, paper fills and marks; same results live or on replay |
| `snipedecide.py` | the GLiNER2.5-Decide sidecar, behind a blocking degeneracy gate |
| `snipescore.py` | scores a ledger against the preregistered gates |
| `deploy_snipe_gcp.sh` | puts the feed+broker on IOWA and the sidecar on FRA |

## Feed facts, measured 2026-10-05

* **PumpPortal is not usable for trades.** `subscribeTokenTrade` answers *"only
  available when connecting with an API key funded with at least 0.02 SOL"*. Its
  free tier gives creates without trades, which cannot mark a curve.
* **Processed `logsSubscribe` on the pump.fun program is free and carries both.**
  1,445 `TradeEvent` and 18 `CreateEvent` in 25 s. `CreateEvent` is the only free
  source of name/symbol/uri, i.e. of the text channel itself.
* **A `Program data:` log line is not CPI-wrapped.** Its first 8 bytes are the
  event discriminator. `pumptape.py` reads inner instructions, where the payload
  *is* wrapped in `e445a52e51cb9a1d`; looking for that wrapper in logs finds zero
  events, which is what made the log path look dead.
* **~80% of pump.fun notifications are failed transactions** (24,356 of 30,477 in
  70 s). A failed tx never moved the curve and is dropped before decoding.
* **2.1% of `TradeEvent`s are zero-SOL legs** and are dropped: no price, so they
  can neither fill nor be marked. Checked on 1,900 live events — 40 zero-leg, **0
  short bodies, 0 layout drift**, so nothing real is being discarded.
* **Text availability: 90.9% of launches** return metadata through a 2-gateway
  fallback, **52.3% carry a non-empty description** (median 14.5 chars, p90 105).
  `pump.mypinata.cloud` serves in 235 ms, `ipfs.io` returns HTTP 429, and
  `cloudflare-ipfs.com` is retired (NXDOMAIN) so it is not in the list.
* **Gate pass rate is 0.57%**, measured on the live run (6 of 1,045 candidates),
  not the ~1.5% first estimated off a truncated census. At ~33 launches/min that
  is **~11 candidates/hour**, so G2's 300 rows takes **~27 h** and necessarily
  spans the feed's 24 h tape rotation. `snipescore.py --ledger` therefore takes
  several paths and dedupes across them on mint. Do NOT loosen the gate to go
  faster: it is preregistered, and fitting it on this tape makes every number
  downstream in-sample.
* **The feed reconnects often** -- 11 connects in the first ~30 min. The watch set
  lives outside the connection loop so it survives, and backoff is exponential,
  but a reconnect is a gap: check `connects` on the periodic stats rows before
  reading a rate off the tape.

## The friction floor

A round trip that exits immediately into an unmoved curve loses, on a fresh 30 SOL
curve: **−2.83%** at a 0.1 SOL clip, **−3.66%** at 0.25, **−8.08%** at 1.0,
**−26.31%** at 5.0. Two 100 bps legs are only 2% of that — the rest is **our own
impact, charged twice**. A 5 SOL clip is 17% of the pool, so sizing on this venue
is an impact problem, not a fee problem, and every arm pays this before the price
does anything at all.

## What to watch first, when the sample arrives

At n=6 the scorer already reports **D2 reducible: `desc_chars >= 73`, agreement
1.000**, i.e. the model's three `buy` verdicts were the three rows with a long
description (117/91/73 chars) and its three `skip` verdicts were the short ones
(33/0/0). At that n any 3/3 split is reproducible by some threshold, so it is not
yet evidence — but it is the **predicted** failure and the reason D2 is blocking.
If it survives to n=300, the readout is "the model detected whether text exists",
which is one line of Python and not a 486M-parameter decision layer.

## Operating

```sh
./deploy_snipe_gcp.sh                       # both boxes; refuses FRA if D1 fails
gcloud compute ssh solana-funder-shadow-fra --zone=europe-west3-b \
  --command='curl -s http://10.156.0.6:8137/health | python3 -m json.tool'
gcloud compute ssh instance-20260902-221655 --zone=us-central1-a \
  --command='systemctl is-active pumpsnipe-feed pumpsnipe-paper'
```

Score, from the box:

```sh
cd ~/pumpsnipe/app && ~/pumpsnipe/venv/bin/python snipescore.py \
  --ledger "$(ls -t ~/pumpsnipe/data/ledger-*.jsonl | head -1)" --json /tmp/report.json
```

## Traps already paid for, do not re-pay them

* **A degenerate head is a void instrument, not a conservative one.** D1 runs 12
  probe texts before the first request; a task emitting one label marks the server
  degenerate, `/decide` answers 503 and the ledger records
  `reject_decider_degenerate`. The deploy refuses to install the unit on a D1
  failure. Without this the arm produces a clean-looking null from nothing.
* **G3 rewards declining, not choosing.** A row an arm skips contributes 0.0, so
  against a negative-level arm any constant "skip" wins. The scorer caught this on
  a dummy ledger at **+27.67%** before a real row existed. G4 (real minus its own
  **rolled** accept vector, identical accept count, identical fee bill) is the
  headline; G3 is printed labelled as confounded.
* **Marks use the standing state**, the last print at or before the horizon. On a
  curve the standing state *is* the fill price. Taking the first print *after* the
  horizon — correct on an order book — returns nothing whenever a token goes
  quiet, which silently deletes exactly the stuck positions.
* **`ThreadPoolExecutor` workers are not daemons** and an atexit hook joins them,
  so one wedged gateway socket keeps the feed alive past its `--duration`. That
  happened here: a 420 s census ran until killed by hand. The pool is daemon
  threads with a bounded queue.
* **`--duration` is wall clock, not monotonic.** A monotonic budget spends *awake*
  seconds and silently overruns a box that slept.
* **IOWA runs a live trader** (`polymarket-us-mm`, ~376 MB) with ~250 MB free. The
  1.95 GB model cannot go there; the units carry `MemoryMax=180M` and `Nice=10` so
  this rig can never be the reason that agent is reaped.
