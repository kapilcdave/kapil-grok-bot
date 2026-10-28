# RESULT: getting MORE bots to buy our coin in the first seconds (2026-10-15)

Scores `PREREG-bot-draw-20261015.md` on the same two tapes (10-10, 10-11). Code `botdraw.py`,
outputs `outbotdraw-1010.txt`, `outbotdraw-1011.txt`. Replicates = same sign and |t| >= 2.5 on both
days, within creator, cluster-robust by creator. Non-Mayhem launches: 21,690 (10-10) / 24,776 (10-11)
of 32,625 / 33,789 in sample. Broad snipers (launch blocks of >= 50 distinct creators) = 85 / 73
wallets. Base rate P(broad sniper in our launch block) = **23.9% / 19.5%**.

## 0. Two measurement fixes to the 10-14 pipeline
- **The create signer is a second dev-side wallet.** Signer != creator on 16.8% / 20.0% of creates,
  and the bundled create-tx dev buy came from the *signer* on 21.0% / 23.7% of launches that had one.
  `bots.py` only treated `creator` as the dev, so those buys were counted as **bot volume** and that
  position was left out of NET: dev NET mean +0.667 vs +0.470 (10-10) and +0.624 vs +0.452 (10-11).
- **A relayer in its own coin's launch block is not a sniper we drew.** The coin's own create signer is
  11.8% / 11.7% of launch-block buyer slots. Counting it as a sniper (prior convention) reads
  P(broad sniper) 24.8% / 22.1% instead of 23.9% / 19.5% — so the 10-14 headline survives, slightly inflated.

## 1. The dev buy IS the draw (reference arm, exploratory)
P(broad sniper in launch block) and distinct non-creator buyers in the first 30 s, by dev buy
(dev = creator + signer), non-Mayhem, 10-10 / 10-11:

| dev buy (first 60 s) | P(broad sniper) | n_30s p50 | median NET |
|---|---|---|---|
| none | 3.2% / 3.8% | 0 / 0 | -0.0056 / -0.0056 |
| <= 0.1 SOL | 5.1% / 4.4% | 0 / 0 | -0.0056 / -0.0056 |
| 0.1-0.5 | 20.5% / 20.0% | 1 / 1 | -0.0082 / -0.0074 |
| 0.5-1 | 38.9% / 28.6% | 3 / 4 | +0.0292 / +0.0466 |
| 1-2 | 48.4% / 32.6% | 6 / 6 | +0.1201 / +0.1213 |
| > 2 | 45.5% / 39.1% | 12 / 12 | +1.0540 / +0.9201 |

Within creator, `log1p(dev buy)` **replicates** on distinct early buyers (+0.499 t 4.5 / +0.609 t 5.8)
and on bot SOL in 30 s (+0.516 t 5.4 / +0.692 t 6.3). It does **not** replicate on P(broad sniper)
(t 1.2 / 3.2). ⚠ The NET column at > 2 SOL is the **bundle cell** of
[[RESULT-bot-levers-20261014]] (insider supply sold into followers), not a dev-buy return.

## 2. Scored levers
| lever | effect | verdict |
|---|---|---|
| **H1 separate-tx dev buy** (vs bundled in the create tx) | 99.3% of all dev buys are bundled; the separate-tx arm is 90 / 109 launches and the 1-8 slot arm is 0 / 4. Cells disagree in sign (22.2% vs 36.0% blk sniper on 10-10; 33.0% vs 27.2% on 10-11), within creator ~0 (t -0.2 / +0.1) | **not scoreable** (se ~4.6pp, only a >= 9pp effect is detectable). No sign that bots discount a bundled dev buy — which is what devbot already sends |
| **H2a same-slot collision** (other creates in our slot) | n_30s **-0.069 (t -3.2) / -0.072 (t -2.9)** | **replicates**, small: a clean slot is worth ~+5-8% early buyers |
| **H2b creates in the prior 60 s** | n_30s -0.056 (t -2.3) / -0.058 (t -2.4) within creator; **-0.103 (t -3.3) / -0.069 (t -2.6)** within creator x hour | **replicates only under the same-window control**; p10->p90 of launch rate (19->45, 21->52 per min) ~= -6 to -9% early buyers. Fails on P(broad sniper), bot SOL and NET |
| **H3 gap since the creator's last launch** | 3-8 h: n_30s **+0.128 (t 3.0) / +0.124 (t 3.2)**, bot SOL **+0.135 (t 3.5) / +0.097 (t 2.8)**. 1-3 h misses (t 2.5 / 2.2), > 8 h inconsistent (t 3.3 / 0.7). First in tape: n_30s +0.214 (t 10.1) / +0.271 (t 14.3), P(broad sniper) **+9.4pp (t 8.7) / +5.0pp (t 5.0)** | **replicates, and SATURATES at 3-8 h** — not monotone in gap. Confirms the 10-14 freshness lever with the corrected buyer definition. No NET effect in this spec either day (\|t\| <= 1.3) |
| **H4 last coin's visible outcome** | cross-section: 54.6% / 52.4% after a sniped coin vs 14.1% / 14.9% — **creator selection**. Within creator: prev_got_sniper **-17.0pp (t -3.0) / -16.6pp (t -2.4)**; prev first-30 s SOL ~0 (t +0.6 / +0.4) | **predicted direction REFUTED.** Dressing the previous coin up does not bring snipers to the next one; if anything they alternate away. The negative itself misses the bar on day 2 |
| **H5 copy-trade pull** (descriptive) | pooled pull **-1.63 / -1.66 SOL** (a buy is followed by *less* flow than preceded it). But 57 / 52 of ~1,350 frequent wallets (3.9-4.2%) have positive pull at t >= 3, mean **+2.57 / +2.31 SOL**, top wallets +8 to +13 SOL within 3 s. One of them buys in our first 30 s on only **1.1% / 1.3%** of launches; P(broad sniper) 48.6% / 40.2% when one does vs 23.6% / 19.2% when not (association, confounded by momentum) | **channel exists, is rare, and is an upper bound.** Being-followed needs a wallet with history, which is the opposite of the freshness lever — they have to be two different wallets |

## 3. Everything we control, together (non-Mayhem)
Fresh = this creator has not launched for >= 3 h; quiet = no other create in our slot.

| cell | n | P(broad sniper) | n_30s p50 / mean | bot SOL 30 s p50 / mean | median NET |
|---|---|---|---|---|---|
| fresh+quiet, no dev buy | 2,426 / 2,114 | 4.8% / 5.7% | 0 / 1.5 — 0 / 1.7 | 0.000 / 1.37 — 0.000 / 1.70 | -0.0056 / -0.0056 |
| fresh+quiet, dev buy 0.5-2 | 3,761 / 4,446 | **45.8% / 30.2%** | 4 / 12.8 — 5 / 17.0 | **2.14 / 9.68 — 3.00 / 10.35** | +0.0704 / +0.0951 |
| fresh+quiet, dev buy > 2 | 2,318 / 2,588 | 49.3% / 38.6% | 13 / 23.4 — 11 / 20.9 | 10.12 / 16.38 — 8.81 / 15.29 | +1.0467 / +0.8456 |
| stale/busy, no dev buy | 2,316 / 2,906 | 1.5% / 2.3% | 0 / 0.5 — 0 / 0.4 | 0.000 / 0.30 — 0.000 / 0.22 | -0.0056 / -0.0056 |
| stale/busy, dev buy 0.5-2 | 1,818 / 2,115 | 36.9% / 30.3% | 4 / 9.6 — 4 / 10.7 | 1.33 / 5.46 — 1.48 / 5.86 | +0.0382 / +0.0347 |
| stale/busy, dev buy > 2 | 1,794 / 2,193 | 40.6% / 39.6% | 12 / 21.2 — 12 / 21.8 | 8.92 / 15.71 — 9.70 / 16.60 | +1.0628 / +1.0237 |

A 0.5-2 SOL bundled dev buy takes the median launch from **0 bot buyers and 0.000 SOL** in the first
30 s to **4-5 buyers and 2-3 SOL**. Freshness + a clean slot roughly **doubles the median bot SOL at a
given dev buy** (2.14 vs 1.33, 3.00 vs 1.48) and triples P(broad sniper) when there is no dev buy
(4.8/5.7% vs 1.5/2.3%), but adds little once the dev buy is > 2 SOL.

## 3b. Can the dev buy be lost? No — the curve floors it (added 10-15)
`devrisk.py`, outputs `outdevrisk-1010.txt` / `-1011.txt`. 136 (10-10) / 184 (10-11) non-Mayhem
launches with a bundled dev buy in 0.4-0.6 SOL that the dev did not sell inside the hour; the position
is valued by liquidating all of it into the observed curve state.

| recovery (SOL back / SOL paid) | 10 s | 60 s | 1 h |
|---|---|---|---|
| p10 | 0.975 / 0.975 | 0.975 / 0.975 | 0.948 / 0.944 |
| p50 | 1.000 / 1.015 | 1.005 / 0.998 | 0.976 / 0.975 |
| p90 | 2.552 / 2.776 | 2.219 / 4.355 | 1.332 / 13.126 |
| **P(recovery < 90%)** | **0.0% / 0.0%** | **0.0% / 0.0%** | **0.0% / 0.0%** |
| P(< 50%), P(< 10%) | 0 / 0 | 0 / 0 | 0 / 0 |
| worst observed | 0.975 / 0.945 | 0.955 / 0.938 | 0.938 / 0.938 |

P(dev NET < -0.05 SOL) is **0.00% at every horizon on both days**. By outside buyers at 1 h:
none (22.8% / 16.8% of launches) median NET **-0.0165** (fees + launch cost, nothing else);
1-4 buyers -0.0141 / -0.0144; >= 5 buyers (53% / 54%) **+0.2102 / +0.1756, P(NET>0) 85% / 92%**.

**Why it is bounded.** A pump.fun curve is reversible and its state cannot fall below the fresh curve
(that would need more tokens sold than were ever bought). Selling the whole position into a fresh
curve returns `30b/(30+2b)` before fees, so for a dev buy of `b` SOL the recovery floor is
`30/(30+2b) x (1-0.0125)/(1+0.0125)`:

| dev buy | floor recovery | worst-case loss incl. launch cost |
|---|---|---|
| 0.1 SOL | 96.9% | 0.0088 SOL |
| **0.5 SOL** | **94.4%** | **0.034 SOL** |
| 1 SOL | 91.4% | 0.092 SOL |
| 2 SOL | 86.1% | 0.288 SOL |
| 5 SOL | 73.2% | 1.365 SOL |

The worst launches in the tape sit exactly on that floor (observed 0.938 for a 0.595 SOL buy, formula
0.938): the curve had drained back to `v_sol = 30.0000` — everything dumped — and the position was
still worth 93.8% of cost. The loss is our own price impact (`2b/(30+2b)`) plus the 2.5% round trip,
not a price collapse.

**The two ways to lose more than that are not market risk:**
1. **The curve completes and we keep holding.** 2.9% / 11.4% of these launches graduated inside the
   hour; past migration the position is a PumpSwap AMM position with no floor. At graduation it is
   worth far more than entry, so this is a failure to sell, not a loss of value.
2. **The sell never lands** (autosell loop dead, RPC down, dependency broken — the precedent is the
   segment maker's blind arming). On the curve the floor holds at any horizon, so time is not the
   risk; not selling is.

## 4. What this means for devbot
- The draw ranking is **dev buy >> wallet freshness (>= 3 h, saturating) > clean slot / quiet minute >
  everything else**. `DEV_BUY_SOL` (default 0) is the only lever with a large effect; `ROTATE_WALLETS`
  and a >= 3 h spacing rule are second; avoiding the busiest minutes is worth single-digit percent.
- Arrival is not income: 2-3 SOL of bot buying is 0.006-0.009 SOL of creator fee. The dev buy pays
  only through selling it into the arrivals (`AUTOSELL_PCT`), and it is a bet — the 10-14 result
  stands that with no launch-block buyer a > 2 SOL dev buy nets a median -0.055 SOL.
- Three ideas are now closed: a separate-tx dev buy is unscoreable from a tape (and bundling is not
  penalised), dev reputation from the previous coin does not carry (H4 refuted, sign negative), and
  the copy-trade channel reaches only ~1% of launches.
- Caveats: two days, one venue, a 30 s / 1 h window, P(broad sniper) at TH=50, and all NET means are
  dominated by bundle cells. H3's NET had no effect either day here, where 10-14 measured +0.009 with
  the creator-only dev side — the corrected dev side has a fatter tail, not a different sign.
