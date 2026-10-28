# PREREG: what makes MORE bots buy our coin in the first seconds (2026-10-15)

Goal: raise bot/sniper arrival on a launch devbot controls. `RESULT-bot-levers-20261014.md` scored
creator history (first launch today = +4.1/+4.6pp broad-sniper arrival, the one replicated lever),
dev-buy SIZE, metadata, socials and coarse timing (US hours). This prereg scores five levers that
study did NOT touch. Written before any of the numbers below were computed.

Tapes: `data/tape-20261010T225715Z.jsonl.gz`, `data/tape-20261011T225728Z.jsonl.gz` (same two days).
Sample: standard-curve creates with meta ok, as in `bots.py`. The pump.fun Mayhem agent
(`BwWK17...`) is excluded from every buyer count and SOL total, and a coin it trades is flagged
`mayhem` (it drives broad snipers away: −6.7/−5.4pp) — primary arms are non-Mayhem.

Broad sniper = wallet appearing in the launch block of >= 50 distinct creators in that tape
(`blocksplit.py` convention; TH=20 reported as a robustness arm). One-creator block wallets are that
creator's own bundle and are NOT counted as draw.

## Outcomes (per launch)
1. `P(broad sniper in launch block)` — the arrival probability the prior study moved.
2. `n_block` — distinct non-creator, non-agent buyers in the create slot.
3. `n_30s` — distinct non-creator buyers in the first 30 s.
4. `log1p(bot SOL 30s)` — their buy SOL.
5. `dev NET` (same formula as `bots.py`) — whether the draw pays.

## Levers
- **H1 dev-buy STRUCTURE.** A dev buy in a SEPARATE transaction after the create draws more buyers
  than the same-size buy bundled in the create tx (`sig == create sig`), because bundle detectors
  treat a same-tx dev buy as rug risk while "dev bought" is a positive filter. Arms: bundled;
  separate same slot; separate 1-8 slots (~0.4-3.2 s); separate <= 60 s. Scored at a matched dev-buy
  size (0.1-2 SOL). For separate-tx arms the outcome window starts STRICTLY AFTER the dev-buy slot
  (buyers before it enter as a control), so arrivals cannot have caused the dev buy.
- **H2 launch LULL.** Fewer competing creates in the 60 s before ours => more arrivals, because a
  sniper fleet's per-minute capital and slot budget is finite. Features: creates in the previous 60 s,
  creates in the same slot (both log1p). Robustness: demean within creator x hour-of-day, so a lull
  effect cannot be a time-of-day effect.
- **H3 GAP gradient.** Hours since that creator's previous launch, bucketed (<1h, 1-3h, 3-8h, >8h,
  first in tape). Doses the one known lever; monotone in gap is the prediction.
- **H4 LAST COIN's visible outcome.** The creator's previous launch drawing a broad sniper / large
  bot SOL predicts arrival on the NEXT launch ("dev's last coin did X" is a common sniper filter).
  Pre-launch information, so valid within creator.
- **H5 COPY-TRADE channel size** (descriptive, not a dev lever). Followed wallet = a wallet whose
  buys are followed within 3 s by buys from >= 3 distinct other wallets on >= 20 distinct coins.
  Measure the extra non-creator SOL in the 30 s after a followed wallet buys, vs coins where the
  first-30 s buyers are all unfollowed. Tells us whether "be the wallet bots copy" is worth building.

## Rules
- Every causal arm is WITHIN creator (creators with >= 2 launches), cluster-robust SE by creator,
  as in `bots.py` / `policy_fe.py`.
- **Replicates** = same sign and |t| >= 2.5 on BOTH tapes. Anything else is unproven and must be
  labelled so, however large.
- A lever that raises arrivals but not dev NET is reported as an arrival lever only; NET is the
  decision variable and its bar is the same.
- Report the headroom too: P(broad sniper) overall and at the best cell, so the ceiling is visible.
- No cell with < 30 launches, and no cell with < 30 creators for a within-creator arm, is read.
