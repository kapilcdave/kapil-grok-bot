# RESULT — buyer breadth is a CROWDING measure, not a quality measure

Preregistration: `preregistrations/pumpfun_buyer_independence_20261001.md`
Code: `pumpbreadth.py`, self-tests `test_pumpbreadth.py` (4 pass)
Output: `data/pumpbreadth.json`

7,913 mints, frame identical to the closed screen (clip 1 SOL, `L_in=2`, `L_out=2`, trail 15%,
H=300 s, 95 bps). Train windows 0–7, test 8–15. Read-only, $0.

## Verdict: `NO-SELECTION`. G1/G2 pass, G3 and G4 fail.

The hypothesis was that a launch bought by **many independent wallets in small clips** is a better
entry than one bought by a few large fresh wallets — the on-chain shadow of "a real community rather
than a wallet farm", and the backtestable proxy for a social screen.

**Six of the seven features separate, and six of the seven point the WRONG WAY.** Quintile means,
lowest bin to highest:

| feature | predicted | q1 → q5 | observed |
|---|:--:|---|:--:|
| `n_buyers_pre` | **+** | −0.082 → −0.143 | **−** |
| `buyer_hhi_pre` | **−** | −0.149 → −0.082 | **+** |
| `max_buyer_share_pre` | **−** | −0.150 → −0.082 | **+** |
| `dev_share_pre` | **−** | −0.138 → −0.082 | **+** |
| `recurring_buyer_share_pre` | **+** | −0.073 → −0.119 | **−** |
| `recurring_sol_share_pre` | **+** | −0.073 → −0.119 | **−** |
| `median_buy_sol_pre` | **−** | −0.078 → −0.163 | **+** (the only match) |

Read the signs together and they say one thing: **the more independent buyers are already in front
of you, the worse your entry.** A diffuse, experienced, many-wallet book before your entry is not
validation — it is competition. The least-bad cell is the *concentrated, dev-dominated, few-wallet,
small-clip* launch, which is the opposite of the thesis. Every bin of every feature is still
negative, exactly as in the closed 9-feature screen.

## The walk-forward and the null

Best train bin: `n_buyers_pre` bin 0 (exactly one pre-entry buyer), train mean **−5.84%**.
Test mean **−8.95%** on 2,065 mints, against a test pooled mean of **−11.88%**.

So the selection does beat the pooled mean by 2.94 pp — and that is worth nothing, because the
full-procedure shuffle null (permute feature values in train, re-select the best of 35 bins,
re-score test; 500 draws) lands at **p50 −8.946%, p95 −8.937%**. The observed **−8.945%** sits at
roughly the **50th percentile of its own null**. A best-of-35-bins procedure beats the pooled mean by
~3 pp on pure noise; that is what the procedure does, not what the feature knows.

## What this closes

The buyer-independence family, and with it the on-chain proxy for a social/community screen **on
this frame**. Do not reopen it with more concentration statistics, wallet-age proxies, or funding-graph
features: the sign is wrong on six of seven and the magnitude is a null. An actual X-data screen is
not foreclosed by this — it carries off-chain information this test cannot see — but it now has to
beat a measured prior that every on-chain shadow of "real community" is an anti-signal here.

Consistent with the venue being negative-sum: visible interest is the competition, not the customer.
