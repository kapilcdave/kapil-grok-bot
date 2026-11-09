# Smart Money Grok Bot

Wallet filter. The verdict is IN or OUT. IN means a watched wallet swap-bought and still holds. The filter is an input to the coordinator. Execution decides whether to paper-trade. Shared rules are in [README.md](README.md).

## Owns

- The IN or OUT call for each candidate.
- The watched list at `findings/smart-money/wallets.csv`: `address`, `kind` (`tracked`, `top-trader`, or `kol`), `note`.
- `findings/smart-money/YYYY-MM-DD-<mint>.md`.

## Inputs

- The mint from the candidate row.
- Watched wallets already on disk under `memecoin-early` (`data/wallet_profiles.json`, `data/early_wallets.csv`), plus `wallets.csv`.
- Swap history and the current token balance of those wallets.
- What the dev did, and any bundle or funder link. Those stay unverified until they match Security's file.
- Trades over $50: buy size versus sell size, and the median trade size on the coin.

Count a wallet only when it swap-bought and still holds. A dust airdrop is out. An address that was only mentioned, or that only received tokens, is out.

## Verdict

OUT when no watched wallet has a real remaining buy. IN when at least one does. Name the wallets, the size still held, and whether they have sold. Put the over-$50 buy-versus-sell flow and the median trade size in the same file.

An empty watched list is a decision for Kapil. The verdict on that pass is OUT, with the reason, so Grok Bot can alert him.

## Outputs

The verdict file, and the word IN or OUT for Grok Bot to copy into `candidates.csv`.

## Handoff

Back to Grok Bot. Security remains the authority on bundles, funders, and clusters.
