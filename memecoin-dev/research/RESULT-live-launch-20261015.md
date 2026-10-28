# RESULT: first live launch of the measured dev-buy lever (2026-10-15)

Two live pump.fun launches from `DZo2Ako…j1RS` using the new `launch_one.py` (create with the dev buy
bundled in the create tx → log the curve for 60 s → verified 100% exit). Prediction under test, from
`RESULT-bot-draw-20261015.md`, fresh+quiet cell at a 0.5–2 SOL dev buy: **P(broad sniper in our launch
block) 29–46%, median 4–5 distinct bot buyers and 2–3 SOL of bot buying in the first 30 s.**

## What happened

| | rehearsal | real launch |
|---|---|---|
| coin | exit rehearsal (`$REHEARSE`) | Quiet Hours (`$QUIET`) |
| mint | `AJBszq48AoYAJknn2nQSTZQn2xbxwWw5x54Ahb2r64Tq` | `4pYUXg1DSgpBTtDkGRcBjYaV5LLSvjn6pcssZLfkHo6S` |
| dev buy | 0.02 SOL | **0.50 SOL** |
| outside SOL on the curve, 60 s | 0.0000 | **+0.0010** |
| broad snipers in the launch block | 0 | **0** |
| distinct non-us wallets, ever | 0 | **1** (at +34 slots ≈ 7 s, and again at +356) |
| sell | confirmed, attempt 1 | confirmed, attempt 1 |
| returned | +0.0211 on a 0.02025 basis (**104%**) | +0.4927 on a 0.50625 basis (**97.3%**) |
| all-in P&L | **−0.0066 SOL** | **−0.0234 SOL** |

Wallet 0.668096 → **0.6381 SOL**; both positions closed (token balance 0, curve drained to ~0); ledger
holds both launches and both sales. Creator fees claimable rose 0.003778 → **0.006778 SOL**.

Chain record of the real launch, from the bonding-curve account's own history:

```
        slot  +slots   +s  who        action
   457184873       0    0  US (dev)   CreateV2, Buy      <- our launch block, we are alone in it
   457184907      34    7  J5WuTkyz…  (0.001 SOL)
   457185178     305   63  US (dev)   Sell
   457185229     356   74  J5WuTkyz…
```

## Reading it honestly

- **The plumbing is proven.** Two creates with a bundled dev buy, two verified exits, both confirmed on
  the first attempt, both leaving a zero token balance. The retry-only-while-tokens-remain guard never
  had to fire. This was the point of the rehearsal and it is the part that was genuinely unknown.
- **The one live draw of the lever came up empty.** Zero broad snipers. That is the *modal* outcome in
  the cell we aimed at — 54–71% of those launches get no broad sniper — so one launch neither confirms
  nor refutes the 29–46%. It is one Bernoulli draw; the arrival rate needs tens of launches.
- **The downside behaved exactly as bounded.** 97.3% recovery against a 94.4% floor, and the all-in
  −0.0234 SOL sits in the predicted no-buyer band (−0.0165 to −0.02, plus dust).

## Two corrections to the research NET model (both make the dev buy cheaper)

1. **A 100% sell closes the token account and refunds its rent (~0.002 SOL).** The rehearsal returned
   **104%** of its cost basis for this reason. `bots.py`'s NET never credited it.
2. **We earn the 0.30% creator fee on our own dev-buy round trip.** Claimable fees rose +0.003 SOL on
   ~0.993 SOL of our own buy+sell volume, so ~0.6% of the 2.5% fee round trip comes back to us.

Net effect: the true cost of an unanswered 0.5 SOL dev buy is ≈ **−0.020 SOL** after claiming, not the
−0.034 worst case or the −0.0165 modelled median.

## What would make the next draw worth taking

At −0.020 to −0.023 SOL per empty launch and 0.6381 SOL left, the bankroll funds ~27 more draws at
0.5 SOL. The cell's own numbers say ~1 in 3 should bring a broad sniper and a median +0.18–0.21 SOL when
≥5 buyers arrive, so the arithmetic is viable in expectation but is a *many-draw* proposition, not a
one-shot one. Nothing here changes the lever ranking; it only establishes that we can take the bet and
reliably get our stake back.
