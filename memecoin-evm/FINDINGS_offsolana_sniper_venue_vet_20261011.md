# Off-Solana sniper venue vet — 2026-10-11

## Headline

There is no off-Solana memecoin launch-sniping venue. Measured on-chain, every
non-Solana chain fails on one of two terms, and the two failures are the same
failure seen from different sides:

- **Nine chains are dead on launch supply** (≤2 new pools/hr).
- **The two chains with real supply have no follower flow.** On BSC/four.meme,
  **104 of 138 launches get exactly one trade** — the creator's own buy, atomic
  with the create — and **2 of 138** ever see >0.1 BNB of lifetime volume
  (median lifetime volume **0.0000 BNB**). On Base/Uniswap-v4, **77% of
  "launches" are Zora creator coins and 0.0% of them ever trade**.

The premise under test was "somewhere less competitive where our Rust
engineering wins." Both venues are indeed uncontested: mean distinct txs in the
creation block is **0.35 on Base** and non-creator buyers appear in **2 of 85**
BSC creation blocks. They are uncontested *because there is nothing to contest*.

**Transferable lesson: on a launch-sniping venue, competition IS the flow.**
A sniper is long "followers arrive after me." Rival snipers are the first
followers and the proxy for the rest. A venue with no rivals has no followers,
so there is no P&L to capture at any latency. Solana is crowded *because* it is
the only chain where launches acquire buyers. "Less competitive" and "a lot of
coins to snipe" are not independent wishes — they are the same dial.

This closes the venue-substitution branch for [[pumpfun-reachable-curve-is-breakeven-pre-cost]]:
the Solana seat is unreachable, and the alternative seats are unoccupied.

## Term 1 — launch supply (GeckoTerminal `new_pools`, 2026-10-11 ~23:30 UTC)

Rate from the timestamp span of the newest 20 pools per network.

```text
solana        1754 pools/hr   (pump-fun 15/20, pumpswap 3/20)
bsc            242 pools/hr   (four-meme 10/20, uniswap-v4 4/20, pancake 6/20)
base            78 pools/hr   (uniswap-v4 14/20, uniswap-v2 5/20)  [GT-filtered]
ton              2 pools/hr
hyperevm         1 pools/hr
sui              1 pools/hr
monad           ~0 pools/hr
tron            ~0 pools/hr
avax            ~0 pools/hr
aptos            0 (no data)
abstract         0 (no data)
```

CLOSED on supply, cause `SUPPLY`: **TON, HyperEVM, Sui, Monad, Tron, Avalanche,
Aptos, Abstract.** No latency or selection work can matter at ≤2 launches/hr.
Only **Base** and **BSC** survive to term 2.

Raw Uniswap-v4 `Initialize` on Base is ~280/hr, well above GT's filtered 78/hr.
That gap is itself the Base finding (see below): GT is filtering out dead coins.

## Term 2 — Base, Uniswap v4

PoolManager `0x498581fF718922c3f8e6A244956aF099B2652b2b`, blocks 52484874–52486374
(1500 blocks ≈ 50 min). 217 `Initialize`, 185 with a recognised quote leg.
Eligible = created ≥400 blocks (~13 min) before window end, to avoid scoring
censored launches: **n=125**.

v4 is a singleton: every pool's swaps emit from one address and carry
`sqrtPriceX96`, so entry/exit prices per seat are exactly reconstructable from
logs. Two `eth_getLogs` range scans give the whole tape.

```text
launchpad          n  traded  %traded  atomic  med 1st-swap blk offset
zora-creator      96       0     0.0%       0   —          (hook 0x0469a4bd, vs ZORA)
0xbdf93814/WETH    8       3    37.5%       3   0
0x00000000/ETH     5       2    40.0%       0   38.5
0xb429d62f/WETH    4       4   100.0%       3   0
... 11 further hooks with n=1–2
```

- **Zora creator coins are 77% of eligible launches and 0.0% of them ever
  trade.** This is Base's headline launch rate and it is a mirage: Zora mints a
  coin per social post. Excluding them leaves ~29 real launches in 50 min
  (~35/hr), of which 17 traded.
- **Competition is absent.** Distinct txs touching a pool within the creation
  block: mean **0.35**, median 0, **max 1**. Within 3 blocks: median 1 (the
  create itself); only 4/17 pools reach ≥2 txs. Within 30 blocks: median 2.
- **There is no race to win.** First swaps: 22 atomic with the create,
  **0 same-block-later-tx**, 15 in a later block at median **+22 blocks ≈ 44 s**.
  Nobody is competing for the next-block seat because nobody wants the token.
- Seat P&L was **not scoreable**: no launchpad group reached n≥5 usable
  entry→horizon observations.

## Term 3 — BSC, four.meme

TokenManager `0x5c952063c7fc8610FFDB798152D69F0B9550762b`. Event layout decoded
from log shape (no ABI needed): `0x396d5e90…` (12 words) = TokenCreate
(creator, token, …, name, symbol); `0x0a5575b3…` and `0x7db52723…` (8 words) =
trades carrying token, account, token amount, BNB amount, and a fee word that is
exactly 1% of the BNB word.

Blocks 127103617–127109617 (6000 blocks ≈ 75 min at 0.75 s): **138 creates →
110 launches/hr**. Eligible (≥1500 blocks forward): n=107.

```text
launches with >=1 trade event      85/107  (79.4%)
first trade ATOMIC with create     83/85   (97.6%)   <- the seat is the creator's
distinct NON-CREATOR accounts in the creation block   2/85
distinct txs within 0/1/3/10/50 blocks   mean 1.68, median 1.0, >=2 txs in 2/85
```

The competition proxy is **identical at every horizon out to 50 blocks**, which
is the whole story: a four.meme launch trades in its own block and then never
again.

```text
per-launch trade events (n=138)        lifetime curve volume (n=138)
  0 trades : 31 launches                 median  : 0.0000 BNB
  1 trade  : 104 launches                p90     : 0.0000 BNB
  2 trades : 1 launch                    >0.1 BNB: 2/138
  6+ trades: 2 launches                  >1.0 BNB: 2/138
```

Flow concentration over the same window (1082 trade events, 135 tokens):

```text
top  1 token = 43.0% of all trades
top  3 tokens = 83.2%
top  5 tokens = 87.8%
top 20 tokens = 89.4%
trades landing on a token created in-window: 947/1082 (87.5%)
```

87.5% of flow is on fresh tokens, so what we observe *is* launch flow — it is
simply concentrated in ~1.4% of launches. Buying the median launch buys a token
with zero subsequent volume. Capturing the 1.4% is not a latency problem; it is
the launch-selection problem, already closed forward at −5 to −9%
([[pumpfun-launch-selection-is-closed]]).

**Internal replication** on a disjoint 4000-block window (n=87): traded 76%,
first-trade atomic 98%, >0.1 BNB volume 1.1%, median volume 0.0000 BNB. Same
structure.

## Why the mechanism argument did not save either venue

The pre-measurement thesis was that Solana's death was *ordering*: the seat is
auction-decided (Jito tips) and the creator's bundle owns the front, so latency
cannot buy position ([[pumpfun-sniper-edge-is-inside-the-creation-block]]).
Base genuinely fixes that — no public mempool, a single sequencer, 200 ms
Flashblocks, no bundle market, so across-flashblock ordering is arrival-time
decided and no tip can outbid being early. BSC fixes it differently: a public
mempool makes the create visible *before* inclusion, so the post-create seat
that paid on Solana (+6.8–10.8%) is reachable by gas laddering rather than by
owning a bundle.

Both fixes are real and both are worthless. The binding constraint was never
ordering on these chains — it is **counterparty existence**. This is the venue-
level restatement of the corpus's standing lesson that our compute was never the
bottleneck (7 µs decode against a 7 ms feed,
[[the-execution-gap-is-cancel-side-selection]]). Here even the feed is not the
bottleneck: there is no one on the other side.

## Limitations, stated plainly

- **Time of day is the one unrun robustness arm, and it is blocked.** Both
  windows are ~23:30–00:50 UTC (US evening). Public BSC RPCs refuse logs older
  than ~12 h (`-32602 Archive requests require a pers…`), so the Asian-peak
  window needs a paid archive key. The conclusion survives unless follower
  volume is **~50× higher** at another hour: it must move "2 of 138 launches
  with >0.1 BNB" to a majority. The structure already replicated across two
  disjoint BSC windows and, independently, on Base.
- Base coverage is Uniswap v4 only (~70% of new Base pools). Launches on
  uniswap-v2-base and Aerodrome are not scanned.
- four.meme coverage is the bonding-curve phase only; post-migration PancakeSwap
  trading is invisible here. This cannot hide graduated winners — a token must
  trade through the curve to migrate, and only 2/138 had >1 BNB of curve volume.
- Supply rates come from 20-pool timestamp spans, a point estimate at one hour.
  They are used only to separate ~0/hr from ~100/hr, a 50× gap.
- Hook addresses are reported as launchpad identities but were not attributed to
  named products (Clanker/flaunch/etc.) beyond Zora's 0x0469a4bd.

## Safety

Read-only `eth_getLogs` / `eth_blockNumber` against public RPCs. No wallet, no
key loaded, no transaction built, simulated, signed, or sent. No funds at risk.

## Reproduce

```text
scan_base_v4.py        -> data/base_v4_tape.json       (Base v4 launch tape)
seat_analysis.py                                       (censoring + launchpad split)
scan_bsc_fourmeme.py   -> data/bsc_fourmeme_tape.json  (four.meme launch tape)
concentration.py                                       (flow concentration)
tod_robustness.py                                      (time-of-day; needs archive RPC)
```

---

# Addendum — BSC PancakeSwap v2 direct launches (the classic snipe)

The first pass scanned only four.meme's bonding curve (~110/hr) and so missed the
other half of BSC's supply: **~144 PancakeSwap-v2 `PairCreated`/hr**, where the
classic BSC snipe actually lives (buy in the liquidity-add block). This venue is
structurally different from both four.meme and Solana, and it needed its own vet.

Factory `0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73`. Windows inside the ~2.4 h
public log retention. **n = 44** WBNB launches with liquidity *and* trades.

## The entry is free — the opposite of Solana

```text
first swap atomic with the dev's liquidity-add tx :  1/37   (Solana: 75% bundled)
median lag, liquidity-add -> first swap           : 30 blk  (~22 s)
competing txs in the first-swap block             : mean 1.24, median 1, >=2 in 1/37
pairs that never receive liquidity                : 37/65  (57%)
pairs that receive liquidity and then trade       : 28/28  (100%)
```

Launches gate trading (`enableTrading`/anti-bot), so the liquidity-add is **not**
the open; the first swap lands a median of 30 blocks later and draws ~1 rival.
There is ~22 s of slack and no race. Latency is worth nothing here.

## The naive backtest prints a fortune, and it is an artifact

Entry = first reachable (non-dev) buy at the open. Three exit rules:

```text
A  mark exit at the pool's last marginal price   mean +92,669,296,806%  median +42.20%  win 89%
B  exit at the first price a real sell CLEARED,
   no executable exit -> -100%                   mean        -22.76%    median  +4.02%  win 54%
     bootstrap 95% CI [-45.49%, +1.07%]                                        <- FAILS
B* same, dropping the stuck positions            mean        +19.08%    median  +6.48%  win 83%
     bootstrap 95% CI [-2.83%, +42.18%]  n=24                           <- spans zero
C  best executed sell (hindsight exit)           mean       +341.94%    median +20.57%
```

Rule A is what a standard sniper backtest reports. It is the marginal price of a
pool being drained, against an exit that never clears. 13/37 (35%) of launches
had no executable exit at all.

**Sell tax is not the story.** Median sell-price/buy-price = **0.9848**; only
2/24 are hard honeypots (sell at ~0.001% of buy). Most sells clear at ~98.5% of
the marginal price. So the stuck 35% is *absence of a sell*, not a proven block —
which is exactly the ambiguity the simulator below was built to resolve.

## The terminal state of a launch is a drained pool

Current quote-side reserves, 1–3 h after launch (n=44):

```text
median WBNB reserve            : 0.000077 BNB
< 0.001 BNB of quote liquidity : 38/44 (86%)
< 0.010 BNB                    : 41/44 (93%)
< 1.000 BNB                    : 43/44 (98%)
launches where buyers were net positive in aggregate : 0/37
```

The *shape* names the mechanism: quote reserves go to ~0 while token reserves
balloon (e.g. 9.27e21 tokens against 0.00008 BNB). A liquidity pull (`Burn`)
drops **both** sides. This drops only the quote side, which is the signature of
**a large holder dumping into the pool until every BNB is drained out**. Buyers'
money leaves the pool into the dumper's pocket. (`Burn` logs could not be read
directly: by measurement time the launch blocks had aged past the ~2.4 h public
retention.)

## A pre-trade sell simulator — built and validated

`eth_simulateV1` **is supported** on `bsc-rpc.publicnode.com` and carries state
between calls in one request, so a full round trip can be simulated read-only.

`sellsim.py` runs `balanceOf -> buy -> balanceOf -> approve -> sell -> balanceOf`
as one carried-state sequence and reports round-trip WBNB recovery at our clip.

Validation:

```text
USDT / CAKE / ETH  ->  recovery = 0.9950 for all three
                       0.9975^2 = 0.99500625  (two 0.25% PancakeSwap fees)
simulated buy output / constant-product expectation = 1.0000 (checked vs getReserves)
```

It reproduces the AMM fee floor to four decimals. The tool is correct.

**But it cannot be backtested at launch time.** `eth_simulateV1` against a
historical block needs archive state, and the public BSC RPCs cap at ~2.4 h and
refuse archive requests. Run at `latest` against already-drained pools it reports
recovery ≈ 0.9999 — *better than the fee floor* — because our clip dwarfs a dead
pool. That is a correct answer to the wrong question. The confusion matrix it
produced (20% recall on stuck positions) is therefore **void as evidence** about
launch-time sellability, and is not claimed here.

## Why the simulator does not rescue the seat

The filter answers "can I sell *now*?" The failure mode is "the pool is drained
*after* I enter." No entry-time sellability check prevents a post-entry dump; to
use the filter you would have to predict the dump, which is the launch-selection
problem already closed forward at −5 to −9%
([[pumpfun-launch-selection-is-closed]]). The simulator remains worth keeping as
a hard pre-trade safety gate — it would have blocked 2/24 outright honeypots —
but it is a safety tool, not an edge.

## The one well-posed open test

Rule B charges −100% whenever *nobody else* sold, which assumes we could not have
sold either. That is conservative and probably wrong: we could have sold our own
clip into whatever standing liquidity the pool had.

**Pre-register:** reconstruct reserves over time from `Sync` events, then model
our exit as an actual constant-product sale of a fixed clip at reserves
prevailing entry+N blocks, for N ∈ {1, 2, 5, 10, 30}. This removes the −100%
assumption and yields a genuinely executable exit for *every* launch, stuck ones
included. Decision rule frozen in advance: the seat passes only if mean realised
BNB return has a bootstrap 95% CI strictly above 0 at some N, and replicates on a
disjoint window. Requires **forward** collection (`Sync` + `Swap` + `Mint` +
`Burn`) because public retention is ~2.4 h.

## Limitations

- n=44 WBNB launches; the B* arm is n=24. Underpowered; CIs are wide.
- One time-of-day window (~23:30–02:00 UTC) for every BSC measurement.
- No archive access: no historical simulation, no `Burn` history, no
  time-of-day arm.
- Our own market impact is not modelled in rules A–C (the open test above fixes
  this by pricing our clip on the curve).
- `Sync`-based reserve reconstruction was not captured in this pass.

## Status

**Not closed.** Entry is reachable and uncontested — genuinely better than
Solana — and the terminal-state and aggregate-buyer evidence is strongly
negative, but the decisive exit model has not been run. One cheap forward test
decides it.

---

# CORRECTION 2026-10-12 — BSC block time is 0.450 s, not 0.75 s

Measured from block timestamps over 4000 blocks: **0.450 s/block**. Every BSC
figure above that was derived by multiplying a block count by an assumed 0.75 s
is wrong by 1.67×. Rates were **understated**; elapsed times were **overstated**.
No conclusion changes, but the corrected values are:

```text
claim as written                          corrected
four.meme: 6000 blk "= 75 min"            45 min
four.meme: "110 launches/hr"              184 launches/hr
PancakeSwap v2: "~144 PairCreated/hr"      240 PairCreated/hr
eligibility window 1500 blk "~19 min"     11 min
forward window 800 blk "~10 min"          6 min
forward window 900 blk "~11 min"          6.75 min
lag liquidity-add -> open: 30 blk "~22 s"  13.5 s
```

The load-bearing one is the last: the slack between the liquidity-add and the
real open is **~13.5 s**, not ~22 s. Still ample against a measured mean of 1.24
competing txs, so "latency is worth nothing on this venue" stands — but the
honest figure is 13.5 s.

Public log retention was also measured in blocks, so it is **between ~1.2 h and
~2.4 h** of chain time (9600 blocks back succeeded, 19200 failed), not a firm
2.4 h. The collector's 11200-block span = **1.40 h**, safely inside it, and the
accumulator interval is set to 4200 s (1.167 h) to leave overlap rather than
gaps — a missed window is lost permanently.

Base figures are unaffected: Base is 2 s/block and that is what was used.

GeckoTerminal supply rates in Term 1 are unaffected — they were computed from
pool timestamps, not block counts.
