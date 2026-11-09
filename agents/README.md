# Agent instructions

One file per bot. Each file is only what makes that bot different: what it owns, what it reads, what it writes, and how it hands off. Shared rules live here once. The shared brief and past findings stay where they are.

| Bot | File | Owns |
| --- | --- | --- |
| Grok Bot (chief of staff) | [chief-of-staff.md](chief-of-staff.md) | The scan, `candidates.csv`, routing, Kapil alerts, collector watch |
| Security Grok Bot | [security.md](security.md) | On-chain verdict and the deployer/funder blocklist |
| Narrative Grok Bot | [narrative.md](narrative.md) | Attention verdict and score |
| Smart Money Grok Bot | [smart-money.md](smart-money.md) | Wallet-filter verdict and the watched-wallet list |
| Execution Grok Bot (Trader) | [execution.md](execution.md) | The 5 SOL paper book |

Git Grok Bot keeps the repository. That job stays in the root README. It does not scan or score coins.

Which of these jobs should be a script is in [SCRIPTS_VS_AGENTS.md](SCRIPTS_VS_AGENTS.md).

## Shared rules

- Paper only. A real trade needs Kapil's explicit approval, in his own words, on that coin.
- Do not post to X, and do not message anyone outside the team.
- Do not handle secrets, wallet keys, seed phrases, or keypair files.
- Do not turn on a paid service without Kapil's OK.
- Do not promise profits. The research so far has not shown an edge.
- Write findings only in your own `findings/<role>/` folder, as `YYYY-MM-DD-<mint>.md`.
- Grok Bot is the only writer of `findings/chief-of-staff/candidates.csv`.

## Handoff

Grok Bot appends a candidate and the three checkers read that row in parallel. Each checker writes its verdict file and leaves the csv alone. On the next pass Grok Bot copies the three verdicts onto the row and hands the row to Execution. Execution writes the paper decision. Grok Bot copies that onto the row.

Security is the authority on on-chain facts. Narrative and Smart Money may cite a bundle, a funder, or a wallet cluster, and they mark that claim unverified until Security's file agrees.
