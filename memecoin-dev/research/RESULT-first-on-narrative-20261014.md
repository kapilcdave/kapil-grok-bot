# RESULT: is being FIRST on a new narrative worth it? (2026-10-14, exploratory)

Code `narrative.py`, output `outnarrative.txt`; tapes 10-10/10-11, Mayhem coins dropped, first 6 h warm-up.
A term (devbot.trends.terms) is NEW if unused for 6 h; it CAUGHT ON if 3+ other creators used it within 1 h.
Peak market cap is first-hour and curve-only, so "$40k" ~ graduation at SOL $107 (nothing on the curve reaches $100k).

| cell | Oct 10 P(>=$40k) | Oct 11 | mean fee (SOL) |
|---|---|---|---|
| all launches | 2.12% | 1.95% | 0.082 / 0.074 |
| leader: first on a new term that caught on | 5.92% | 5.76% | 0.427 / 0.361 |
| follower: later coin on that term | 0.88% | 0.91% | 0.097 / 0.061 |
| **first on a new term, unconditional** | **1.95%** | **2.09%** | **0.085 / 0.086** |

- Being first is worth nothing by itself: unconditional first movers are base rate. The leader premium
  is selection on the narrative catching on (people copy BECAUSE the leader pumps).
- Only 6.2% / 6.9% of new terms catch on. The whole value is skill at predicting which ones will.
- Copying a narrative that is catching on is WORSE than a random launch (0.9% vs 2.0%), on both days,
  consistent with the human study's hot-copy result (~11% less volume).
- Implied: mean fee ~0.06 + h x 0.36 SOL for a picker whose new terms catch on at rate h (h = 6-7% is base rate).
  A picker can be scored for FREE: log its picks, check an hour later whether 3+ other creators used the term.
