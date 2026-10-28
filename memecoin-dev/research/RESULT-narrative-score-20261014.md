# RESULT: scoring the overnight narrative collection (2026-10-14, day 3)

EXPLORATORY, one day. Code `narrscore.py`, output `outnarrscore.txt`. Inputs: the laptop tape + posts from
`run_narrative_overnight.sh` (started 09:24Z) and the VM tape's creates (`data/vmcreates-1013.jsonl.gz`,
from 10-13 20:25Z) for the 6 h new-term warm-up and creator history. The laptop feed had about 48 min of
outages after 19:25Z (DNS/WS drops), so the clean launch window is **09:26Z to 18:25Z**: 11,850 non-Mayhem
launches. Training table: `data/narr-table-20261014.jsonl.gz`, one row per launch, with visible-at-launch
fields and first-hour labels.

## 0. Instrument fix: self-graduated coins
116 launches were coins where the creator bought ≥ 50 SOL of its own curve in the first hour (brand
names like Apple, Riot Games, Claude AI: ~85 SOL, 0 outside buyers). They were **46% of all "$40k hits"**,
and they arrive in fresh-wallet batches that fake a term "catching on". They are now dropped from the
labels and from the term history, like Mayhem coins. The 10-10/10-11 $40k base rates (2.1%/2.0%)
included them; on day 3 without them the base is **1.13%**.

## 1. Day-3 replication of the narrative base rates
| cell | n | P(≥$40k) | mean fee |
|---|---|---|---|
| all launches | 11,850 | 1.13% | 0.091 |
| LEADER (first on a new term that caught on) | 333 | 5.41% | 0.464 |
| FOLLOWER (copy) | 2,999 | 0.90% | 0.084 |
| first on a new term, unconditional | 4,696 | 1.28% | 0.101 |

The catch-on rate is **7.09%** (was 6.16%/6.94%). All three days agree: leaders win, copies don't, and being
first on a random new term is roughly the base rate.

## 2. The linked X post is what separates leaders
- 76% of leaders link an X post, against 38% of first-on-new-term coins.
- A leader's post was **13 s** old at launch (median; p25 2 s, p75 49 s).
- First on a new term AND links a post ≤ 15 s old: **19.6% catch on** (vs 7.1%); mean fee 0.24 SOL;
  median 0.029 SOL; 61% clear the 0.0056 launch cost.
- The first coin on any post ≤ 15 s old (n 1,615): median fee **0.039 SOL** (7x cost), 62% clear cost.
  All launches: median 0.0004, 40% clear cost.
- **Within creator** (134 creators who launched both kinds), the fresh-post first coin vs the same
  creator's other coins: fee **+0.207 SOL (t 3.46)**, log-fee t 4.01, +49 outside buyers (t 3.48), leader
  +4.2pp (t 1.98). One day, so not yet replicated under the |t| ≥ 2.5 on two days rule.
- Copies don't pay: the second coin on a caught-on post gets P($40k) 0.90% and median fee 0.
- Promo/dev-style post text (CA, pump, "narrative", $TICKER) does no better than other posts. Many linked
  posts are devs' own (handles like *dev*, Pumpfun, shill accounts); `post_promo` and
  `handle_prior_coins` are in the table, but they don't separate outcomes.

## 3. Can visible-at-launch features pick? (hashed text of name/ticker/description/post + history)
Time split 60/40, fit both ways:
| target | AUC (fwd / reversed) | top 10% hit vs base | top 10% mean fee vs base |
|---|---|---|---|
| caught on, among first-on-new-term | 0.789 / 0.767 | 27.3% / 19.3% vs 7.1% / 6.8% | 0.25 / 0.29 vs 0.09 / 0.10 |
| reached $40k, all launches | 0.734 / 0.778 | 4.4% / 2.7% vs 1.2% / 0.9% | 0.19 / 0.13 vs 0.09 |
| reached $40k, among post-linkers | 0.62 / 0.66 (CI touches 0.5) | ~2% vs 0.9% | — |
| post coined by 3+ others, among first on a post | 0.661 / 0.667 | 19.5% / 14.8% vs 10.3% / 7.8% | 0.23 / 0.25 |

The text model's top 10% (19–27%) is barely better than the one-line rule "first coin on a post ≤ 15 s
old" (19.6%). **The picking edge is speed onto a fresh post, not reading the narrative.** Among posts,
the text can't tell which will reach $40k.

## 4. Attainability
The leader was ahead of the next creator on the term by a median of **12 s**, and 43% led by ≤ 5 s. To
be the leader you have to coin a post within seconds of it appearing. That is j7tracker-feed speed, not
LLM-design speed (devbot's concept + art pipeline takes tens of seconds and is out of free quota). A
copy that arrives second gets base rate or worse.

## 5. Scoring a picker (gut or AI) for free
`data/narr-blind-20261014.csv` has 200 first-on-new-term coins with a fetched post, from the later 40%,
with outcomes hidden (15.5% caught on in it). Put any nonzero `score` on the picks, then run with
`--picks <file>`. **Power warning:** a random 20% pick from the sheet scored 20.6% [8.8%, 35.3%] against 14.5%
unpicked. A 200-row sheet only detects a picker that roughly doubles the base rate.

## Caveats
- One day, a 9 h window, first-hour outcomes, one venue.
- Fees count every non-creator wallet, so a dev's own bundle wallets inflate fee and buyers; the
  within-creator test controls for the dev, but not for per-coin bundling.
- `post_ok` and the post text are fetched seconds after launch, which is visible-at-launch in practice.
- The text model is a linear bag of words, not an LLM.
