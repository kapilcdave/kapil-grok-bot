# RESULT — verified creator history is real, but the profitable seat is latency-gated

Date: 2026-10-02  
Preregistration: `preregistrations/pumpfun_deployer_latency_reach_20261002.md`  
Code/output: `pumpdeplat.py`, `data/pumpdeplat_verified.json`

## Verdict: `LATENCY-GATED` and `TOO-SMALL-TO-TRADE`

This full rerun used 8,472 CreateEvent-verified births. At every latency, both
A-period creator scores and B-period outcomes were recomputed at that same
latency; no fast score was applied to a slow fill.

| entry delay | selected B n | selected mean | clustered 95% CI | top−population CI |
|---|---:|---:|---:|---:|
| 0 slots, end of birth slot | 55 | −2.07% | [−8.94%, +12.04%] | [−3.59, +18.56] pp |
| 1 slot / 0.4 s | 70 | −9.39% | [−13.27%, +0.67%] | [−5.76, +7.71] pp |
| 2 slots / 0.8 s | 69 | −11.64% | [−15.99%, −0.30%] | [−8.26, +7.10] pp |
| **5 slots / 2.0 s** | **44** | **−5.20%** | **[−8.62%, +5.02%]** | **[−2.70, +12.26] pp** |
| 12 slots / 4.8 s | 67 | −13.66% | [−20.48%, −11.09%] | [−13.84, −4.35] pp |
| 25 slots / 10 s | 38 | −4.19% | [−11.50%, +5.30%] | [−4.97, +9.85] pp |
| 50 slots / 20 s | 29 | −14.46% | [−18.12%, −11.65%] | [−12.97, −6.94] pp |

At the preregistered five-slot retail floor, the selected mean is negative and
both required CI lower bounds fail. At a 0.5 SOL capacity clip, the selected
row loses **0.258 SOL per B window**, so the magnitude gate also fails.

The contrast is decisive:

- after the verified birth transaction, the creator-history selector earns
  **+31.31%** on B;
- at the end of that same birth slot, the independently reselected group earns
  **−2.07%**;
- one slot later it earns **−9.39%**.

Creator history is informative, but the monetizable return is concentrated in
the transaction-ordering seat immediately behind creation. Under the stated
no-latency directive, this is a capital **NO-GO**. The funding graph remains a
research dataset only and cannot authorize live trading.
