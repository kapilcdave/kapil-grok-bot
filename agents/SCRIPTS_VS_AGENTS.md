# Scripts vs agents

A pass is mostly a join of public data, and that join should be a script. The agent reads the row. This file only proposes the split.

Grok Bot keeps routing and alerts. Security, Smart Money, and Execution apply a fixed rule to the rows. Narrative keeps the two judgments at the bottom. PASS / CAUTION / FAIL and IN / OUT are those rules. Cutoffs stay unset here. The watched-wallet list stays with the team.

Code lives in `memecoin-dev/runners/`, `memecoin-dev/research/`, `memecoin-dev/devbot/`, or `crypto/dexarb/`.

## Scripts

| Job | Data source | Already in the repo |
| --- | --- | --- |
| Create-slot bundles | Solana RPC `getBlock` on the create slot. Pump.fun trades as a cross-check. | Research only: `research/createblock.py`, `dexarb/pumpbundleseat.py` (`fee_cluster`), `dexarb/pumpsameslot.py`, `research/cbrule.py`, `tapefeat.py`. `bundlenet.py` covers the first 10s and 60s. |
| Funder / hub vs the blocklist | Solana RPC. Native SOL into the deployer and the bundle wallets before create. | Trace only: `dexarb/pumpfunding.py`, `pumpfunding_batch.py` (drops under 0.005 SOL), `dexarb/PUMPGRAPH.md`. `runners/features.py` `launch()` stores `devMints` and `devMigrations`. No blocklist. |
| Same-slot migration | Solana RPC. Create slot versus the migrate slot. | `features.py` `launch()` stores `grad_lag_s` (same second, not same slot). `dexarb/pumpfundergradexit.py` is the delay after the curve fills. |
| Dev buy and sell timing | Solana RPC. The dev wallet around create. | Yes. `features.py` `dev_buy()` (create, then sold or moved within 10 min). Tape replays: `research/devpnl.py`, `devrisk.py`. |
| Real SOL vs quoted market cap | RPC `real_sol_reserves` or pool SOL. DexScreener or GeckoTerminal for the cap. | Pieces, no ratio. `devbot/pump.py` `curve_real_sol`. `features.py` `candles()`. `dexarb/pumpswapmint.py` checks a PumpSwap SOL quote. |
| Tracked / KOL buys still held | RPC swap plus current balance, joined to the watched list. | No join. `dexarb/pumpwallet.py` is a profit census. `memecoin-dev/research_wallets.py` is this team's own wallets. |
| Buy vs sell over $50 | Pump.fun trades or RPC swaps. | No $50 cutoff. `features.py` `flow()` (Jupiter volume). `research/postbuyers.py` (buy vs sell SOL, buys of at least 1 SOL). `research/chaintrades.py` (side and SOL per trade). |
| Median trade size | Pump.fun trades or RPC swaps. | Old tapes. `dexarb/pumporacle.py` (`trades_p50`). `chaintrades.py` has the sizes. |
| SOL fees paid | RPC meta: `fee` plus native transfers. | A sample. `features.py` `fees()` (last 25 txs). `research/createblock.py` on the create block. |
| Priority fee and who paid it | RPC fee payer and compute price. | Yes. `features.py` `_tx_fees()` names the payer. `fee_cluster` groups one shared birth-slot fee. |
| Holder concentration | GeckoTerminal token info. RPC `getTokenLargestAccounts` when it answers. | Yes. `runners/enrich.py` `holders()` (count, top 10, next band). `flow()` has Jupiter's top-holder share. Public RPC 429'd on largest-accounts (2026-10-21). |
| Dust airdrops | RPC: token balance up, no swap. | Funding dust only. `pumpfunding.py` (SOL under 0.005). `dev_buy()` `dev_moved_10m`. `postbuyers.py` (three address prefixes). No holder-airdrop filter. |

`holders()` also returns mint and freeze authority. Nothing reads LP lock. `devbot/launchpads.py` sets a lock on this team's own launches.

The scan is the same kind of script: pump.fun, about $100k to $300k, rising, a name denylist, a pool with liquidity. `runners/sources.py` reads both screeners, and `cbrule.py` has a brand-name set. Wash and micro-buys come from the flow and median rows. The paper book is arithmetic: 5 SOL, size written down first, then the first of +100%, −40%, or 10 minutes.

## Judgment

- **Is the catalyst real.** Scripts can supply the post time (`features.py` `post_time()`), the post text (`runners/enrich.py` `story()`), and same-name coins (`siblings()`). Narrative decides whether the post is about this coin, from an account that matters, and from before launch.
- **Is the attention organic.** `runners/sources.py` `dex_profiles()` lists current profiles and boosts and does not store when one was paid for. `flow()` carries Jupiter's organic label. Narrative separates paid callers and signal bots from people talking about the coin.

Narrative writes the word, the score, and the upgrade and downgrade conditions.
