# RESULT — the birth transaction is the deployer's own buy, so no latency or network buys entry

Descriptive measurement, not a strategy test: counts over the 9,290 virgin curves in
`data/pumptape/`. No gates, no P&L. Read-only, $0.

Written to answer one question: the persistent profit found in
`RESULT-pumpfun-wallet-persistence-20261001.md` is 97.8% inside the launch transaction — is that
transaction a **shared bundle** an outsider can buy into, or **one operator's own dev-buy**?

## It is one wallet

| wallets in the birth transaction | mints |
|---:|---:|
| **1** | **9,272 (99.81%)** |
| 2 | 2 |
| 3 | 8 |
| 4 | 8 |

Eighteen launches out of 9,290 have more than one buyer in the launch transaction, and the repeated
wallet *sets* top out at 8 and 6 occurrences. There is no bundle marketplace in this data: the seat
is the deployer buying their own mint in the transaction that creates it.

## And deployer access does not concentrate

4,280 distinct wallets hold the seat. 3,021 of them appear exactly once.

| | share of 9,290 launches |
|---|---:|
| top 1 deployer | 1.35% |
| top 10 | 5.60% |
| top 50 | 15.49% |
| top 100 | 22.71% |
| top 500 | 48.79% |

So even a relationship with the single busiest launcher would cover 1.35% of launches. There is no
counterparty to pay for coverage.

## Cross-reference: we already win the only race there is

From the Frankfurt forward work, which is the other half of this answer:

* First later same-mint birth-slot transaction: **p50 13.633 ms**, p95 185.330 ms
  (`RESULT-pumpfun-frankfurt-forward-lifecycle-20260930.md`). The 0.036 ms minimum is the extreme,
  not the norm.
* At local enqueue (p99 **77 us**), **99 of 100 births had zero same-mint competitors ahead**
  (`RESULT-pumpfun-frankfurt-concurrent-reader-dispatch-20260930.md`).
* The edge tolerates one transaction ahead and dies at two
  (`RESULT-pumpfun-same-slot-order-decay-20260929.md`).

**We are already first among reachable followers in 99% of births.** Latency is solved; it is not
what stands between us and the +540.97 SOL.

## Consequence

The gap between the follower seat we can occupy and the launch transaction we cannot is **not a
latency gap and not a network-architecture gap** — it is authorship. The deployer knows the mint
address before it is public; a faster box, a closer region, a Jito bundle, or a different chain
cannot manufacture that.

Do not buy infrastructure, change region, or port to another chain on the theory that the
birth-transaction profit is a race we are losing. It is not a race.
