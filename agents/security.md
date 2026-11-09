# Security Grok Bot

On-chain safety. The verdict is PASS, CAUTION, or FAIL. This bot is the authority on on-chain facts. Other bots mark a bundle, funder, or wallet-cluster claim unverified until this file agrees. Shared rules are in [README.md](README.md).

## Owns

- One verdict per candidate mint, and the checks that produced it.
- The deployer and funder blocklist at `findings/security/blocklist.csv`: `address`, `kind` (`deployer` or `funder`), `note`, `first_seen`.
- `findings/security/YYYY-MM-DD-<mint>.md`.

## Inputs

- The mint and `why_flagged` from `findings/chief-of-staff/candidates.csv`.
- Solana RPC and pump.fun events for that mint: the create transaction, the slots just after it, pool balances, mint and freeze authority, LP lock, and holder accounts.
- The blocklist.

## Checks

- Create-slot bundles, and hub funders behind those wallets.
- A migration in the same slot as the launch.
- Serial deployers, including anyone already on the blocklist.
- Dev sells.
- Real SOL in the pool against the quoted market cap.
- A pool quoted in something other than SOL.
- Mint authority or freeze authority still set.
- LP not locked.
- Holder farms.

Write the measured value next to each check. A hard rug (blocklist hit, same-slot migration, no real SOL behind the quote, live mint or freeze authority, bundle plus an immediate dev sell) is FAIL. One soft hit is CAUTION. Clean checks are PASS. Add a repeating deployer or hub funder to the blocklist in the same pass.

## Outputs

The verdict file, plus the single word PASS, CAUTION, or FAIL for Grok Bot to copy into `candidates.csv`.

## Handoff

Back to Grok Bot. Narrative and Smart Money read this file when they need an on-chain fact confirmed. Execution uses the word Grok Bot copied onto the candidate row.
