# kapil-grok-bot

Versioned home for the code, findings, and working notes produced by Kapil's team of AI assistants ("Grok Bots").

The team currently does Solana memecoin trading research. Later, Kapil will add agents in Iowa and Ohio that trade on Kalshi. Their work will live under `kalshi/`.

## Bot roster

| Bot | Role | Writes to |
| --- | --- | --- |
| Grok Bot | Chief of staff. Coordinates the team and keeps the research picture together. | `findings/chief-of-staff/` |
| Security Grok Bot | Token safety analysis. | `findings/security/` |
| Execution Grok Bot | Bankroll, position sizing, and trade execution. | `findings/execution/` |
| Narrative Grok Bot | Attention and narrative analysis. | `findings/narrative/` |
| Smart Money Grok Bot | Tracking top Solana wallets. | `findings/smart-money/` |
| Git Grok Bot | Repository and source-control keeper. Owns structure, commits, and pull requests. | Decisions and logs go in `notes/`. |

## Folder layout

```
.
├── code/            Scripts and tools
├── findings/        Research notes and reports, one folder per research bot
│   ├── chief-of-staff/
│   ├── security/
│   ├── execution/
│   ├── narrative/
│   └── smart-money/
├── notes/           General notes, decisions, and logs
├── site/            Reserved for an upcoming Vercel dashboard
└── kalshi/          Reserved for future Kalshi trading agents
```

## Conventions

- Name findings files with a date prefix: `YYYY-MM-DD-topic.md`. Example: `2026-10-28-new-token-cluster.md`.
- Each research bot writes in its own folder under `findings/`. Shared decisions, run logs, and anything that is not a research finding go in `notes/`.
- Scripts and reusable tools go in `code/`. Put a short comment at the top of each file saying what it does and which bot it belongs to.
- Leave `site/` as a placeholder until dashboard work is explicitly started. See `site/README.md`.
- Put Kalshi-specific work in `kalshi/` only.

## Secrets

Never commit secrets. That includes:

- `.env` files and any other environment files
- private keys, wallet seeds, and seed phrases
- Solana keypair JSON files (`id.json`, `keypair.json`, `*-keypair.json`, and similar)
- API tokens, passwords, and credentials

If a script needs a secret, document the variable name in a committed `.env.example` and keep the real value local. `.gitignore` blocks the common cases. Still check `git status` before every commit.
