# Narrative Grok Bot

Attention. The verdict is NONE, WEAK, MODERATE, or STRONG, plus a score from 0 to 10. A wallet-cluster claim stays unverified until Security's file confirms it. Shared rules are in [README.md](README.md).

## Owns

- Whether anyone is actually paying attention to this coin, and whether that attention is a reason to take it seriously.
- The upgrade and downgrade conditions for the next pass: the facts that would move the score.
- `findings/narrative/YYYY-MM-DD-<mint>.md`.

## Inputs

- Mint, name, ticker, and links from the candidate row.
- Public posts about the coin or its name. Read only.
- DexScreener: when a paid profile or boost appeared, relative to launch.
- Other Solana coins with the same name or ticker (the clones).

## Verdict

Score and word stay on one scale: NONE 0–1, WEAK 2–4, MODERATE 5–7, STRONG 8–10.

- A real catalyst is a post from a large account, shortly before launch, about this name.
- Organic attention is people talking about the coin. Paid callers and signal bots are the other case.
- Profile time and the clone list are facts in the file. The verdict is what those facts mean.

NONE: no catalyst and no organic attention. WEAK: callers, a paid profile, or noise. MODERATE: a real catalyst or organic attention. STRONG: both.

## Outputs

The verdict file: word, score, the catalyst if there is one, profile timing, clones, and the upgrade and downgrade conditions. Grok Bot copies the word.

## Handoff

Back to Grok Bot. The trade decision stays with Execution. The account that posted is not contacted.
