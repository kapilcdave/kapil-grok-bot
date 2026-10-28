# PREREG: does the big-dev-buy edge hold on OTHER days, and which trigger fits our position? (10-16)

Written 10-16 ~08:20Z, BEFORE any row of the new tape is scored. Tape: VM `~/tape/tape-YYYYMMDD.jsonl`
(snipefeed `--watch-s 130`, public WSS, started 10-16 ~08:10Z for 48 h). Scorer: `research/tapescore.py`.

## Why
Every number so far (`research/devpnl.py`, memory `big-dev-buy-pays-on-others-launches`) comes from ONE night
(10-15 21:22Z to 10-16 05:40Z), an Elon/Tesla Roadster narrative wave. The "hot theme" result (4+ other
creators on the theme: +0.245) rests on ~5 stories, 23 of its 82 coins being "elon". Our one live 3 SOL launch
($Wreckage, DZ, no bundle, first on a 7 s old @dailystar post) drew zero trades: −0.111.

## Fixed definitions (same as devpnl.py)
- Population: non-Mayhem creates. Mayhem = any trade breaking v_sol*v_tok = 30 * 1.073e9 by >1%, or any
  trade by BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s.
- Dev buy = the creator's buys in the create slot. "~3 SOL" = 2.95-3.10; band = 2.5-5.5.
- Score = FOLLOWERS t30 at FEE 1.75%/side and COST 0.02: the creator's create-slot tokens valued on the curve at
  t+30 s (slots * 0.4 s). The replay drops the creator, create-slot buyers, wallets selling tokens they never
  bought, and wallets that bought 2+ coins of ONE creator only (followers, over the whole tape).
- A mint is dropped if a feed gap (> 5 s without any row) touches its first 130 s.
- No create-slot buyers = OUR position (no bundle, nobody watching our wallet).
- Hot theme: >= 4 creates by OTHER creators in the prior 30 min that share a name/symbol word (len > 2, stop
  list in tapescore.py).
- Post age: from the create to the twitter `/status/<id>` snowflake time.
- 95% intervals: bootstrap, 4000 resamples, seed 0.

## Tests, per UTC day of tape (each day scored once, when it is complete)
- **P1:** ~3 SOL mean > 0 AND lower 95% bound > 0.
- **P2 (our position):** ~3 SOL with no create-slot buyers, mean > 0 AND lower 95% bound > 0.
- **P3 (hot theme):** band 2.5-5.5, hot minus not-hot > 0 with lower bound > 0, AND the hot set spans >= 5
  distinct trigger words.
- **P4 (post age):** band 2.5-5.5, linked post 60-600 s old minus < 60 s old > 0 with lower bound > 0.

## Decision
A live 3 SOL launch is justified only if P1 AND P2 pass on a NEW day, and the trigger we would launch on
(P3 or P4) also passes that day. If P2 fails, our position (no bundle, no followers) does not have the edge,
whatever P1 says. Then STOP; no more 3 SOL launches.

## Amendment, 10-16 ~08:45Z (only the tape's first ~200 s were seen: 13 launches, 1 at ~3 SOL, for the scorer cross-check)
The j7 access token expires at 15:20Z, so a first checkpoint is added. **Block A** = creates 10-16 08:00-14:00Z,
scored ONCE at ~14:05Z with the same P1-P4 rules and decision. **Block B** = 14:00Z to 10-17 00:00Z, scored once
after it ends. A launch in the 14:05-15:20Z window is justified only if Block A passes P1 and P2 and the
chosen trigger. If Block A has fewer than 15 launches at ~3 SOL, it is UNDERPOWERED: no launch, wait for Block B.

## Amendment 2, 10-16 ~09:30Z, before any row of these tapes was scored for this question
The operator asked for nights before the Tesla wave. Two full-day snipefeed-2 tapes are on the laptop,
`research/data/tape-20261010T225715Z.jsonl.gz` and `tape-20261011T225728Z.jsonl.gz` (10-10 23Z to 10-12 23Z). No
part of the 3-SOL finding used them. They are scored with the SAME P1-P4 and the same decision, per UTC day.
These tapes are the full firehose; the scorer keeps each mint's first 130 s, as `--watch-s 130` does.

## Results (scored once each, as fixed above)
- **Block A** (08-14Z, scored 14:05Z): P1 +1.198 n=286 PASS; **P2 +0.024 CI[−0.081,+0.156] n=15 FAIL**; P3 −0.074 FAIL;
  P4 −0.467 FAIL. Decision as written: STOP.
- **Block B** (14-24Z, scored 00:10Z 10-17): P1 +0.940 n=382 PASS; **P2 +0.236 CI[+0.007,+0.538] n=37 PASS**, win 35%;
  P3 −0.347 FAIL; P4 −0.473 FAIL (fresh beats older, the opposite of P4's claim).
- Reading: by the letter, no block meets the decision (A fails P2; neither block passes P3 or P4, and the fresh-post trigger
  was never itself a pre-declared test). Post hoc, the full day pooled is P2 +0.175 CI[+0.007,+0.398] n=52, win 37%,
  median −0.118, worst −0.127, in line with 10-11 (+0.187), 10-12 (+0.145) and the Tesla night.
