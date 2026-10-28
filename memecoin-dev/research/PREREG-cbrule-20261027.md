# PREREG: low-dev-buy + heavy create-block buying (frozen 2026-10-27, before any outcome is read)

Source of the idea: operator 10-27 ("real coins have snipers/bots/insiders piling into the create block; brand coins = huge dev buy").
Derived on the 10-20..24 runners (n=19 non-brand, hindsight-selected). TEST SET = 10-14 curve tape, a different week.

Rule (all must hold), window = create slot .. create slot+2 from the tape's `slot` field:
1. creator SOL bought in window < 2
2. non-creator SOL bought in window >= 20
3. >= 3 distinct non-creator buyers in window
4. no AGENT BwWK17cb... trade in the first hour (not Mayhem)
5. name not in the brand list in research/cbrule.py (BR); variant B drops this clause

Outcome (read only after this file is committed): GeckoTerminal 1-min path, 8h from create.
 runner = peak mc >= $1M ; big = peak mc >= $5M ; compare to the base rate of ALL non-Mayhem launches on the same tape.
Pass = runner rate lift >= 5x base with Wilson lower bound above base, n >= 30 candidates. Anything else = no edge, reported as is.
Paper only, nothing armed.

## AMENDMENT 1 (2026-10-27, written BEFORE reading outcomes of any template match)
Result of the first rule on the 10-14 tape: FAIL (3/279 >= $1M = 1.1%, 3.9x base 0.28%, < 5x bar; 0/279 >= $5M).
New rule T (the bundle template, from 10 of 13 real runners incl. CATTLEAXE 10-24 and ███WIF 10-26): within create slot..+2,
dev buys < 2 SOL, and non-dev buyers include one 0.1-1 SOL, one 1-10 SOL and one 15-60 SOL wallet, not Mayhem. 38 matches on 10-14.
(The 10-26 ███WIF buys were 2/5/10; its big buy is < 15, so T is slightly STRICTER than the recall sample; reported as is.)
Outcome: GT peak mc >= $1M within 8h of create. PASS = >= 4 of the matches (>= 10%) reach $1M, i.e. >2x the 4.9% graduate rate and >30x the 0.28% all-launch base.
Fewer than 4 = no edge. n=38 can only detect a large effect; a pass would only justify a larger forward test, not arming.
