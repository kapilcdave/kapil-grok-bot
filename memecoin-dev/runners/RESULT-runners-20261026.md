# RESULT: narrative runners, primary 4 h hold (2026-10-26)

Scored once, at the preregistered trigger in `PREREG-runners-20261021.md`: 60 primary picks with a 4 h exit. Tape `runners/data_box/runners_2026102{2,3,4}.jsonl.gz`. Code `runners/score.py`, unchanged. 916 coins admitted and enriched, 2026-10-22 00:15Z through 2026-10-24 07:31Z. Rule outcomes: PICK 89, no_story 448, not_original 358, mc 21. Of the 89 picks, 63 have a 4 h exit, 23 have no snapshot inside the 3 min entry window, 3 are still censored. No order was sent.

## Primary (4 h, clip $100, 1% per leg plus impact)

n=63. Mean net **−82.4%**, bootstrap 95% CI **[−90.4%, −72.6%]**. Median −99.7%. Win rate 3.2% (2 of 63). Touched 2x inside the hold: 20.6%.

- **G1 real:** fail. Mean is negative and the CI is entirely below 0.
- **G3 replicates:** fail. First half (n=31) −84.7%, second half (n=32) −80.1%.
- **G4 not one coin:** fail. Best pick +59.1%. Dropping it leaves −84.7%.
- **G2 selection:** fail. Primary minus control is negative. The control *mean* (+15,234%, n=514) is not a price: 4 coins print +57x to +57,410x because the tracker flapped pools (the bug fixed for the news ladder in `score.Pin`, amendment 1 of the news-first v2 prereg). Winsorizing control at +500% gives mean −51%. Control median −97.7%, win rate 12.8%. Primary does not beat control on the mean test, the median, or the win rate. Re-running the 63 primary exits on pinned snaps changes nothing: 0 of 63 switched pool between entry and exit, and the mean stays −82.4%.

1 h, 12 h, and 24 h holds on the same picks are also negative (1 h −68.0% n=65; 12 h −85.7% n=46; 24 h −81.2% n=25). `primary_keep` (the avoid filter, not a gate) is −33.3% at 4 h, n=11.

## Not this score

News-first v2's primary is the ladder, not this 4 h hold. Closed `any_pick_v2` ladders are still 52, the same set named in amendment 1: win 9.6% vs control 13.3%, mean −57.6% [−67.1%, −48.1%]. The formal v2 trigger is 60 closed ladders. W1 cannot clear from here, as that amendment already said.
